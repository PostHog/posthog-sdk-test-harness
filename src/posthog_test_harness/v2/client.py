"""Core v2 HTTP client. Native execution and callback continuations stay in the host."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from urllib.parse import quote, urlsplit

import aiohttp

from .contracts import MAX_BODY, VERSION, BoundaryError, decode_json, encode_json, json_equal, require
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
        if path != "negotiate":
            require(self.negotiation is not None, "not_negotiated", "Negotiate before fixture use")
            headers["Authorization"] = "Bearer " + self.negotiation["session_id"]
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                async with self.session.post(
                    self.base_url + "/v2/" + path,
                    data=payload,
                    headers=headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=None),
                ) as response:
                    require(response.status == 200, "http_error", f"Host returned HTTP {response.status}")
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
                    if (
                        response_name == "NegotiateResponse"
                        and isinstance(result, dict)
                        and result.get("kind") == "accepted"
                    ):
                        require(
                            result.get("catalog_sha256") == self.contracts.catalog_hash,
                            "catalog_mismatch",
                            "Adapter accepted a different catalog identity",
                        )
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
            {"contract_version": VERSION, "catalog_sha256": self.contracts.catalog_hash, "transport": "http-json-v2"},
        )
        if result["kind"] == "rejected":
            code = "catalog_mismatch" if result["code"] == "catalog_mismatch" else "incompatible_adapter"
            raise BoundaryError(code, "Adapter rejected the selected contract")
        # Constants in the named response schema pin the exact requested version/hash/transport.
        require(
            len({p["id"] for p in result["profiles"]}) == len(result["profiles"]),
            "invalid_response",
            "Duplicate profile ID",
        )
        require(
            len(set(result["supported_routes"])) == len(result["supported_routes"]),
            "invalid_response",
            "Duplicate supported route",
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
            if result["kind"] == "failed":
                raise BoundaryError(result["failure"]["code"], result["failure"]["message"], result["failure"]["kind"])
            fixture.receiver = result["receiver"]
            fixture.add_reference(fixture.receiver)
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
        self.receiver = None
        self.active, self.closed, self.busy = False, False, False
        self.references, self.plans = {}, {}
        self.reference_ids = set()
        self.cursor = 0
        self.observations = []
        self.cancelled_calls = set()
        self.observed_calls, self.observed_callbacks = set(), set()

    @property
    def contracts(self):
        return self.client.contracts

    def check_active(self):
        require(self.active and not self.closed, "invalid_state", "Fixture is not live")

    def invalidate(self):
        self.active = False
        self.references.clear()

    def add_reference(self, reference):
        self.contracts.validate("Reference", reference)
        previous = self.references.get(reference["id"])
        require(previous is None or previous == reference["kind"], "invalid_reference", "Reference kind changed")
        self.references[reference["id"]] = reference["kind"]

    async def reference(self, reference_id, fixture):
        self.check_active()
        require(
            reference_id not in self.reference_ids and reference_id not in self.references,
            "duplicate_id",
            "Reference ID already used",
        )
        if fixture.get("kind") == "callback":
            self.contracts.validate_plan(fixture["plan"], self.references)
        request = {"fixture_id": self.id, "reference_id": reference_id, "fixture": fixture}
        self.contracts.validate("ReferenceRequest", request)
        self.reference_ids.add(reference_id)
        try:
            result = await self.client._post("fixtures/references", "ReferenceRequest", "ReferenceResponse", request)
            require(result["fixture_id"] == self.id, "invalid_response", "Wrong reference fixture")
            if result["kind"] == "failed":
                raise BoundaryError(result["failure"]["code"], result["failure"]["message"], result["failure"]["kind"])
            reference = result["reference"]
            require(
                reference["id"] == reference_id and reference["kind"] == fixture["kind"],
                "invalid_reference",
                "Wrong installed reference",
            )
            self.add_reference(reference)
            if fixture["kind"] == "callback":
                self.plans[reference_id] = deepcopy(fixture["plan"])
            return deepcopy(reference)
        except BoundaryError as error:
            self.invalidate()
            self.client.errors.append(error)
            raise

    def _record(self, receipt):
        self.contracts.validate("CallReceipt", receipt)
        require(receipt["fixture_id"] == self.id, "invalid_response", "Wrong receipt fixture")
        call_id = receipt["call_id"]
        previous = self.client.calls.get(call_id)
        require(previous is None or json_equal(previous, receipt), "invalid_response", "Conflicting call receipt")
        completion = receipt["completion"]
        if call_id in self.cancelled_calls:
            require(
                completion["kind"] == "harness" and completion["failure"]["kind"] in ("cancelled", "harness_error"),
                "invalid_response",
                "Native completion arrived after cancellation won",
            )
        if completion["kind"] == "sdk" and self.active:
            outcome = completion["outcome"]
            for key in ("retained", "error"):
                if key in outcome:
                    self.add_reference(outcome[key])
            value = outcome.get("value")
            kinds = self.contracts.operations[receipt["route"]]["result_reference_kinds"]
            if kinds and isinstance(value, dict) and value.get("kind") in kinds:
                self.add_reference(value)
                require(
                    "retained" not in outcome or json_equal(outcome["retained"], value),
                    "invalid_response",
                    "Conflicting retained result identity",
                )
        elif completion["kind"] == "harness" and completion["failure"]["kind"] in (
            "timeout",
            "cancelled",
            "harness_error",
        ):
            self.invalidate()
        self.client.calls[call_id] = deepcopy(receipt)

    async def invoke(self, call_id, route, args, *, receiver=None, references=None, timeout_ms=5000):
        self.check_active()
        self.client.deadline(timeout_ms)
        require(not self.busy, "invalid_state", "Fixture already has a pending invocation")
        require(
            call_id not in self.client.used_call_ids,
            "duplicate_id",
            "Call ID already used",
        )
        invoke = {
            "call_id": call_id,
            "route": route,
            "receiver": receiver if receiver is not None else self.receiver,
            "args": deepcopy(args),
        }
        if references is not None:
            invoke["references"] = deepcopy(references)
        self.contracts.validate_invoke(invoke, self.references)
        self.client.used_call_ids.add(call_id)
        self.client.pending[call_id] = {"fixture_id": self.id, "route": route}  # Reserve before network I/O.
        self.busy = True
        try:
            if route not in self.client.negotiation["supported_routes"]:
                raise BoundaryError("missing_operation", "Applicable operation has no binding", "unsupported_binding")
            response = await self.client._post(
                "invoke",
                "InvokeRequest",
                "InvokeResponse",
                {"fixture_id": self.id, "timeout_ms": timeout_ms, "invoke": invoke},
                timeout_ms + 1000,
            )
            receipt = response["receipt"]
            require(
                receipt["call_id"] == call_id
                and receipt["route"] == route
                and "parent_call_id" not in receipt
                and "callback_invocation_id" not in receipt,
                "invalid_response",
                "Wrong top-level call attribution",
            )
            self._record(receipt)
        except BoundaryError as error:
            receipt = {
                "fixture_id": self.id,
                "call_id": call_id,
                "route": route,
                "completion": {"kind": "harness", "failure": error.failure()},
            }
            if call_id in self.client.calls:
                # Preserve an already observed terminal result. The contradictory or
                # lost HTTP reply is a run error, not a replacement native outcome.
                self.invalidate()
                self.client.errors.append(error)
                receipt = self.client.calls[call_id]
            else:
                self._record(receipt)
        except asyncio.CancelledError:
            self.invalidate()
            receipt = {
                "fixture_id": self.id,
                "call_id": call_id,
                "route": route,
                "completion": {
                    "kind": "harness",
                    "failure": {"kind": "cancelled", "code": "caller_cancelled", "message": "Runner task cancelled"},
                },
            }
            if call_id in self.client.calls:
                self.client.errors.append(BoundaryError("caller_cancelled", "Runner task cancelled", "cancelled"))
            else:
                self.client.calls[call_id] = receipt
            raise
        finally:
            self.client.pending.pop(call_id, None)
            self.busy = False
        return deepcopy(receipt)

    async def cancel(self, call_id, reason="runner cancellation"):
        call = self.client.calls.get(call_id) or self.client.pending.get(call_id)
        require(
            call and call["fixture_id"] == self.id and not self.closed,
            "invalid_state",
            "Unknown, cross-fixture or closed call",
        )
        try:
            response = await self.client._post(
                "cancel",
                "CancelRequest",
                "CancelResponse",
                {"fixture_id": self.id, "call_id": call_id, "reason": reason},
            )
            require(
                response["fixture_id"] == self.id and response["call_id"] == call_id,
                "invalid_response",
                "Wrong cancellation attribution",
            )
            if response["state"] == "cancelled":
                existing = self.client.calls.get(call_id)
                require(
                    existing is None
                    or (
                        existing["completion"]["kind"] == "harness"
                        and existing["completion"]["failure"]["kind"] == "cancelled"
                    ),
                    "invalid_response",
                    "Cancellation contradicts terminal completion",
                )
                self.cancelled_calls.add(call_id)
                self.invalidate()
            return response
        except BoundaryError as error:
            self.invalidate()
            self.client.errors.append(error)
            raise

    async def observe(self):
        # Diagnostics survive close; observing them must not revive runtime references.
        try:
            result = await self.client._post(
                "fixtures/observations",
                "ObservationsRequest",
                "ObservationsResponse",
                {"fixture_id": self.id, "after_sequence": self.cursor},
            )
            require(result["fixture_id"] == self.id, "invalid_response", "Wrong observation fixture")
            expected = self.cursor
            for observation in result["observations"]:
                expected += 1
                require(observation["sequence"] == expected, "invalid_response", "Observation sequence gap")
                if observation["kind"] == "call":
                    receipt = observation["receipt"]
                    require(
                        receipt["call_id"] not in self.observed_calls, "invalid_response", "Duplicate call observation"
                    )
                    self.observed_calls.add(receipt["call_id"])
                    if receipt["call_id"].startswith("@callback/"):
                        self._nested(receipt)
                    else:
                        previous = self.client.calls.get(receipt["call_id"])
                        pending = self.client.pending.get(receipt["call_id"])
                        require(
                            (
                                json_equal(previous, receipt)
                                if previous is not None
                                else pending is not None
                                and pending["fixture_id"] == receipt["fixture_id"]
                                and pending["route"] == receipt["route"]
                                and "parent_call_id" not in receipt
                                and "callback_invocation_id" not in receipt
                            ),
                            "invalid_response",
                            "Unknown or conflicting observed call",
                        )
                    self._record(receipt)
                else:
                    require(observation["fixture_id"] == self.id, "invalid_response", "Wrong callback fixture")
                    ref = observation["callback"]
                    require(
                        ref["kind"] == "callback" and ref["id"] in self.plans,
                        "invalid_reference",
                        "Unknown observed callback",
                    )
                    prefix = self._callback_id(ref["id"], observation["invocation_index"])
                    require(observation["invocation_id"] == prefix, "invalid_response", "Wrong callback invocation ID")
                    require(prefix not in self.observed_callbacks, "invalid_response", "Duplicate callback observation")
                    self.observed_callbacks.add(prefix)
                    plan = self.plans[ref["id"]]
                    require(
                        observation["invocation_index"] < plan["max_invocations"],
                        "invalid_response",
                        "Callback invocation limit exceeded",
                    )
                    expected_ids = [prefix + "/" + quote(step["step_id"], safe="-._~") for step in plan["calls"]]
                    actual_ids = observation["call_ids"]
                    require(
                        actual_ids == expected_ids[: len(actual_ids)], "invalid_response", "Wrong continuation calls"
                    )
                    if observation["completion"]["kind"] == "sdk":
                        require(actual_ids == expected_ids, "invalid_response", "Incomplete successful continuation")
                    owner = observation["owner_call_id"]
                    if owner is not None:
                        receipt = self.client.calls.get(owner) or self.client.pending.get(owner)
                        require(
                            receipt and receipt["fixture_id"] == self.id, "invalid_response", "Wrong callback owner"
                        )
                    for argument in observation["args"]:
                        if self.active:
                            for key in ("retained", "error"):
                                if key in argument:
                                    self.add_reference(argument[key])
                    for call_id in observation["call_ids"]:
                        receipt = self.client.calls.get(call_id)
                        require(
                            receipt
                            and receipt.get("callback_invocation_id") == prefix
                            and receipt.get("parent_call_id") == owner,
                            "invalid_response",
                            "Missing callback call receipt",
                        )
                    if observation["completion"]["kind"] == "harness":
                        failure = observation["completion"]["failure"]
                        self.client.errors.append(BoundaryError(failure["code"], failure["message"], failure["kind"]))
                self.observations.append(deepcopy(observation))
            require(result["cursor"] == expected, "invalid_response", "Wrong observation cursor")
            self.cursor = expected
            return deepcopy(result["observations"])
        except BoundaryError as error:
            self.invalidate()
            self.client.errors.append(error)
            raise

    def _callback_id(self, ref_id, index):
        return f"@callback/{quote(self.id, safe='-._~')}/{quote(ref_id, safe='-._~')}/{index}"

    def _nested(self, receipt):
        for ref_id, plan in self.plans.items():
            for index in range(plan["max_invocations"]):
                prefix = self._callback_id(ref_id, index)
                if receipt.get("callback_invocation_id") != prefix:
                    continue
                for step in plan["calls"]:
                    if receipt["call_id"] == prefix + "/" + quote(step["step_id"], safe="-._~"):
                        require(receipt["route"] == step["route"], "invalid_response", "Wrong continuation route")
                        parent = receipt.get("parent_call_id")
                        if parent:
                            owner = self.client.calls.get(parent) or self.client.pending.get(parent)
                            require(
                                owner and owner["fixture_id"] == self.id, "invalid_response", "Wrong continuation owner"
                            )
                        return
        raise BoundaryError("invalid_response", "Unregistered continuation receipt")

    async def context_scope(self, scope_id, context, calls, timeout_ms=5000):
        """Enter/call/exit in one host continuation, never across HTTP handlers."""
        self.check_active()
        self.client.deadline(timeout_ms)
        require(not self.busy, "invalid_state", "Fixture already has a pending invocation")
        request = {
            "fixture_id": self.id,
            "scope_id": scope_id,
            "context": context,
            "calls": deepcopy(calls),
            "timeout_ms": timeout_ms,
        }
        self.contracts.validate("ContextScopeRequest", request)
        self.contracts.live_reference(context, self.references)
        ids = [call["call_id"] for call in calls]
        require(
            len(set(ids)) == len(ids) and not set(ids) & self.client.used_call_ids,
            "duplicate_id",
            "Context continuation call ID already used",
        )
        for call in calls:
            self.contracts.validate_invoke(call, self.references)
        self.client.used_call_ids.update(ids)
        self.client.pending.update({call["call_id"]: {"fixture_id": self.id, "route": call["route"]} for call in calls})
        self.busy = True
        try:
            response = await self.client._post(
                "fixtures/context-scope", "ContextScopeRequest", "ContextScopeResponse", request, timeout_ms + 1000
            )
            require(
                response["fixture_id"] == self.id and response["scope_id"] == scope_id,
                "invalid_response",
                "Wrong context scope attribution",
            )
            receipts = response["calls"]
            require(len(receipts) <= len(calls), "invalid_response", "Extra context scope call")
            for expected, receipt in zip(calls, receipts):
                require(
                    receipt["call_id"] == expected["call_id"]
                    and receipt["route"] == expected["route"]
                    and "parent_call_id" not in receipt
                    and "callback_invocation_id" not in receipt,
                    "invalid_response",
                    "Wrong context call attribution",
                )
                self._record(receipt)
            if response["result"]["kind"] == "failed":
                failure = response["result"]["failure"]
                raise BoundaryError(failure["code"], failure["message"], failure["kind"])
            require(len(receipts) == len(calls), "invalid_response", "Missing context scope call")
            return deepcopy(response)
        except BoundaryError as error:
            self.invalidate()
            self.client.errors.append(error)
            raise
        except asyncio.CancelledError:
            self.invalidate()
            self.client.errors.append(BoundaryError("caller_cancelled", "Context continuation cancelled", "cancelled"))
            raise
        finally:
            for identity in ids:
                self.client.pending.pop(identity, None)
            self.busy = False

    async def close(self, timeout_ms=None):
        if self.closed:
            return
        if timeout_ms is None:
            timeout_ms = min(5000, self.client.negotiation["max_timeout_ms"])
        self.client.deadline(timeout_ms)
        self.invalidate()
        try:
            result = await self.client._post(
                "fixtures/close",
                "CloseRequest",
                "CloseResponse",
                {"fixture_id": self.id, "timeout_ms": timeout_ms},
                timeout_ms + 1000,
            )
            require(
                result["kind"] == "closed" and result["fixture_id"] == self.id,
                "teardown_failed",
                "Host failed fixture teardown",
            )
            self.closed = True
            await self.observe()
        except BoundaryError as error:
            self.client.errors.append(error)
            raise
