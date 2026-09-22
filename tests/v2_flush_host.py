"""Generic controlled HTTP adapter for harness tests, not SDK conformance."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

import aiohttp
from aiohttp import web

from posthog_test_harness.v2.contracts import MAX_BODY, VERSION, BoundaryError, decode_json

PROFILE = {
    "id": "controlled",
    "sdk_type": "server",
    "sdk_capabilities": [],
    "fixture_capabilities": ["storage.empty.v1"],
    "runtime": {"family": "server"},
    "protocol": "legacy",
    "module": {},
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
    defect: str | None
    storage: dict = field(default_factory=dict)
    storage_prepared: bool = True
    engine: object = None
    task: asyncio.Task | None = None
    closed: bool = False


class Host:
    def __init__(self, contracts, defect=None, defect_case=0, missing_capability=None, missing_route=None):
        self.contracts, self.defect, self.defect_case = contracts, defect, defect_case
        self.profile = deepcopy(PROFILE)
        if missing_capability:
            self.profile["fixture_capabilities"].remove(missing_capability)
        self.routes = [r for r in ("/setup", "/capture", "/flush") if r != missing_route]
        self.fixtures, self.inputs, self.closed, self.paths = {}, [], [], []
        self.call_ids = set()
        self.timeouts = 0

    async def handle(self, request):
        self.paths.append(request.path)
        data = decode_json(await request.read())
        path = request.path.removeprefix("/v2/")
        if path == "negotiate":
            if self.defect == "rejected" or data != {"protocol": VERSION}:
                return web.json_response({"error": "protocol"}, status=400)
            return web.json_response(
                {
                    "protocol": VERSION,
                    "profiles": [self.profile],
                    "supported_routes": self.routes,
                    "max_timeout_ms": 60000,
                }
            )
        identity = data["fixture_id"]
        if path == "fixtures/allocate":
            if identity in self.fixtures or data["profile_id"] != self.profile["id"]:
                return web.json_response({"error": "duplicate"}, status=409)
            defect = self.defect if len(self.fixtures) == self.defect_case else None
            self.fixtures[identity] = HostFixture(data["case_id"], defect)
            return web.json_response({"fixture_id": identity})
        fixture = self.fixtures.get(identity)
        if fixture is None:
            return web.json_response({"error": "missing"}, status=404)
        if path == "fixtures/close":
            if fixture.task and not fixture.task.done():
                fixture.task.cancel()
                await asyncio.gather(fixture.task, return_exceptions=True)
            fixture.closed = True
            self.closed.append(identity)
            if fixture.defect == "teardown":
                return web.json_response({"error": "teardown"}, status=503)
            return web.json_response({"fixture_id": identity})
        if path != "invoke" or fixture.closed or data["call_id"] in self.call_ids:
            return web.json_response({"error": "invalid"}, status=409)
        self.call_ids.add(data["call_id"])
        self.inputs.append(deepcopy(data))
        if fixture.defect == "http" and data["route"] == "/flush":
            return web.json_response({"error": "http"}, status=503)
        fixture.task = asyncio.create_task(self.invoke(fixture, data))
        try:
            result = await asyncio.wait_for(fixture.task, data["timeout_ms"] / 1000)
            completion = {"kind": "sdk", "outcome": self.outcome(data, result)}
        except TimeoutError:
            self.timeouts += 1
            completion = {
                "kind": "harness",
                "failure": {"kind": "timeout", "code": "host_deadline", "message": "Controlled operation timed out"},
            }
        except BoundaryError as error:
            completion = {"kind": "harness", "failure": error.failure()}
        except Exception as error:
            completion = {
                "kind": "sdk",
                "outcome": {"kind": "thrown", "error": {"name": type(error).__name__, "message": str(error)}},
            }
        return web.json_response({"fixture_id": identity, "call_id": data["call_id"], "completion": completion})

    def outcome(self, call, result):
        return {"kind": "void"} if result is None else {"kind": "value", "value": result}

    async def invoke(self, fixture, call):
        return None


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
