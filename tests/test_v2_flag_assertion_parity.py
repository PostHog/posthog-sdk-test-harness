"""Migrated getter actions preserve outcomes without adding result assertions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from posthog_test_harness.v2.steps import Context
from tests.test_v2_remote_flags import FEATURE, IDS, SPECS
from tests.v2_flush_host import serve
from tests.v2_remote_flags_host import RemoteFlagsHost


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


class UndefinedGetterHost(RemoteFlagsHost):
    def outcome(self, call, result):
        if call["route"] == "/get_feature_flag":
            return {"kind": "undefined"}
        return super().outcome(call, result)


@pytest.mark.parametrize(
    "index,defect,status,code,getters",
    [
        (1, None, "passed", None, 1),
        (12, None, "passed", None, 2),
        (13, None, "failed_assertion", "flag_value", 1),
        (1, "wrong_version", "failed_assertion", "flag_request_query", 1),
    ],
)
async def test_undefined_preserves_original_assertions(contracts, index, defect, status, code, getters):
    async with serve(contracts, host_type=UndefinedGetterHost, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    result = report["results"][index]["result"]
    assert result["status"] == status
    if code:
        assert result["failure"]["code"] == code
    calls = [c for c in report["calls"] if c["route"] == "/get_feature_flag"]
    assert len(calls) == getters
    assert all(c["completion"] == {"kind": "sdk", "outcome": {"kind": "undefined"}} for c in calls)
    assert sum("/flags" in r["path"] for d in diagnostics["cases"] for r in d["wire_requests"]) == getters
    assert strict_exit_code(contracts, report) == (0 if status == "passed" else 1)


class ThrowingGetterHost(RemoteFlagsHost):
    async def invoke(self, fixture, call):
        result = await super().invoke(fixture, call)
        if call["route"] == "/get_feature_flag":
            raise RuntimeError("Native getter failure")
        return result


async def test_native_throw_still_fails_getter_action(contracts):
    async with serve(contracts, host_type=ThrowingGetterHost) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[1]])
    assert report["results"][1]["result"]["failure"]["code"] == "flag_getter_thrown"
    assert report["calls"][-1]["completion"]["outcome"]["kind"] == "thrown"
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("check_result", [True, False])
async def test_context_preserves_harness_failures_with_or_without_result_check(contracts, check_result):
    step = SimpleNamespace(source={"path": "test.feature", "line": 1})
    context = Context(
        SimpleNamespace(contracts=contracts), SimpleNamespace(steps=[step]), {}, None, 5000, {"invocations": []}
    )
    context.fixture = SimpleNamespace(
        id="fixture",
        invoke=AsyncMock(
            return_value={
                "completion": {
                    "kind": "harness",
                    "failure": {
                        "kind": "unsupported_binding",
                        "code": "missing-parameter",
                        "message": "Unsupported input",
                    },
                }
            }
        ),
    )
    with pytest.raises(BoundaryError) as caught:
        await context.call("/get_feature_flag", {}, check_result=check_result)
    assert caught.value.kind == "unsupported_binding"
    assert caught.value.code == "missing-parameter"


async def test_context_preserves_undefined_without_imposing_sdk_assertions(contracts):
    step = SimpleNamespace(source={"path": "test.feature", "line": 1})
    context = Context(
        SimpleNamespace(contracts=contracts), SimpleNamespace(steps=[step]), {}, None, 5000, {"invocations": []}
    )
    context.fixture = SimpleNamespace(
        id="fixture", invoke=AsyncMock(return_value={"completion": {"kind": "sdk", "outcome": {"kind": "undefined"}}})
    )
    assert await context.call("/get_feature_flag", {}) == {"kind": "undefined"}
