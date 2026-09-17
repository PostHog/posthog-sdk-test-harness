"""Independent controlled analytics/profile implementation for runner tests."""

from copy import deepcopy
from uuid import uuid4

from tests.v2_flush_host import Host, QueueEngine


class AnalyticsEngine(QueueEngine):
    def capture(self, args):
        event = {
            "event": args["event"],
            "distinct_id": args.get("distinct_id"),
            "timestamp": self.clock,
            "uuid": str(uuid4()),
            "properties": {"$lib": "controlled-analytics", **deepcopy(args.get("properties", {}))},
        }
        if self.defect == "wrong_identity":
            event["distinct_id"] = "incorrect"
        if self.defect == "missing_uuid":
            del event["uuid"]
        if self.defect == "missing_property":
            event["properties"] = {}
        if self.defect != "missing_capture":
            self.records.append({"record_id": str(uuid4()), "event": event})
        if self.defect == "duplicate_event":
            self.records.append({"record_id": str(uuid4()), "event": deepcopy(event)})
        if self.defect == "incorrect_result":
            return False

    def identify(self, args):
        properties = {"$set": deepcopy(args.get("set", {}))}
        if self.defect in ("literal_profile", "masked_profile"):
            properties["$set.email"] = properties["$set"]["email"]
            if self.defect == "literal_profile":
                del properties["$set"]
            else:
                properties["$set"]["email"] = "incorrect"
        return self.capture({"event": "$identify", "distinct_id": args["distinct_id"], "properties": properties})

    def alias(self, args):
        return self.capture(
            {
                "event": "$create_alias",
                "distinct_id": args["distinct_id"],
                "properties": {"alias": args["alias"], "distinct_id": args["distinct_id"]},
            }
        )


class AnalyticsHost(Host):
    def __init__(self, contracts, **options):
        super().__init__(contracts, **options)
        self.profile["id"] = "controlled-analytics-v1"
        self.profile["module"]["entry"] = "tests.v2_analytics_host.AnalyticsEngine"
        self.routes = [
            r for r in ("/setup", "/capture", "/flush", "/identify", "/alias") if r != options.get("missing_route")
        ]

    async def invoke(self, fixture, call):
        args = call["args"]
        if call["route"] == "/setup":
            assert fixture.manual and fixture.clock and fixture.storage_prepared and fixture.engine is None
            assert set(args) == {"project_token", "config"} and set(args["config"]) == {"host"}
            fixture.engine = AnalyticsEngine(fixture.storage, fixture.clock, args["config"]["host"], fixture.defect)
            return await fixture.engine.setup()
        if call["route"] == "/identify":
            return fixture.engine.identify(args)
        if call["route"] == "/alias":
            return fixture.engine.alias(args)
        return await super().invoke(fixture, call)
