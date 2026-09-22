"""Bounded HTTP calls to isolated SDK receivers using the draft2 interface."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from urllib.parse import urlsplit

import aiohttp

from .contracts import MAX_BODY, VERSION, BoundaryError, decode_json, encode_json, require
from .network import validate_host


class Client:
    def __init__(self, base_url, contracts, *, allow_private_network=False):
        try:
            parsed = urlsplit(base_url)
            validate_host(parsed.hostname)
            port = parsed.port
        except ValueError as error:
            raise BoundaryError("invalid_transport", "Expected HTTP host and explicit port") from error
        require(
            parsed.scheme == "http"
            and (parsed.hostname == "127.0.0.1" or allow_private_network)
            and port
            and parsed.username is None
            and parsed.password is None
            and parsed.path in ("", "/")
            and "?" not in base_url
            and "#" not in base_url
            and not any(ord(char) <= 32 or ord(char) == 127 for char in base_url),
            "invalid_transport",
            "Expected HTTP host and explicit port; non-loopback hosts require --allow-private-network",
        )
        self.base_url, self.contracts = base_url.rstrip("/"), contracts
        self.session = None
        self.negotiation = None
        self.fixtures, self.calls, self.pending, self.errors = {}, {}, {}, []
        self.used_call_ids = set()

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(auto_decompress=False, trust_env=False)
        try:
            await self.negotiate()
        except BaseException:
            await self.session.close()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            for fixture in self.fixtures.values():
                if not fixture.closed:
                    try:
                        await fixture.close()
                    except BoundaryError as error:
                        # Attempt every close, but never suppress a teardown failure.
                        if error not in self.errors:
                            self.errors.append(error)
        finally:
            await self.session.close()
        if self.errors and exc is None:
            raise self.errors[0]

    async def _post(self, path, request_name, response_name, data, timeout_ms=5000):
        self.contracts.validate(request_name, data)
        payload = encode_json(data)
        require(len(payload) <= MAX_BODY, "body_limit", "Request exceeds protocol body limit")
        headers = {"Content-Type": "application/json"}
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                async with self.session.post(
                    self.base_url + "/v2/" + path,
                    data=payload,
                    headers=headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=None),
                ) as response:
                    require(
                        response.content_type == "application/json" and not response.headers.get("Content-Encoding"),
                        "invalid_response",
                        "Expected uncompressed JSON response",
                    )
                    chunks, length = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        length += len(chunk)
                        require(length <= MAX_BODY, "body_limit", "Response exceeds protocol body limit")
                        chunks.append(chunk)
                    result = decode_json(b"".join(chunks))
                    if response.status != 200:
                        detail = result.get("error") if isinstance(result, dict) else None
                        if isinstance(detail, dict):
                            detail = detail.get("message")
                        suffix = f": {detail[:500]}" if isinstance(detail, str) else ""
                        raise BoundaryError("http_error", f"Host returned HTTP {response.status}{suffix}")
                    self.contracts.validate(response_name, result)
                    return result
        except TimeoutError as error:
            raise BoundaryError("transport_timeout", "Host response deadline elapsed", "timeout") from error
        except aiohttp.ClientError as error:
            raise BoundaryError("transport_error", "Host connection failed") from error

    async def negotiate(self):
        require(self.negotiation is None, "invalid_state", "Session already negotiated")
        result = await self._post(
            "negotiate",
            "NegotiateRequest",
            "NegotiateResponse",
            {"protocol": VERSION},
        )
        self.negotiation = result
        return deepcopy(result)

    def deadline(self, timeout_ms):
        require(self.negotiation is not None, "not_negotiated", "Negotiate before fixture use")
        self.contracts.validate("DeadlineMs", timeout_ms)
        require(
            timeout_ms <= self.negotiation["max_timeout_ms"], "invalid_deadline", "Deadline exceeds negotiated bound"
        )

    async def allocate(self, fixture_id, case_id, profile_id, timeout_ms=5000):
        self.deadline(timeout_ms)
        require(fixture_id not in self.fixtures, "duplicate_id", "Fixture IDs cannot be reused")
        require(profile_id in {p["id"] for p in self.negotiation["profiles"]}, "unknown_profile", "Unknown profile ID")
        self.contracts.validate(
            "AllocateRequest",
            {"fixture_id": fixture_id, "case_id": case_id, "profile_id": profile_id, "timeout_ms": timeout_ms},
        )
        # Reserve before sending: a lost response must never authorize another allocation.
        fixture = Fixture(self, fixture_id, case_id, profile_id)
        self.fixtures[fixture_id] = fixture
        try:
            result = await self._post(
                "fixtures/allocate",
                "AllocateRequest",
                "AllocateResponse",
                {"fixture_id": fixture_id, "case_id": case_id, "profile_id": profile_id, "timeout_ms": timeout_ms},
                timeout_ms + 1000,
            )
            require(result["fixture_id"] == fixture_id, "invalid_response", "Wrong allocated fixture")
            fixture.active = True
            return fixture
        except BoundaryError as error:
            self.errors.append(error)
            raise

    @asynccontextmanager
    async def fixture(self, *args, **kwargs):
        fixture = await self.allocate(*args, **kwargs)
        try:
            yield fixture
        finally:
            await fixture.close()


class Fixture:
    def __init__(self, client, fixture_id, case_id, profile_id):
        self.client, self.id, self.case_id, self.profile_id = client, fixture_id, case_id, profile_id
        self.active, self.closed, self.busy = False, False, False

    def check_active(self):
        require(self.active and not self.closed, "invalid_state", "Fixture is not live")

    def invalidate(self):
        self.active = False

    async def invoke(self, call_id, route, args, *, receiver=None, references=None, timeout_ms=5000):
        self.check_active()
        self.client.deadline(timeout_ms)
        require(not self.busy, "invalid_state", "Fixture already has a pending invocation")
        require(call_id not in self.client.used_call_ids, "duplicate_id", "Call ID already used")
        if receiver is not None or references is not None:
            raise BoundaryError(
                "references_unavailable", "Object references have no draft2 binding", "unsupported_binding"
            )
        require(isinstance(args, dict), "invalid_envelope", "Expected argument object")
        self.client.used_call_ids.add(call_id)
        self.busy = True
        receipt = {"fixture_id": self.id, "call_id": call_id, "route": route}
        self.client.pending[call_id] = receipt
        try:
            if route not in self.client.negotiation["supported_routes"]:
                raise BoundaryError("missing_operation", "Applicable operation has no binding", "unsupported_binding")
            result = await self.client._post(
                "invoke",
                "InvokeRequest",
                "InvokeResponse",
                {"fixture_id": self.id, "call_id": call_id, "route": route, "args": args, "timeout_ms": timeout_ms},
                timeout_ms + 1000,
            )
            require(
                result["fixture_id"] == self.id and result["call_id"] == call_id,
                "invalid_response",
                "Wrong invocation attribution",
            )
            receipt["completion"] = result["completion"]
        except BoundaryError as error:
            receipt["completion"] = {"kind": "harness", "failure": error.failure()}
        except asyncio.CancelledError:
            self.invalidate()
            receipt["completion"] = {
                "kind": "harness",
                "failure": {"kind": "cancelled", "code": "caller_cancelled", "message": "Runner task cancelled"},
            }
            raise
        finally:
            self.client.pending.pop(call_id, None)
            self.busy = False
            if "completion" in receipt:
                self.client.calls[call_id] = deepcopy(receipt)
                if receipt["completion"]["kind"] == "harness":
                    self.invalidate()
        return deepcopy(receipt)

    async def close(self, timeout_ms=None):
        if self.closed:
            return
        timeout_ms = timeout_ms or min(5000, self.client.negotiation["max_timeout_ms"])
        self.client.deadline(timeout_ms)
        self.invalidate()
        # A failed close is not retried implicitly; the failure remains a run error.
        self.closed = True
        try:
            result = await self.client._post(
                "fixtures/close",
                "CloseRequest",
                "CloseResponse",
                {"fixture_id": self.id, "timeout_ms": timeout_ms},
                timeout_ms + 1000,
            )
            require(result["fixture_id"] == self.id, "teardown_failed", "Wrong closed fixture")
        except BoundaryError as error:
            self.client.errors.append(error)
            raise
