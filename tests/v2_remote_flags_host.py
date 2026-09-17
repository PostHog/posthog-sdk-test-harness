"""Controlled native remote getter/cache/listener engine, not an SDK adapter claim."""

from contextvars import ContextVar
from copy import deepcopy
from urllib.parse import quote
from uuid import uuid4

import aiohttp

from posthog_test_harness.v2.contracts import BoundaryError, decode_json
from tests.v2_ai_host import AIEngine, AIHost

OWNER = ContextVar("flag_owner", default=None)
UNDEFINED = object()


class RemoteFlagsEngine(AIEngine):
    """Owns fetching, HTTP parsing, cache choice, tracking and synchronous notifications."""

    def __init__(self, storage, host, config, protocol, defect, token, *, client=False, cached=False):
        super().__init__(storage, host, config, protocol, defect)
        self.token, self.client, self.cached = token, client, cached
        self.values, self.listeners, self.exposures = {}, [], set()
        self.loaded = False
        self.identity = "controlled-client"
        self.parsed_responses = []
        self.callback_contexts = []

    async def setup(self):
        if self.defect == "startup_flags":
            await self.fetch({"distinct_id": self.identity})

    async def capture(self, args):
        if self.defect == "capture_flags":
            await self.fetch({"distinct_id": args["distinct_id"]})
        await super().capture(args)

    async def fetch(self, args):
        body = {
            "token": self.token,
            "distinct_id": args.get("distinct_id", self.identity),
            "groups": deepcopy(args.get("groups", {})),
            "group_properties": deepcopy(args.get("group_properties", {})),
            "geoip_disable": args.get("disable_geoip", False),
        }
        if "person_properties" in args:
            body["person_properties"] = deepcopy(args["person_properties"])
        if "key" in args:
            body["flag_keys_to_evaluate"] = [args["key"]]
        if self.defect == "wrong_token":
            body["token"] = "incorrect"
        if self.defect == "api_key_alias":
            body["api_key"] = body.pop("token")
        if self.defect == "token_shadows_alias":
            body["api_key"], body["token"] = self.token, "incorrect"
        if self.defect == "wrong_identity":
            body["distinct_id"] = "incorrect"
        if self.defect == "device_at_root":
            body["device_id"] = body.pop("person_properties", {}).get("$device_id")
        if self.defect == "omit_groups":
            body.pop("groups")
        if self.defect == "wrong_group_properties":
            body["group_properties"] = {}
        if self.defect == "omit_geoip":
            body.pop("geoip_disable")
        if self.defect == "false_geoip_lost":
            body["geoip_disable"] = True
        if self.defect == "wrong_scope":
            body["flag_keys_to_evaluate"] = []
        path = "/decide" if self.defect == "decide" else "/flags"
        version = "1" if self.defect == "wrong_version" else "2"
        headers = {"Authorization": "Bearer incorrect"} if self.defect == "authorization" else {}
        async with aiohttp.ClientSession(trust_env=False) as session:
            for attempt in range(2):
                async with session.post(self.host + path + "?v=" + version, json=body, headers=headers) as response:
                    if response.status in (502, 504) and attempt == 0 and self.defect != "no_retry":
                        await response.read()
                        continue
                    if response.status != 200:
                        await response.read()
                        self.notify(True)
                        return {}
                    data = await response.json()
                    self.parsed_responses.append(deepcopy(data))
                    values = data["featureFlags"]
                    if self.defect == "wrong_parse":
                        values = {key: "incorrect" for key in values}
                    if self.client or self.cached or self.defect == "unexpected_cache":
                        self.values = deepcopy(values)
                        self.loaded = True
                    if self.client:
                        self.notify(data.get("errorsWhileComputingFlags"))
                    return values

    async def get_feature_flag(self, args):
        key = args["key"]
        if args.get("only_evaluate_locally"):
            return args.get("default_value")
        if self.client or ((self.cached or self.defect == "unexpected_cache") and self.loaded):
            values = self.values
        else:
            values = await self.fetch(args)
        value = values.get(key, args.get("default_value"))
        identity = args.get("distinct_id", self.identity)
        if key in values and args.get("send_event", True) and self.defect != "no_tracking":
            signature = (identity, key, type(value), value)
            if signature not in self.exposures:
                self.exposures.add(signature)
                properties = {"$feature_flag": key, "$feature_flag_response": value}
                if self.defect == "tracking_value":
                    properties["$feature_flag_response"] = "incorrect"
                if self.defect == "tracking_key":
                    properties["$feature_flag"] = "incorrect"
                await self.capture({"distinct_id": identity, "event": "$feature_flag_called", "properties": properties})
        return value

    async def reload_feature_flags(self, args):
        await self.fetch(args)

    def update_flags(self, args):
        if not args.get("merge"):
            self.values.clear()
        self.values.update(deepcopy(args["flags"]))
        self.loaded = True
        self.notify()

    def get_feature_flags(self):
        return deepcopy(self.values)

    def callback_args(self):
        enabled = {key: value for key, value in self.values.items() if value}
        return list(enabled), deepcopy(enabled)

    def on_feature_flags(self, callback):
        self.listeners.append(callback)
        if self.loaded and self.defect != "missing_immediate":
            self.callback_contexts.append(OWNER.get())
            callback(*self.callback_args())

        def unsubscribe():
            if callback in self.listeners and self.defect != "ignored_unsubscribe":
                self.listeners.remove(callback)

        return unsubscribe

    def dispose(self):
        self.listeners.clear()

    def notify(self, errors=UNDEFINED):
        context = {} if errors is UNDEFINED else {"errorsLoading": errors}
        for callback in list(self.listeners):
            self.callback_contexts.append(OWNER.get())
            callback(*self.callback_args(), context)


class RemoteFlagsHost(AIHost):
    def __init__(self, contracts, *, cached=False, sdk_type="server", **options):
        options.setdefault(
            "sdk_capabilities", ("flags_v2", "flags_getter_remote_uncached") if not cached else ("flags_v2",)
        )
        missing_callback = options.get("missing_capability") == "callbacks.continuation"
        if missing_callback:
            options.pop("missing_capability")
        super().__init__(contracts, **options)
        self.cached = cached
        if sdk_type is not None:
            self.profile["sdk_type"] = sdk_type
        self.profile["id"] = "controlled-remote-flags-v1"
        self.profile["products"] = ["flags", "analytics"]
        self.profile["module"]["entry"] = "tests.v2_remote_flags_host.RemoteFlagsEngine"
        if not missing_callback:
            self.profile["fixture_capabilities"].append("callbacks.continuation")
        self.routes = [
            r
            for r in (
                "/setup",
                "/capture",
                "/flush",
                "/get_feature_flag",
                "/get_feature_flags",
                "/update_flags",
                "/reload_feature_flags",
                "/on_feature_flags",
                "/subscription/unsubscribe",
            )
            if r != options.get("missing_route")
        ]
        self.extensions["fixtures/references"] = ("Reference", self.reference)
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

    async def reference(self, fixture_id, fixture, data):
        spec = data["fixture"]
        if spec["kind"] != "callback" or "callbacks.continuation" not in self.profile["fixture_capabilities"]:
            return self.response(
                "ReferenceResponse",
                {
                    "kind": "failed",
                    "fixture_id": fixture_id,
                    "failure": {
                        "kind": "blocked_fixture",
                        "code": "callback_unavailable",
                        "message": "Native callback binding unavailable",
                    },
                },
            )
        plan = spec["plan"]
        self.contracts.validate_plan(plan, fixture.references)
        ref = {"kind": "callback", "id": data["reference_id"]}
        fixture.references[ref["id"]] = "callback"
        fixture.retained[ref["id"]] = self.callback(fixture_id, fixture, ref, plan)
        return self.response("ReferenceResponse", {"kind": "created", "fixture_id": fixture_id, "reference": ref})

    def callback(self, fixture_id, fixture, ref, plan):
        index = 0

        def invoke(*args):
            nonlocal index
            prefix = f"@callback/{quote(fixture_id, safe='-._~')}/{quote(ref['id'], safe='-._~')}/{index}"
            outcomes = [
                {"kind": "undefined"} if arg is UNDEFINED else {"kind": "value", "value": deepcopy(arg)} for arg in args
            ]
            call_ids, results = [], {}
            try:
                if index >= plan["max_invocations"]:
                    raise BoundaryError("callback_limit", "Native callback exceeded bound", "blocked_fixture")
                for call in plan["calls"]:
                    # Only genuinely synchronous operations can run on this synchronous native stack.
                    receiver = call["receiver"]
                    if (
                        call["route"] != "/get_feature_flags"
                        or receiver != {"source": "reference", "reference": fixture.receiver}
                        or call["args"]
                        or call.get("references")
                    ):
                        raise BoundaryError(
                            "synchronous_continuation_unavailable",
                            "Continuation cannot run on native synchronous stack",
                            "blocked_fixture",
                        )
                    result = {"kind": "value", "value": fixture.engine.get_feature_flags()}
                    call_id = prefix + "/" + quote(call["step_id"], safe="-._~")
                    receipt = {
                        "fixture_id": fixture_id,
                        "call_id": call_id,
                        "parent_call_id": OWNER.get(),
                        "callback_invocation_id": prefix,
                        "route": call["route"],
                        "completion": {"kind": "sdk", "outcome": result},
                    }
                    fixture.observations.append(
                        {"kind": "call", "sequence": len(fixture.observations) + 1, "receipt": receipt}
                    )
                    results[call["step_id"]] = result
                    call_ids.append(call_id)
                returns = plan["returns"]
                result = (
                    returns["outcome"]
                    if returns["source"] == "literal"
                    else (
                        outcomes[returns["index"]]
                        if returns["source"] == "callback_argument"
                        else results[returns["step_id"]]
                    )
                )
                completion = {"kind": "sdk", "outcome": deepcopy(result)}
            except BoundaryError as error:
                completion = {"kind": "harness", "failure": error.failure()}
            fixture.observations.append(
                {
                    "kind": "callback",
                    "sequence": len(fixture.observations) + 1,
                    "fixture_id": fixture_id,
                    "callback": ref,
                    "invocation_id": prefix,
                    "invocation_index": index,
                    "owner_call_id": OWNER.get(),
                    "args": outcomes,
                    "completion": completion,
                    "call_ids": call_ids,
                }
            )
            index += 1
            if completion["kind"] == "sdk":
                return result.get("value")

        return invoke

    def outcome(self, call, result):
        if call["route"] in ("/get_feature_flag", "/get_feature_flags"):
            return {"kind": "value", "value": result}
        return super().outcome(call, result)

    async def invoke(self, fixture, call):
        token = OWNER.set(call["call_id"])
        try:
            route, args = call["route"], call["args"]
            if route == "/setup":
                fixture.engine = RemoteFlagsEngine(
                    fixture.storage,
                    args["config"]["host"],
                    args["config"],
                    self.profile["protocol"],
                    fixture.defect,
                    args["project_token"],
                    client=self.profile.get("sdk_type") == "client",
                    cached=self.cached,
                )
                self.engines.append(fixture.engine)
                return await fixture.engine.setup()
            engine = fixture.engine
            if route == "/get_feature_flag":
                return await engine.get_feature_flag(args)
            if route == "/get_feature_flags":
                return engine.get_feature_flags()
            if route == "/update_flags":
                return engine.update_flags(args)
            if route == "/reload_feature_flags":
                return await engine.reload_feature_flags(args)
            if route == "/on_feature_flags":
                callback = fixture.retained[call["references"]["/callback"]["id"]]
                unsubscribe = engine.on_feature_flags(callback)
                ref = {"kind": "subscription", "id": str(uuid4())}
                fixture.retained[ref["id"]] = unsubscribe
                fixture.references[ref["id"]] = "subscription"
                return ref
            if route == "/subscription/unsubscribe":
                return fixture.retained[call["receiver"]["id"]]()
            return await super().invoke(fixture, call)
        finally:
            OWNER.reset(token)
