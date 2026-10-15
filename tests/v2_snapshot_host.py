"""Controlled snapshot engine over real mock HTTP; not SDK conformance evidence."""

import json
from copy import deepcopy

import aiohttp

from tests.v2_analytics_wire_host import AnalyticsWireEngine
from tests.v2_capture_exception_host import CaptureExceptionHost

UNDEFINED = object()


class Snapshot:
    def __init__(self, engine, identity, options, flags, payloads, accessed=None):
        self.engine, self.identity, self.options = engine, identity, options
        self.flags, self.payloads = flags, payloads
        self.accessed = set(accessed or ())

    async def access(self, key):
        self.accessed.add(key)
        if not self.identity or self.engine.disabled:
            return
        value = self.flags.get(key, UNDEFINED)
        signature = (self.identity, json.dumps(self.options.get("groups", {}), sort_keys=True), key)
        if signature in self.engine.exposures and self.engine.defect != "no_dedupe":
            return
        self.engine.exposures.add(signature)
        properties = {"$feature_flag": key}
        if value is UNDEFINED:
            properties["$feature_flag_error"] = "flag_missing"
            if self.engine.defect == "missing_response_true":
                properties["$feature_flag_response"] = True
        else:
            properties["$feature_flag_response"] = value
        if "groups" in self.options:
            properties["$groups"] = deepcopy(self.options["groups"])
        if self.engine.defect == "wrong_exposure":
            properties["$feature_flag_response"] = "wrong"
        if self.engine.defect == "missing_error":
            properties.pop("$feature_flag_error", None)
        if self.engine.defect == "wrong_groups":
            properties["$groups"] = {"organization": "wrong"}
        await self.engine.capture(
            {"event": "$feature_flag_called", "distinct_id": self.identity, "properties": properties}
        )

    async def get_flag(self, key):
        await self.access(key)
        return self.flags.get(key, UNDEFINED)

    async def is_enabled(self, key, options=None):
        await self.access(key)
        default = (options or {}).get("default_value", False)
        if self.engine.defect == "default_overrides" and options:
            return default
        return self.flags[key] is not False if key in self.flags else default

    async def get_flag_payload(self, key):
        if self.engine.defect == "payload_access":
            await self.access(key)
        return self.payloads.get(key, UNDEFINED)

    def keys(self):
        return list(self.flags)

    def only(self, keys):
        selected = (
            list(self.flags) if self.engine.defect == "filter_all" else [key for key in keys if key in self.flags]
        )
        child = Snapshot(
            self.engine,
            self.identity,
            self.options,
            {key: self.flags[key] for key in selected},
            {key: self.payloads[key] for key in selected if key in self.payloads},
            self.accessed,
        )
        if self.engine.defect == "parent_leak":
            child.accessed = self.accessed
        return child

    def only_accessed(self):
        selected = list(self.flags) if self.engine.defect == "accessed_all" else self.accessed
        return self.only(selected)


class SnapshotEngine(AnalyticsWireEngine):
    def __init__(self, storage, host, config, protocol, defect, *, token):
        super().__init__(storage, host, config, token, protocol, defect)
        self.disabled = config.get("disabled", False)
        self.exposures = set()
        self.previous = None

    async def evaluate(self, args):
        if self.defect == "no_evaluation":
            return Snapshot(self, args.get("distinct_id"), args.get("options") or {}, {}, {})
        identity = args.get("distinct_id")
        options = args.get("options") or {}
        if not identity and self.defect == "invent_identity":
            identity = "invented"
        if (not identity or self.disabled or options.get("flag_keys") == []) and self.defect != "empty_network":
            return Snapshot(self, identity, options, {}, {})
        body = {"token": self.token, "distinct_id": identity}
        body.update(
            {
                key: deepcopy(options[key])
                for key in ("groups", "person_properties", "group_properties")
                if key in options
            }
        )
        if "disable_geoip" in options:
            body["geoip_disable"] = options["disable_geoip"]
        if "flag_keys" in options:
            body["flag_keys_to_evaluate"] = options["flag_keys"]
        if self.defect == "wrong_context":
            body["person_properties"] = {"attempt": False}
        if self.defect == "wrong_scope":
            body["flag_keys_to_evaluate"] = []
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(self.host + "/flags/?v=2", json=body) as response:
                data = await response.json() if response.status == 200 else {}
        if self.defect == "extra_request":
            await self.post("/flags/?v=2", body)
        values = data.get("featureFlags", {})
        if "flag_keys" in options:
            values = {key: value for key, value in values.items() if key in options["flag_keys"]}
        payloads = {
            key: json.loads(value) for key, value in data.get("featureFlagPayloads", {}).items() if key in values
        }
        snapshot = Snapshot(self, identity, options, values, payloads)
        if self.defect == "stale_snapshot" and self.previous:
            return self.previous
        self.previous = snapshot
        return snapshot


class SnapshotHost(CaptureExceptionHost):
    def __init__(self, contracts, *, value_shape="scalar", payload_shape="decoded", **options):
        super().__init__(contracts, **options)
        self.value_shape, self.payload_shape = value_shape, payload_shape
        self.profile["sdk_capabilities"] += [
            "flag_snapshot_value_" + value_shape,
            "flag_snapshot_payload_" + payload_shape,
            "flag_snapshot_missing_undefined",
            "flag_snapshot_exposure_missing_absent",
            "flag_snapshot_enablement_default",
        ]
        if options.get("missing_route") != "/evaluate_flags/read":
            self.routes.append("/evaluate_flags/read")

    async def invoke(self, fixture, call):
        args = call["args"]
        if call["route"] == "/setup":
            config = args["config"]
            fixture.engine = SnapshotEngine(
                fixture.storage,
                config["host"],
                config,
                self.profile["protocol"],
                fixture.defect,
                token=args["project_token"],
            )
            fixture.engine.disabled = config.get("disabled", False)
            return await fixture.engine.setup()
        if call["route"] != "/evaluate_flags/read":
            return await super().invoke(fixture, call)
        if fixture.defect == "operation_throw":
            raise RuntimeError("Controlled snapshot failure")
        snapshot = await fixture.engine.evaluate(args)
        return await self.reads(snapshot, args["reads"])

    async def reads(self, snapshot, reads):
        results = []
        for read in reads:
            method = read["method"]
            if snapshot.engine.defect == "accessor_retry":
                await snapshot.engine.post(
                    "/flags/?v=2", {"token": snapshot.engine.token, "distinct_id": snapshot.identity}
                )
            if (
                snapshot.engine.defect in ("enumeration_access", "filter_access")
                and method
                in (("keys",) if snapshot.engine.defect == "enumeration_access" else ("only", "only_accessed"))
                and snapshot.flags
            ):
                await snapshot.access(next(iter(snapshot.flags)))
            if method in ("only", "only_accessed"):
                child = snapshot.only(read["keys"]) if method == "only" else snapshot.only_accessed()
                value = await self.reads(child, read["reads"])
            elif method == "keys":
                value = snapshot.keys()
            elif method == "is_enabled":
                value = await snapshot.is_enabled(read["key"], read.get("options"))
            elif method == "get_flag":
                value = await snapshot.get_flag(read["key"])
                if value is not UNDEFINED and self.value_shape == "rich":
                    value = {
                        "key": read["key"],
                        "enabled": value is not False,
                        "variant": value if isinstance(value, str) else None,
                    }
            else:
                value = await snapshot.get_flag_payload(read["key"])
                if value is not UNDEFINED and self.payload_shape == "serialized":
                    value = json.dumps(value)
            if (
                snapshot.engine.defect == "drop_falsy"
                and method == "get_flag_payload"
                and value is not UNDEFINED
                and not value
            ):
                value = UNDEFINED
            if snapshot.engine.defect == "double_decode" and method == "get_flag_payload" and isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            if snapshot.engine.defect == "coerce_bool" and type(value) is bool:
                value = int(value)
            if snapshot.engine.defect == "wrong_payload" and method == "get_flag_payload":
                value = "wrong"
            results.append({"kind": "undefined"} if value is UNDEFINED else {"kind": "value", "value": value})
        if snapshot.engine.defect == "reorder":
            results.reverse()
        if snapshot.engine.defect == "extra_result":
            results.append({"kind": "value", "value": None})
        return {"results": results}
