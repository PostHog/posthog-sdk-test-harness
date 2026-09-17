"""Controlled remote evaluator and genuine retained objects for harness tests."""

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import aiohttp

from tests.v2_analytics_host import AnalyticsEngine, AnalyticsHost


class Snapshot:
    def __init__(self, engine, identity, values, payloads):
        self.engine, self.identity = engine, identity
        self.values, self.payloads = deepcopy(values), deepcopy(payloads)
        self.accessed = set()
        self.enablement_reads = 0

    def value(self, key):
        if key in self.values:
            self.accessed.add(key)
            self.engine.exposure(self.identity, key, self.values[key])
        value = self.values.get(key)
        if self.engine.defect == "wrong_flag" and key in self.values:
            return "incorrect"
        return value

    def enabled(self, key, default=False):
        value = self.value(key)
        self.enablement_reads += 1
        if self.engine.defect == "first_enablement_wrong" and self.enablement_reads == 1:
            return False
        return default if value is None else bool(value)

    def payload(self, key):
        if self.engine.defect == "payload_exposure":
            self.value(key)
        if self.engine.defect == "payload_marks_access":
            self.accessed.add(key)
        if self.engine.defect == "wrong_payload":
            return {"incorrect": True}
        return deepcopy(self.payloads.get(key))

    def keys(self):
        keys = list(self.values)
        return keys + keys[:1] if self.engine.defect == "duplicate_keys" else keys

    def only(self, keys):
        selected = (
            self.values
            if self.engine.defect == "broken_filter"
            else {k: v for k, v in self.values.items() if k in keys}
        )
        return Snapshot(self.engine, self.identity, selected, {k: v for k, v in self.payloads.items() if k in selected})

    def only_accessed(self):
        return self.only(self.accessed)


class FlagsEngine(AnalyticsEngine):
    def __init__(self, *args):
        super().__init__(*args)
        self.exposures = set()

    async def evaluate(self, args):
        if self.defect == "evaluation_timeout":
            await asyncio.Event().wait()
        identity = args.get("distinct_id")
        payload = {"distinct_id": identity, "api_key": "fixture-token"}
        if "flag_keys" in args and self.defect != "omit_scope":
            payload["flag_keys_to_evaluate"] = args["flag_keys"]
        if self.defect == "wrong_flag_identity":
            payload["distinct_id"] = "incorrect"
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(self.host + "/flags", json=payload) as response:
                response.raise_for_status()
                data = await response.json()
        values = data["featureFlags"]
        keys = args.get("flag_keys")
        if keys is not None and self.defect != "unscoped_snapshot":
            values = {k: v for k, v in values.items() if k in keys}
        payloads = {k: json.loads(v) for k, v in data["featureFlagPayloads"].items() if k in values}
        snapshot = Snapshot(self, identity, values, payloads)
        snapshot.remote_clean = data.get("errorsWhileComputingFlags") is False
        if self.defect == "eager_exposure":
            for key in values:
                snapshot.value(key)
        return snapshot

    def exposure(self, identity, key, value):
        signature = (identity, key, type(value), value)
        if signature in self.exposures and self.defect != "duplicate_exposure":
            return
        self.exposures.add(signature)
        super().capture(
            {
                "event": "$feature_flag_called",
                "distinct_id": identity,
                "properties": {"$feature_flag": key, "$feature_flag_response": value},
            }
        )

    async def capture_snapshot(self, args, snapshot):
        if self.defect == "capture_reevaluates":
            snapshot = await self.evaluate({"distinct_id": args["distinct_id"]})
        properties = {"$feature/" + k: v for k, v in snapshot.values.items()}
        properties["$active_feature_flags"] = [k for k, v in snapshot.values.items() if v]
        if self.defect == "capture_wrong_flag":
            properties["$feature/beta-ui"] = "incorrect"
        return super().capture({**args, "properties": properties})


class FlagsHost(AnalyticsHost):
    def __init__(self, contracts, **options):
        super().__init__(contracts, **options)
        self.profile["id"] = "controlled-flags-v1"
        self.profile["module"]["entry"] = "tests.v2_flags_host.FlagsEngine"
        self.routes += [
            r
            for r in (
                "/evaluate_flags",
                "/snapshot/is_enabled",
                "/snapshot/get_flag",
                "/snapshot/get_flag_payload",
                "/snapshot/keys",
                "/snapshot/only",
                "/snapshot/only_accessed",
            )
            if r != options.get("missing_route")
        ]

    def outcome(self, call, result):
        if call["route"].startswith("/snapshot/") or call["route"] == "/evaluate_flags":
            return {"kind": "value", "value": result}
        return super().outcome(call, result)

    def retain(self, fixture, snapshot):
        reference = {"kind": "snapshot", "id": str(uuid4())}
        fixture.references[reference["id"]] = reference["kind"]
        fixture.retained[reference["id"]] = snapshot
        return reference

    async def invoke(self, fixture, call):
        args, route = call["args"], call["route"]
        if route == "/setup":
            assert fixture.manual and fixture.clock and fixture.storage_prepared and fixture.engine is None
            assert set(args) == {"project_token", "config"} and set(args["config"]) == {"host"}
            fixture.engine = FlagsEngine(fixture.storage, fixture.clock, args["config"]["host"], fixture.defect)
            return await fixture.engine.setup()
        if route == "/evaluate_flags":
            return self.retain(fixture, await fixture.engine.evaluate(args))
        if route.startswith("/snapshot/"):
            snapshot = fixture.retained[call["receiver"]["id"]]
            if fixture.defect == "accessor_reevaluates":
                await fixture.engine.evaluate({"distinct_id": snapshot.identity})
            if route == "/snapshot/get_flag":
                return snapshot.value(args["key"])
            if route == "/snapshot/is_enabled":
                return snapshot.enabled(args["key"], args.get("default_value", False))
            if route == "/snapshot/get_flag_payload":
                return snapshot.payload(args["key"])
            if route == "/snapshot/keys":
                return snapshot.keys()
            if route == "/snapshot/only":
                return self.retain(fixture, snapshot.only(args["keys"]))
            if route == "/snapshot/only_accessed":
                return self.retain(fixture, snapshot.only_accessed())
        if route == "/capture" and "/flags" in call.get("references", {}):
            snapshot = fixture.retained[call["references"]["/flags"]["id"]]
            return await fixture.engine.capture_snapshot(args, snapshot)
        return await super().invoke(fixture, call)
