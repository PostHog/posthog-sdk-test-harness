"""Native stores and instrumented evaluator for flags fixture conformance tests."""

import asyncio
import json
from copy import deepcopy
from datetime import datetime

import aiohttp

from posthog_test_harness.v2.contracts import BoundaryError
from posthog_test_harness.v2.flag_fixtures import CAPABILITIES
from tests.v2_flags_host import FlagsEngine, FlagsHost, Snapshot


class LocalFlagsEngine(FlagsEngine):
    def __init__(self, *args):
        super().__init__(*args)
        self.definitions, self.cache = {}, {}
        self.ready = asyncio.Event()
        self.activity = {"cache_lookups": 0, "local_evaluations": 0}

    def install(self, document):
        # A bounded controlled evaluator, not an implementation of every v1 rule.
        # Unsupported fixture shapes must be visible rather than treated as truthy.
        try:
            assert set(document) == {"flags", "cohorts", "group_type_mapping"}
            assert document["cohorts"] == document["group_type_mapping"] == {}
            assert isinstance(document["flags"], list)
            definitions = {}
            for definition in document["flags"]:
                assert set(definition) == {"id", "key", "active", "version", "filters"}
                assert isinstance(definition["key"], str) and type(definition["active"]) is bool
                assert type(definition["id"]) is int and type(definition["version"]) is int
                assert definition["key"] not in definitions
                filters = definition["filters"]
                assert set(filters) <= {"groups", "multivariate"}
                assert len(filters["groups"]) == 1
                group = filters["groups"][0]
                assert set(group) <= {"properties", "rollout_percentage", "variant"}
                assert group["rollout_percentage"] == 100
                assert isinstance(group["properties"], list)
                for prop in group["properties"]:
                    assert set(prop) == {"key", "type", "operator", "value"}
                    assert prop["type"] == "person" and prop["operator"] == "exact"
                    assert isinstance(prop["key"], str) and isinstance(prop["value"], str)
                if "multivariate" in filters:
                    assert isinstance(group["variant"], str)
                    assert filters["multivariate"] == {
                        "variants": [{"key": group["variant"], "rollout_percentage": 100}]
                    }
                else:
                    assert "variant" not in group
                definitions[definition["key"]] = deepcopy(definition)
        except (AssertionError, KeyError, TypeError):
            raise BoundaryError(
                "unsupported_definition", "Controlled evaluator cannot load this definition shape", "blocked_fixture"
            ) from None
        if self.defect != "definitions_ignored":
            self.definitions = definitions
            self.ready.set()

    @staticmethod
    def context_key(args):
        fields = ("distinct_id", "groups", "person_properties", "group_properties", "disable_geoip", "device_id")
        return json.dumps({key: args[key] for key in fields if key in args}, sort_keys=True)

    def cache_put(self, command):
        context = {"distinct_id": command["distinct_id"]}
        if self.defect != "cache_seed_ignored":
            expires_at = datetime.fromisoformat(self.clock.replace("Z", "+00:00")).timestamp() + 300
            self.cache[self.context_key(context)] = (
                expires_at,
                deepcopy(command["flags"]),
                deepcopy(command["payloads"]),
            )

    def lookup(self, args):
        self.activity["cache_lookups"] += 1
        entry = self.cache.get(self.context_key(args))
        now = datetime.fromisoformat(self.clock.replace("Z", "+00:00")).timestamp()
        return entry[1:] if entry is not None and entry[0] > now else None

    def local(self, key, args):
        self.activity["local_evaluations"] += 1
        definition = self.definitions.get(key)
        if definition is None:
            return None
        if not definition["active"]:
            return False
        group = definition["filters"]["groups"][0]
        properties = args.get("person_properties", {})
        for prop in group["properties"]:
            if prop["key"] not in properties:
                return None
            if properties[prop["key"]] != prop["value"]:
                return False
        return group.get("variant", True)

    async def remote(self, args):
        return await super().evaluate(args)

    async def evaluate(self, args):
        keys = args.get("flag_keys")
        identity = args.get("distinct_id")
        if keys == []:
            if self.defect == "empty_looks_up_cache":
                self.lookup(args)
            if self.defect == "empty_evaluates_locally":
                self.local(next(iter(self.definitions), "missing"), args)
            if self.defect == "empty_evaluates_remotely":
                await super().evaluate(args)
            return Snapshot(self, identity, {}, {})
        cached = self.lookup(args)
        if cached is not None:
            values, payloads = deepcopy(cached)
            if keys is not None and self.defect != "cache_scope_ignored":
                values = {key: value for key, value in values.items() if key in keys}
            return Snapshot(self, identity, values, {key: value for key, value in payloads.items() if key in values})
        selected = list(self.definitions) if keys is None else keys
        values, unresolved = {}, []
        for key in selected:
            value = self.local(key, args)
            if value is None:
                unresolved.append(key)
            else:
                values[key] = value
        if (
            (unresolved or not self.definitions)
            and self.defect != "skip_fallback"
            and (not args.get("only_evaluate_locally", False) or self.defect == "local_only_remote")
        ):
            try:
                remote = await self.remote(args)
            except aiohttp.ClientResponseError:
                if self.defect == "lose_local_on_failure":
                    values.clear()
            else:
                values = (
                    {**values, **remote.values}
                    if self.defect == "remote_overwrites_local"
                    else {**remote.values, **values}
                )
        return Snapshot(self, identity, values, {})


class LocalFlagsHost(FlagsHost):
    engine_type = LocalFlagsEngine

    def __init__(self, contracts, missing_capability=None, **options):
        native = missing_capability in CAPABILITIES.values()
        super().__init__(contracts, missing_capability=None if native else missing_capability, **options)
        self.profile["id"] = "controlled-local-flags-v1"
        self.profile["module"]["entry"] = "tests.v2_local_flags_host.LocalFlagsEngine"
        self.profile["fixture_capabilities"] += [cap for cap in CAPABILITIES.values() if cap != missing_capability]
        self.extensions["fixtures/flags"] = ("FlagState", self.control_flags)
        self.routes += [
            r
            for r in (
                "/get_feature_flag",
                "/get_feature_flags",
                "/is_local_evaluation_ready",
                "/wait_for_local_evaluation_ready",
            )
            if r != options.get("missing_route")
        ]

    async def control_flags(self, fixture_id, fixture, data):
        command = data["command"]
        kind = command["kind"]
        base = {"fixture_id": fixture_id, "command": kind}
        self.controls.append(deepcopy(data))

        async def apply():
            if (
                CAPABILITIES[kind] not in self.profile["fixture_capabilities"]
                or fixture.defect == "native_component_unavailable"
            ):
                raise BoundaryError("component_unavailable", "No native flag component fixture", "blocked_fixture")
            assert fixture.engine is not None and fixture.manual
            if fixture.defect == "native_fixture_timeout":
                await asyncio.Event().wait()
            engine = fixture.engine
            site = {"layer": "native_component", "implementation": "tests.v2_local_flags_host.LocalFlagsEngine."}
            if kind == "definitions_install":
                engine.install(command["definitions"])
                site["implementation"] += "definitions"
            elif kind == "evaluation_cache_put":
                engine.cache_put(command)
                site["implementation"] += "cache"
            else:
                site["implementation"] += "lookup/local"
                return {**base, "kind": "activity", "observation": {**site, **engine.activity}}
            return {**base, "kind": "applied", "observation": site}

        fixture.task = asyncio.create_task(apply())
        try:
            response = await asyncio.wait_for(fixture.task, data["timeout_ms"] / 1000)
        except TimeoutError:
            fixture.expired = True
            fixture.references.clear()
            response = {
                **base,
                "kind": "failed",
                "failure": {
                    "kind": "timeout",
                    "code": "fixture_deadline",
                    "message": "Native fixture deadline elapsed",
                },
            }
        except BoundaryError as error:
            response = {**base, "kind": "failed", "failure": error.failure()}
        if fixture.defect == "wrong_native_fixture":
            response["fixture_id"] = "other-fixture"
        return self.response("FlagStateResponse", response)

    def outcome(self, call, result):
        if call["route"] in (
            "/get_feature_flag",
            "/get_feature_flags",
            "/is_local_evaluation_ready",
            "/wait_for_local_evaluation_ready",
        ):
            return {"kind": "value", "value": result}
        return super().outcome(call, result)

    async def invoke(self, fixture, call):
        args, route = call["args"], call["route"]
        if route == "/setup":
            assert fixture.manual and fixture.clock and fixture.storage_prepared and fixture.engine is None
            assert set(args) == {"project_token", "config"} and set(args["config"]) == {"host"}
            fixture.engine = self.engine_type(fixture.storage, fixture.clock, args["config"]["host"], fixture.defect)
            return await fixture.engine.setup()
        if route == "/get_feature_flag":
            snapshot = await fixture.engine.evaluate(
                {k: v for k, v in args.items() if k != "key"} | {"flag_keys": [args["key"]]}
            )
            return snapshot.value(args["key"])
        if route == "/get_feature_flags":
            return (await fixture.engine.evaluate(args)).values
        if route == "/is_local_evaluation_ready":
            return fixture.engine.ready.is_set()
        if route == "/wait_for_local_evaluation_ready":
            try:
                await asyncio.wait_for(fixture.engine.ready.wait(), args.get("timeout_ms", 30000) / 1000)
                return True
            except TimeoutError:
                return False
        return await super().invoke(fixture, call)
