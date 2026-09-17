"""Migration flush completion is observed; subsequent source assertions decide delivery."""

import asyncio

import pytest

from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS
from tests.test_v2_legacy_capture import FEATURE, IDS
from tests.v2_flush_host import serve
from tests.v2_legacy_capture_host import LegacyCaptureHost


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


class RejectedFlushHost(LegacyCaptureHost):
    async def invoke(self, fixture, call):
        if call["route"] == "/flush":
            if fixture.defect == "hang_flush":
                await asyncio.Future()
            if fixture.defect != "fail_before_delivery":
                await super().invoke(fixture, call)
            raise RuntimeError("Native flush rejected")
        return await super().invoke(fixture, call)


@pytest.mark.parametrize(
    "defect,status,code",
    [
        (None, "passed", None),
        ("fail_before_delivery", "failed_assertion", "request_count"),
        ("hang_flush", "harness_error", "host_deadline"),
    ],
)
async def test_rejected_flush_preserves_request_assertions_and_harness_failures(contracts, defect, status, code):
    case_id = next(identity for identity in IDS if identity.endswith(":does_not_retry_on_400"))
    async with serve(contracts, host_type=RejectedFlushHost, defect=defect) as (host, url):
        report, diagnostics = await run(
            contracts,
            SPECS,
            [FEATURE],
            url,
            host.profile["id"],
            case_ids=[case_id],
            timeout_ms=100 if defect == "hang_flush" else 5000,
        )
    row = next(r["result"] for r in report["results"] if r["case_id"] == case_id)
    assert row["status"] == status
    if code:
        assert row["failure"]["code"] == code
    completion = next(c["completion"] for c in report["calls"] if c["route"] == "/flush")
    if defect == "hang_flush":
        assert completion["failure"]["kind"] == "timeout"
    else:
        assert completion["outcome"]["kind"] == "thrown"
        assert len(diagnostics["cases"][0]["wire_requests"]) == (0 if defect else 1)
    assert strict_exit_code(contracts, report) == (0 if status == "passed" else 1)
