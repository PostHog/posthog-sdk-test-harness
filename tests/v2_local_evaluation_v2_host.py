"""Controlled loader for the v=2 definitions feed over real mock HTTP, not SDK conformance.

The engine reads only what the step tests serve: a config version 2 flag answers its first
rule's value, a config version 1 flag with an empty 100% condition answers true, an inactive
flag answers false, and any other config version falls back to /flags.
"""

import aiohttp

from tests.v2_ai_host import AIEngine, AIHost


class LocalV2Engine(AIEngine):
    def __init__(self, storage, host, config, protocol, defect, token):
        super().__init__(storage, host, config, protocol, defect)
        self.token, self.secret = token, config.get("secret_key")
        self.document, self.etag = None, None
        self.loads = 0

    async def setup(self):
        await super().setup()
        if self.secret is not None:
            await self.load_definitions()

    def query(self):
        if self.defect == "no_v" or (self.defect == "poll_drops_v" and self.loads > 0):
            return [("token", self.token)]
        if self.defect == "v1":
            return [("token", self.token), ("v", "1")]
        if self.defect == "repeated_v":
            return [("token", self.token), ("v", "2"), ("v", "2")]
        return [("token", self.token), ("v", "2")]

    async def load_definitions(self):
        headers = {"Authorization": "Bearer " + self.secret}
        if self.etag is not None:
            headers["If-None-Match"] = self.etag
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.get(self.host + "/flags/definitions", params=self.query(), headers=headers) as response:
                self.loads += 1
                if response.status == 304:
                    await response.read()
                    if self.defect == "refetch_on_304":
                        # Drops what it holds, so the next read downloads the feed again.
                        self.document, self.etag = None, None
                    return
                if response.status != 200:
                    await response.read()
                    return
                self.document = await response.json()
                self.etag = response.headers.get("ETag")

    async def reload_feature_flags(self):
        if self.defect == "reload_remote":
            await self.post("/flags/?v=4", {"token": self.token, "distinct_id": "reload"})
        await self.load_definitions()

    def local_value(self, flag):
        if not flag["active"]:
            return False
        filters = flag["filters"]
        version = filters.get("version", 1)
        if version == 2:
            return filters["rules"][0]["value"]
        if version == 1:
            return True
        return None

    async def remote_value(self, key, distinct_id):
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(
                self.host + "/flags/?v=4", json={"token": self.token, "distinct_id": distinct_id}
            ) as response:
                body = await response.json()
        return body["flags"][key]["value"]

    async def get_feature_flag(self, args):
        if self.document is None and self.defect == "refetch_on_304":
            await self.load_definitions()
        flag = next((f for f in (self.document or {}).get("flags", []) if f["key"] == args["key"]), None)
        if flag is None:
            return None
        value = self.local_value(flag)
        locally_evaluated = value is not None
        if value is None:
            value = await self.remote_value(args["key"], args["distinct_id"])
        properties = {"$feature_flag": args["key"], "$feature_flag_response": value}
        properties["locally_evaluated"] = locally_evaluated
        if flag is not None and flag["filters"].get("version") == 2:
            properties["$feature_flag_config_version"] = 2
            if self.defect != "missing_reason_code":
                properties["$feature_flag_reason_code"] = "targeting_match"
            if self.defect == "v1_reason":
                properties["$feature_flag_reason"] = "Matched condition set 1"
        await self.capture(
            {"distinct_id": args["distinct_id"], "event": "$feature_flag_called", "properties": properties}
        )
        return value


class LocalV2Host(AIHost):
    def __init__(self, contracts, **options):
        options.setdefault("sdk_capabilities", ("feature_flags_local_evaluation_v2",))
        super().__init__(contracts, **options)
        self.profile["id"] = "controlled-local-evaluation-v2"
        self.profile["products"] = ["flags"]
        self.profile["module"]["entry"] = "tests.v2_local_evaluation_v2_host.LocalV2Engine"
        self.routes = ["/setup", "/get_feature_flag", "/reload_feature_flags", "/flush"]
        self.engines = []

    def outcome(self, call, result):
        if call["route"] == "/get_feature_flag":
            return {"kind": "value", "value": result}
        return super().outcome(call, result)

    async def invoke(self, fixture, call):
        args, route = call["args"], call["route"]
        if route == "/setup":
            assert fixture.storage_prepared and fixture.engine is None
            fixture.engine = LocalV2Engine(
                fixture.storage, args["config"]["host"], args["config"], "legacy", fixture.defect, args["project_token"]
            )
            self.engines.append(fixture.engine)
            return await fixture.engine.setup()
        if route == "/reload_feature_flags":
            return await fixture.engine.reload_feature_flags()
        if route == "/get_feature_flag":
            return await fixture.engine.get_feature_flag(args)
        return await super().invoke(fixture, call)
