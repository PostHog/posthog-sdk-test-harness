"""Controlled queue engine and HTTP host for harness tests, not an SDK binding."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

import aiohttp
from aiohttp import web

from posthog_test_harness.v2.contracts import (
    MAX_BODY,
    VERSION,
    BoundaryError,
    Contracts,
    decode_json,
    encode_json,
    json_equal,
)
from posthog_test_harness.v2.fixtures import CAPABILITIES

PROFILE = {
    "id": "controlled-flush-v1",
    "runtime": {"family": "server", "name": "controlled-python", "version": "1", "execution_context": "async_local"},
    "identity": "request_scoped",
    "protocol": "legacy",
    "products": ["analytics", "flags"],
    "module": {
        "entry": "tests.v2_flush_host.QueueEngine",
        "format": "native",
        "package": "controlled-double",
        "version": "1",
    },
    "fixture_capabilities": list(CAPABILITIES.values()),
}


class QueueEngine:
    """The queue is the engine's actual storage, not adapter-maintained counters."""

    def __init__(self, storage, clock, host, defect):
        self.records = storage.setdefault("events", [])
        self.clock, self.host, self.defect = clock, host, defect

    async def post(self, path, payload):
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(self.host + path, json=payload) as response:
                await response.read()
                return response.status

    async def setup(self) -> None:
        # Real startup traffic in the double ensures the flush-only window does not
        # accidentally depend on initialization being silent.
        await self.post("/flags", {"api_key": "fixture-token"})

    def capture(self, args) -> None:
        if self.defect != "missing_capture":
            self.records.append({"record_id": str(uuid4()), "event": {"timestamp": self.clock, **deepcopy(args)}})

    async def flush(self) -> None:
        if self.defect == "timeout":
            await asyncio.Event().wait()
        if self.defect == "thrown":
            raise RuntimeError("Controlled native failure")
        if not self.records:
            if self.defect == "empty_network":
                await self.post("/unknown", {})
        elif self.defect != "no_delivery":
            records = list(self.records)
            events = [r["event"] for r in records]
            if self.defect == "missing_event":
                events = events[:1]
            status = await self.post("/batch", {"batch": events})
            if 200 <= status < 300:
                if self.defect != "keep_delivered":
                    self.records.clear()
            elif self.defect == "lose_retry":
                self.records.clear()
            elif self.defect == "replace_retry":
                self.records[:] = [{**r, "record_id": str(uuid4())} for r in records]
        if self.defect == "incorrect_result":
            return False


@dataclass
class HostFixture:
    case_id: str
    receiver: dict
    defect: str | None
    references: dict
    clock: str | None = None
    storage: dict = field(default_factory=dict)
    manual: bool = False
    storage_prepared: bool = False
    engine: QueueEngine | None = None
    task: asyncio.Task | None = None
    closed: bool = False
    expired: bool = False
    observations: list = field(default_factory=list)
    retained: dict = field(default_factory=dict)


class Host:
    def __init__(self, contracts, defect=None, defect_case=0, missing_capability=None, missing_route=None):
        self.contracts, self.defect, self.defect_case = contracts, defect, defect_case
        self.profile = deepcopy(PROFILE)
        if missing_capability:
            self.profile["fixture_capabilities"].remove(missing_capability)
        self.routes = [r for r in ("/setup", "/capture", "/flush") if r != missing_route]
        self.fixtures, self.inputs, self.controls, self.closed, self.paths = {}, [], [], [], []
        self.call_ids = set()
        self.session = str(uuid4())
        self.timeouts = 0
        self.extensions = {}

    async def handle(self, request):
        self.paths.append(request.path)
        try:
            data = decode_json(await request.read())
            path = request.path.removeprefix("/v2/")
            if path == "negotiate":
                self.contracts.validate("NegotiateRequest", data)
                expected = {
                    "contract_version": VERSION,
                    "catalog_sha256": self.contracts.catalog_hash,
                    "transport": "http-json-v2",
                }
                if self.defect == "rejected" or not json_equal(data, expected):
                    return self.response(
                        "NegotiateResponse",
                        {
                            "kind": "rejected",
                            "code": (
                                "catalog_mismatch"
                                if data["catalog_sha256"] != self.contracts.catalog_hash
                                else "incompatible_version"
                            ),
                            "message": "Incompatible host",
                        },
                    )
                return self.response(
                    "NegotiateResponse",
                    {
                        "kind": "accepted",
                        **expected,
                        "session_id": self.session,
                        "adapter": {"name": "controlled-flush-double", "version": "1"},
                        "profiles": [self.profile],
                        "supported_routes": self.routes,
                        "max_timeout_ms": 300000,
                    },
                )
            if request.headers.get("Authorization") != "Bearer " + self.session:
                return web.Response(status=401)
            names = {
                "fixtures/allocate": "Allocate",
                "fixtures/flush": "FlushFixture",
                "invoke": "Invoke",
                "fixtures/close": "Close",
                "fixtures/observations": "Observations",
            }
            names.update({path: schema for path, (schema, _) in self.extensions.items()})
            if path not in names:
                return web.Response(status=404)
            self.contracts.validate(names[path] + "Request", data)
            fixture_id = data["fixture_id"]
            if path == "fixtures/allocate":
                if fixture_id in self.fixtures or data["profile_id"] != self.profile["id"]:
                    return web.Response(status=409)
                receiver = {"kind": "instance", "id": fixture_id + "/receiver"}
                defect = self.defect if len(self.fixtures) == self.defect_case else None
                self.fixtures[fixture_id] = HostFixture(data["case_id"], receiver, defect, {receiver["id"]: "instance"})
                return self.response(
                    "AllocateResponse", {"kind": "allocated", "fixture_id": fixture_id, "receiver": receiver}
                )
            if fixture_id not in self.fixtures:
                return web.Response(status=404)
            fixture = self.fixtures[fixture_id]
            if path == "fixtures/close":
                if fixture.task and not fixture.task.done():
                    fixture.task.cancel()
                    await asyncio.gather(fixture.task, return_exceptions=True)
                fixture.closed = True
                fixture.references.clear()
                fixture.retained.clear()
                fixture.engine = None
                fixture.storage.clear()
                if fixture_id not in self.closed:
                    self.closed.append(fixture_id)
                if fixture.defect == "teardown":
                    return web.Response(status=503)
                return self.response("CloseResponse", {"kind": "closed", "fixture_id": fixture_id})
            if path == "fixtures/observations":
                return self.response(
                    "ObservationsResponse",
                    {
                        "fixture_id": fixture_id,
                        "cursor": len(fixture.observations),
                        "observations": fixture.observations[data["after_sequence"] :],
                    },
                )
            if path == "cancel" and path in self.extensions:
                return await self.extensions[path][1](fixture_id, fixture, data)
            if fixture.closed or fixture.expired or (fixture.task and not fixture.task.done()):
                return web.Response(status=409)
            if path == "fixtures/flush":
                return self.control(fixture_id, fixture, data)
            if path in self.extensions:
                return await self.extensions[path][1](fixture_id, fixture, data)
            call = data["invoke"]
            self.contracts.validate_invoke(call, fixture.references)
            if call["call_id"] in self.call_ids:
                return web.Response(status=409)
            self.call_ids.add(call["call_id"])
            self.inputs.append(deepcopy(data))
            if fixture.defect == "http" and call["route"] == "/flush":
                return web.Response(status=503)
            fixture.task = asyncio.create_task(self.invoke(fixture, call))
            try:
                result = await asyncio.wait_for(fixture.task, timeout=data["timeout_ms"] / 1000)
                outcome = self.outcome(call, result)
                completion = {"kind": "sdk", "outcome": outcome}
            except TimeoutError:
                self.timeouts += 1
                fixture.expired = True
                fixture.references.clear()
                completion = {
                    "kind": "harness",
                    "failure": {
                        "kind": "timeout",
                        "code": "host_deadline",
                        "message": "Controlled native operation timed out",
                    },
                }
            except Exception as error:
                identity = str(uuid4())
                fixture.retained[identity] = error
                fixture.references[identity] = "exception"
                completion = {
                    "kind": "sdk",
                    "outcome": {"kind": "thrown", "error": {"kind": "exception", "id": identity}},
                }
            receipt = {
                "fixture_id": fixture_id,
                "call_id": call["call_id"],
                "route": call["route"],
                "completion": completion,
            }
            fixture.observations.append(
                {"kind": "call", "sequence": len(fixture.observations) + 1, "receipt": deepcopy(receipt)}
            )
            return self.response("InvokeResponse", {"receipt": receipt})
        except BoundaryError:
            return web.Response(status=400)

    def outcome(self, call, result):
        return {"kind": "void"} if result is None else {"kind": "value", "value": result}

    async def invoke(self, fixture, call):
        args = call["args"]
        if call["route"] == "/setup":
            assert fixture.manual and fixture.clock is not None and fixture.storage_prepared and fixture.engine is None
            assert set(args) == {"project_token", "config"} and set(args["config"]) == {"host"}
            fixture.engine = QueueEngine(fixture.storage, fixture.clock, args["config"]["host"], fixture.defect)
            return await fixture.engine.setup()
        assert fixture.engine is not None
        if call["route"] == "/capture":
            return fixture.engine.capture(args)
        assert call["route"] == "/flush" and args == {}
        return await fixture.engine.flush()

    def control(self, fixture_id, fixture, data):
        self.controls.append(deepcopy(data))
        command = data["command"]
        kind = command["kind"]
        base = {"fixture_id": fixture_id, "command": kind}
        if CAPABILITIES[kind] not in self.profile["fixture_capabilities"] or (
            fixture.defect == "blocked" and kind == "queue_snapshot"
        ):
            return self.response(
                "FlushFixtureResponse",
                {
                    **base,
                    "kind": "failed",
                    "failure": {
                        "kind": "blocked_fixture",
                        "code": "component_unavailable",
                        "message": "Queue component observation unavailable",
                    },
                },
            )
        if kind == "queue_snapshot":
            assert fixture.engine is not None and fixture.manual
            observation = {
                "layer": "native_component",
                "implementation": "tests.v2_flush_host.QueueEngine.records",
                "records": deepcopy(fixture.engine.records),
            }
            if fixture.defect == "wrong_fixture":
                base["fixture_id"] = "other-fixture"
            return self.response("FlushFixtureResponse", {**base, "kind": "queue", "observation": observation})
        assert fixture.engine is None
        if kind == "clock_fixed":
            fixture.clock = command["timestamp"]
        elif kind == "storage_empty":
            fixture.storage.clear()
            fixture.storage_prepared = True
        else:
            fixture.manual = True
        return self.response("FlushFixtureResponse", {**base, "kind": "applied"})

    def response(self, schema, body):
        self.contracts.validate(schema, body)
        return web.Response(body=encode_json(body), content_type="application/json")


@asynccontextmanager
async def serve(contracts, *, host_type=Host, **options):
    host = host_type(contracts, **options)
    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_post("/v2/{tail:.*}", host.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield host, url
    finally:
        for fixture in host.fixtures.values():
            if fixture.task and not fixture.task.done():
                fixture.task.cancel()
                await asyncio.gather(fixture.task, return_exceptions=True)
        await runner.cleanup()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", required=True)
    parser.add_argument("--defect")
    args = parser.parse_args()
    async with serve(Contracts(args.contracts), defect=args.defect) as (_, url):
        print(url, flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
