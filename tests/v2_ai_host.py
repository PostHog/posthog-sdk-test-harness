"""Controlled public AI engine over real HTTP, not native SDK conformance."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from tests.v2_flush_host import Host, QueueEngine


class AIEngine(QueueEngine):
    def __init__(self, storage, host, config, protocol, defect):
        super().__init__(storage, None, host, defect)
        self.flush_at, self.protocol = config.get("flush_at", 20), protocol

    async def setup(self) -> None:
        if self.defect == "startup_flags":
            await self.post("/flags", {"api_key": "fixture-token"})
        if self.defect == "startup_capture":
            await self.post("/batch", {"batch": [{"event": "unexpected"}]})

    async def enqueue(self, args, ai):
        identity = args.get("uuid") or str(uuid4())
        timestamp = args.get("timestamp") or datetime.now(timezone.utc).isoformat()
        wire_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        event = {
            "event": args["event"],
            "distinct_id": args["distinct_id"],
            "uuid": identity,
            "timestamp": wire_timestamp,
            "properties": deepcopy(args.get("properties", {})),
        }
        event = self.enrich_event(event, args)
        if self.defect == "wrong_event":
            event["event"] = "wrong"
        if self.defect == "missing_uuid":
            del event["uuid"]
        if self.defect == "invalid_uuid":
            event["uuid"] = "not-a-uuid"
        if self.defect == "replace_supplied_uuid":
            identity = event["uuid"] = str(uuid4())
        if self.defect == "wire_uuid":
            event["uuid"] = str(uuid4())
        if self.defect == "offset_timestamp":
            event["timestamp"] = timestamp
        if self.defect == "wrong_instant":
            event["timestamp"] = "2025-01-02T08:34:05Z"
        if self.defect == "nanosecond_drift":
            event["timestamp"] = "2025-01-02T03:04:05.000000001Z"
        if self.defect == "rewrite_property":
            event["properties"]["timestamp_like"] = wire_timestamp
        path = "/i/v0/ai/batch/" if ai else "/batch" if self.protocol == "legacy" else "/i/v1/analytics/events"
        if self.defect == "wrong_route":
            path = "/batch"
        if self.defect == "reroute":
            path = "/i/v0/ai/batch/"
        if self.defect != "no_delivery":
            self.records.append({"path": path, "event": event})
        if len(self.records) >= self.flush_at and self.defect != "suppress_threshold":
            await self.flush()
        if self.defect == "null_result":
            return None
        if self.defect == "returned_uuid":
            return str(uuid4())
        return identity

    def enrich_event(self, event, args):
        return event

    async def capture_ai(self, args) -> str | None:
        return await self.enqueue(args, True)

    async def capture(self, args) -> None:
        await self.enqueue(args, False)

    async def flush(self) -> None:
        records, self.records[:] = list(self.records), []
        for record in records:
            events = [record["event"]]
            if self.defect == "second_event_timestamp":
                events.append({**record["event"], "timestamp": "2025-01-02T03:04:06Z"})
            await self.post(record["path"], {"batch": events})
            if self.defect == "duplicate_request":
                await self.post(record["path"], {"batch": events})


class AIHost(Host):
    def __init__(
        self, contracts, *, runtime="server", protocol="legacy", sdk_capabilities=("capture_ai_v0",), **options
    ):
        super().__init__(contracts, **options)
        self.profile["id"] = "controlled-ai-v1"
        self.profile["runtime"]["family"] = runtime
        self.profile["identity"] = "stateful_installation" if runtime != "server" else "request_scoped"
        self.profile["protocol"] = protocol
        self.profile["products"] = ["analytics", "ai"]
        self.profile["module"]["entry"] = "tests.v2_ai_host.AIEngine"
        if sdk_capabilities is not None:
            self.profile["sdk_capabilities"] = list(sdk_capabilities)
        self.routes = [r for r in ("/setup", "/capture_ai", "/capture", "/flush") if r != options.get("missing_route")]

    def outcome(self, call, result):
        # Native AIEngine.capture_ai is data-valued, including None; not void.
        if call["route"] == "/capture_ai":
            return {"kind": "value", "value": result}
        return super().outcome(call, result)

    async def invoke(self, fixture, call):
        args = call["args"]
        if call["route"] == "/setup":
            assert fixture.storage_prepared and fixture.engine is None
            fixture.engine = AIEngine(
                fixture.storage, args["config"]["host"], args["config"], self.profile["protocol"], fixture.defect
            )
            return await fixture.engine.setup()
        if call["route"] == "/capture_ai":
            return await fixture.engine.capture_ai(args)
        if call["route"] == "/capture":
            return await fixture.engine.capture(args)
        assert call["route"] == "/flush" and args == {}
        return await fixture.engine.flush()
