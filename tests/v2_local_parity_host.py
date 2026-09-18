"""Controlled HTTP loader and narrow versioned evaluator, not SDK conformance."""

import asyncio
import json

import aiohttp

from posthog_test_harness.v2.contracts import BoundaryError, decode_json
from tests.v2_ai_host import AIEngine, AIHost


def truthy(value):
    if isinstance(value, list):
        return all(truthy(item) for item in value)
    return value if type(value) is bool else isinstance(value, str) and value.lower() == "true"


def boolean_like(value):
    if isinstance(value, list):
        return all(boolean_like(item) for item in value)
    return type(value) is bool or isinstance(value, str) and value.lower() in ("true", "false")


def normalized(value):
    return (value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), sort_keys=True)).lower()


def exact(condition, value, version):
    # The scoped equality semantics in local-feature-flag-evaluator/spec.md:318-324.
    if condition == []:
        return truthy(value)
    if version != 2 and boolean_like(condition):
        return truthy(condition) == truthy(value)
    members = condition if isinstance(condition, list) else [condition]
    return any(normalized(member) == normalized(value) for member in members)


class LocalParityEngine(AIEngine):
    def __init__(self, storage, host, config, protocol, defect, token, path):
        super().__init__(storage, host, config, protocol, defect)
        self.token, self.secret, self.path = token, config.get("secret_key"), path
        self.document = None
        self.reload_count = 0
        self.disposed = False

    async def setup(self):
        await super().setup()
        if self.secret is not None and self.defect != "no_initial_fetch":
            await self.load_definitions(initial=True)

    async def reload_feature_flags(self):
        self.reload_count += 1
        if self.defect == "reload_timeout":
            await asyncio.Event().wait()
        if self.defect != "no_new_fetch":
            await self.load_definitions()

    async def load_definitions(self, initial=False):
        secret = "incorrect" if self.defect == "auth_error" and not initial else self.secret
        token = "incorrect" if self.defect == "token_error" and not initial else self.token
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.get(
                self.host + self.path, params={"token": token}, headers={"Authorization": "Bearer " + (secret or "")}
            ) as response:
                if response.status != 200:
                    # Native loaders can swallow a failure and retain old readiness.
                    await response.read()
                    return
                document = await response.json()
        self.validate_document(document)
        if self.defect == "ignored_reload" and self.reload_count > 0:
            return
        if self.defect == "ignored_version":
            document.pop("property_matching_version", None)
        if self.defect == "omission_retains_version" and "property_matching_version" not in document and self.document:
            document["property_matching_version"] = self.document.get("property_matching_version", 1)
        if self.defect != "not_installed":
            self.document = document

    @staticmethod
    def validate_document(document):
        def prop(p):
            if p["type"] == "cohort":
                assert set(p) == {"key", "type", "value"} and p["key"] == "id"
                assert str(p["value"]) in document["cohorts"]
            else:
                assert set(p) == {"key", "type", "value", "operator"}
                assert p["type"] in ("person", "group") and p["operator"] in ("exact", "is_not")

        try:
            assert set(document) <= {"flags", "cohorts", "group_type_mapping", "property_matching_version"}
            assert type(document.get("property_matching_version", 1)) is int
            assert document.get("property_matching_version", 1) in (1, 2)
            assert len({f["key"] for f in document["flags"]}) == len(document["flags"])
            for flag in document["flags"]:
                assert set(flag) <= {"id", "name", "key", "active", "version", "filters"}
                assert type(flag["active"]) is bool and isinstance(flag["key"], str)
                filters = flag["filters"]
                assert set(filters) <= {"groups", "aggregation_group_type_index"}
                assert len(filters["groups"]) == 1
                group = filters["groups"][0]
                assert set(group) == {"properties", "rollout_percentage"} and group["rollout_percentage"] == 100
                for p in group["properties"]:
                    prop(p)
            for cohort in document["cohorts"].values():
                assert set(cohort) == {"type", "values"} and cohort["type"] in ("AND", "OR")
                for p in cohort["values"]:
                    prop(p)
        except (AssertionError, KeyError, TypeError):
            raise BoundaryError(
                "unsupported_definition", "Controlled equality evaluator cannot load this shape", "blocked_fixture"
            ) from None

    def property_match(self, prop, properties, version, seen=()):
        if prop["type"] == "cohort":
            key = str(prop["value"])
            if key in seen:
                raise BoundaryError(
                    "unsupported_definition", "Cyclic cohort is outside this evaluator", "blocked_fixture"
                )
            cohort = self.document["cohorts"][key]
            values = [self.property_match(p, properties, version, (*seen, key)) for p in cohort["values"]]
            if None in values:
                return None
            return all(values) if cohort["type"] == "AND" else any(values)
        if prop["key"] not in properties:
            return None
        match = exact(prop["value"], properties[prop["key"]], version)
        return not match if prop["operator"] == "is_not" else match

    def evaluate_local(self, args):
        if self.document is None:
            return None
        flag = next((f for f in self.document["flags"] if f["key"] == args["key"]), None)
        if flag is None:
            return None
        if not flag["active"]:
            return False
        filters = flag["filters"]
        properties = args.get("person_properties", {})
        if "aggregation_group_type_index" in filters:
            group_type = self.document["group_type_mapping"][str(filters["aggregation_group_type_index"])]
            if group_type not in args.get("groups", {}):
                return None
            properties = args.get("group_properties", {}).get(group_type, {})
        values = [
            self.property_match(p, properties, self.document.get("property_matching_version", 1))
            for p in filters["groups"][0]["properties"]
        ]
        return None if None in values else all(values)

    async def get_feature_flag(self, args):
        value = self.evaluate_local(args)
        if self.defect == "remote_escape":
            await self.post("/flags", {"token": self.token, "distinct_id": args["distinct_id"]})
        if self.defect == "wrong_bool":
            value = not value
        if self.defect == "wrong_string":
            value = "incorrect"
        if self.defect == "inconclusive":
            value = None
        if value is not None and args.get("send_event", True):
            await self.capture(
                {
                    "distinct_id": args["distinct_id"],
                    "event": "$feature_flag_called",
                    "properties": {"$feature_flag": args["key"], "$feature_flag_response": value},
                }
            )
        return value

    def dispose(self):
        self.document = None
        self.disposed = True


class LocalParityHost(AIHost):
    def __init__(self, contracts, *, definition_path="/flags/definitions", **options):
        options.setdefault("sdk_capabilities", ("feature_flags_local_evaluation_v1",))
        super().__init__(contracts, **options)
        self.definition_path = definition_path
        self.profile["id"] = "controlled-local-parity-v1"
        self.profile["products"] = ["flags"]
        self.profile["module"]["entry"] = "tests.v2_local_parity_host.LocalParityEngine"
        self.routes = [
            r for r in ("/setup", "/get_feature_flag", "/reload_feature_flags") if r != options.get("missing_route")
        ]
        self.engines = []

    async def handle(self, request):
        engine = None
        if request.path == "/v2/fixtures/close":
            data = decode_json(await request.read())
            fixture = self.fixtures.get(data.get("fixture_id"))
            engine = fixture.engine if fixture else None
        response = await super().handle(request)
        if engine is not None and response.status == 200:
            engine.dispose()
        return response

    def outcome(self, call, result):
        if call["route"] == "/get_feature_flag":
            if self.defect == "wrong_public_value":
                result = not result
            return {"kind": "value", "value": result}
        return super().outcome(call, result)

    async def invoke(self, fixture, call):
        args, route = call["args"], call["route"]
        if route == "/setup":
            assert fixture.storage_prepared and fixture.engine is None
            fixture.engine = LocalParityEngine(
                fixture.storage,
                args["config"]["host"],
                args["config"],
                self.profile["protocol"],
                fixture.defect,
                args["project_token"],
                self.definition_path,
            )
            self.engines.append(fixture.engine)
            return await fixture.engine.setup()
        if route == "/reload_feature_flags":
            return await fixture.engine.reload_feature_flags()
        if route == "/get_feature_flag":
            return await fixture.engine.get_feature_flag(args)
        return await super().invoke(fixture, call)
