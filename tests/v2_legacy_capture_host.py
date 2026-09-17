"""Controlled legacy batch/event contracts; the engine owns state and HTTP retries."""

from tests.v2_analytics_wire_host import AnalyticsWireEngine, AnalyticsWireHost


class LegacyCaptureEngine(AnalyticsWireEngine):
    retryable_statuses = {408, 429, 500, 502, 503, 504}

    def __init__(self, storage, host, config, token, wire_variant, defect):
        super().__init__(storage, host, config, token, "legacy", defect)
        self.wire_variant = wire_variant

    def encode_request(self, path, payload, headers):
        # Wire contract is an engine choice, independent of profile runtime or claims.
        for event in payload.get("batch", []):
            properties = event.setdefault("properties", {})
            if self.defect != "omit_lib":
                properties["$lib"] = "controlled-legacy"
            if self.wire_variant == "event":
                if "distinct_id" in event:
                    properties["distinct_id"] = event.pop("distinct_id")
                properties["token"] = self.token
            if self.defect == "wrong_root_identity":
                event["distinct_id"] = "wrong"
            if self.defect == "wrong_property_identity":
                properties["distinct_id"] = "wrong"
            if self.defect == "missing_token":
                properties.pop("token", None)
            if self.defect == "shadow_event_token":
                event["token"] = "wrong"
        if self.defect == "missing_token":
            payload.pop("api_key", None)
        if self.wire_variant == "event":
            return "/e/", payload["batch"], {"Content-Type": "application/json"}
        return path, payload, {"Content-Type": "application/json"}


class LegacyCaptureHost(AnalyticsWireHost):
    def __init__(self, contracts, *, wire_variant="batch", sdk_capabilities=None, **options):
        if sdk_capabilities is None:
            sdk_capabilities = ("capture_v0", "capture_v0_" + wire_variant)
        super().__init__(contracts, sdk_capabilities=sdk_capabilities, protocol="legacy", **options)
        self.wire_variant = wire_variant
        self.profile["id"] = "controlled-legacy-capture-v1"
        self.profile["module"]["entry"] = "tests.v2_legacy_capture_host.LegacyCaptureEngine"

    async def invoke(self, fixture, call):
        if call["route"] == "/setup":
            args = call["args"]
            assert fixture.storage_prepared and fixture.engine is None
            fixture.engine = LegacyCaptureEngine(
                fixture.storage,
                args["config"]["host"],
                args["config"],
                args["project_token"],
                self.wire_variant,
                fixture.defect,
            )
            return await fixture.engine.setup()
        return await super().invoke(fixture, call)
