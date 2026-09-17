"""Controlled stateful cache owned and read by the native test engine."""

from copy import deepcopy
from uuid import uuid4

from tests.v2_flags_host import FlagsEngine, FlagsHost

GETTERS = (
    "/get_feature_flag",
    "/is_feature_enabled",
    "/get_feature_flag_payload",
    "/get_feature_flag_result",
    "/get_feature_flags",
    "/get_feature_flags_and_payloads",
)


class CachedFlagsEngine(FlagsEngine):
    def __init__(self, *args):
        super().__init__(*args)
        self.identity = str(uuid4())
        self.values, self.payloads = {}, {}

    def update_flags(self, args):
        if self.defect == "cache_not_updated":
            return
        if not args.get("merge", False):
            self.values.clear()
            self.payloads.clear()
        self.values.update(deepcopy(args["flags"]))
        self.payloads.update(deepcopy(args.get("payloads", {})))

    def reset(self):
        if self.defect != "cache_not_reset":
            self.values.clear()
            self.payloads.clear()
            self.exposures.clear()
            self.identity = str(uuid4())

    def exposure(self, identity, key, value):
        if self.defect == "string_exposure" and isinstance(value, bool):
            value = str(value).lower()
        return super().exposure(identity, key, value)

    def track(self, args):
        key = args["key"]
        if key in self.values and (args.get("send_event", True) or self.defect == "ignored_tracking_option"):
            self.exposure(self.identity, key, self.values[key])

    async def get(self, route, args):
        if self.defect == "cached_read_network":
            await self.post("/flags", {"distinct_id": self.identity})
        key = args.get("key")
        if route in ("/get_feature_flag", "/is_feature_enabled", "/get_feature_flag_result"):
            self.track(args)
        if route == "/get_feature_flag":
            return (
                "incorrect" if self.defect == "wrong_cached_value" else self.values.get(key, args.get("default_value"))
            )
        if route == "/is_feature_enabled":
            value = self.values.get(key)
            if self.defect == "false_is_missing" and value is False:
                value = None
            return args.get("default_value", False) if value is None else bool(value)
        if route == "/get_feature_flag_payload":
            if self.defect == "payload_exposure":
                self.track(args)
            value = self.payloads.get(key)
            return args.get("default_value") if value is None else deepcopy(value)
        if route == "/get_feature_flag_result":
            if key not in self.values:
                return None
            value = self.values[key]
            result = {
                "key": key,
                "enabled": bool(value),
                "variant": value if isinstance(value, str) else None,
                "payload": deepcopy(self.payloads.get(key)),
            }
            if self.defect == "wrong_result_key":
                result["key"] = "incorrect"
            if self.defect == "wrong_result_variant":
                result["variant"] = "incorrect"
            if self.defect == "missing_result_variant":
                del result["variant"]
            return result
        if route == "/get_feature_flags":
            values = deepcopy(self.values)
            if self.defect == "extra_bulk_flag":
                values["extra"] = True
            return values
        if route == "/get_feature_flags_and_payloads":
            payloads = deepcopy(self.payloads)
            if self.defect == "wrong_paired_payload":
                payloads = {"incorrect": True}
            return {"flags": deepcopy(self.values), "payloads": payloads}
        raise AssertionError("Unknown cached getter")


class CachedFlagsHost(FlagsHost):
    def __init__(self, contracts, **options):
        super().__init__(contracts, **options)
        self.profile["id"] = "controlled-cached-flags-v1"
        self.profile["runtime"]["family"] = "desktop"
        self.profile["identity"] = "stateful_installation"
        self.profile["module"]["entry"] = "tests.v2_cached_flags_host.CachedFlagsEngine"
        self.routes += [r for r in (*GETTERS, "/update_flags", "/reset") if r != options.get("missing_route")]

    def outcome(self, call, result):
        if call["route"] in GETTERS:
            fixture = next(f for f in self.fixtures.values() if f.receiver == call["receiver"])
            if fixture.defect == "null_as_void" and result is None:
                return {"kind": "void"}
            return {"kind": "value", "value": result}
        return super().outcome(call, result)

    async def invoke(self, fixture, call):
        args, route = call["args"], call["route"]
        if route == "/setup":
            assert fixture.manual and fixture.clock and fixture.storage_prepared and fixture.engine is None
            assert set(args) == {"project_token", "config"} and set(args["config"]) == {"host"}
            fixture.engine = CachedFlagsEngine(fixture.storage, fixture.clock, args["config"]["host"], fixture.defect)
            return await fixture.engine.setup()
        if route == "/update_flags":
            return fixture.engine.update_flags(args)
        if route == "/reset":
            return fixture.engine.reset()
        if route in GETTERS:
            return await fixture.engine.get(route, args)
        return await super().invoke(fixture, call)
