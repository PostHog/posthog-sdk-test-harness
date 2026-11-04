"""Controlled /flags?v=4 reader for the Gherkin read features, not an SDK adapter claim.

The engine fetches the served body and answers with the contract expectation of the
fixture case whose response it is, so the generated scenarios and their bindings
are checked against the same expectations as the YAML suite. Defects change one
answer the way a faulty SDK would.
"""

import json

import aiohttp

from posthog_test_harness import flags_v4
from tests.v2_remote_flags_host import RemoteFlagsEngine, RemoteFlagsHost

UNDEFINED = object()


def _canonical(body):
    return json.dumps(body, sort_keys=True)


def _expectations_by_response():
    """Some value corpus cases serve the same record; their expectations must agree."""
    expectations = {}
    for source in ("readers", "values"):
        for case_id in flags_v4.case_ids(source):
            expected = flags_v4.expectation(source, case_id)
            known = expectations.setdefault(_canonical(flags_v4.response(source, case_id)), expected)
            assert known == expected, f"Cases serving the same response expect different results: {case_id}"
    return expectations


EXPECTATIONS = _expectations_by_response()


class FlagsV4Engine(RemoteFlagsEngine):
    async def read(self, args):
        body = {"token": self.token, "distinct_id": args["distinct_id"], "flag_keys_to_evaluate": [args["key"]]}
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(f"{self.host}/flags?v=4", json=body) as response:
                data = await response.json(content_type=None)
        self.parsed_responses.append(data)
        # An unknown body (for example one with merged default fields) is an SDK throw.
        expected = EXPECTATIONS[_canonical(data)]
        assert expected["key"] == args["key"]
        return expected

    async def get_feature_flag(self, args):
        value = (await self.read(args))["legacy_value"]
        if self.defect == "numeric_boolean" and isinstance(value, bool):
            return int(value)
        if self.defect == "null_for_false" and value is False:
            return None
        if self.defect == "missing_as_false" and value is None:
            return False
        if self.defect == "missing_as_null" and value is None:
            return None
        return UNDEFINED if value is None else value

    async def get_feature_flag_payload(self, args):
        payload = (await self.read(args))["legacy_payload"]
        if self.defect == "serialized_payload" and payload is not None:
            return json.dumps(payload)
        if self.defect == "float_payload" and type(payload) is int:
            return float(payload)
        if self.defect == "payload_for_string" and payload is None:
            return "compact"
        if self.defect == "missing_as_null":
            return payload
        return UNDEFINED if payload is None else payload


class FlagsV4Host(RemoteFlagsHost):
    engine_type = FlagsV4Engine

    def __init__(self, contracts, *, sdk_capabilities=("flags_response_v4", "feature_flag_payloads"), **options):
        super().__init__(contracts, sdk_capabilities=sdk_capabilities, **options)
        self.profile["id"] = "controlled-flags-v4-reads"
        self.profile["module"]["entry"] = "tests.v2_flags_v4_host.FlagsV4Engine"
        self.routes.append("/get_feature_flag_payload")

    def outcome(self, call, result):
        if call["route"] in ("/get_feature_flag", "/get_feature_flag_payload"):
            return {"kind": "undefined"} if result is UNDEFINED else {"kind": "value", "value": result}
        return super().outcome(call, result)

    async def invoke(self, fixture, call):
        if call["route"] == "/get_feature_flag_payload":
            return await fixture.engine.get_feature_flag_payload(call["args"])
        return await super().invoke(fixture, call)
