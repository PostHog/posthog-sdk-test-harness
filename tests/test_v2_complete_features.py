"""Complete companion features exercised through the public HTTP adapter."""

import pytest

from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_ai_host import AIHost
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve
from tests.v2_legacy_capture_host import LegacyCaptureHost
from tests.v2_local_parity_host import LocalParityHost
from tests.v2_remote_flags_host import RemoteFlagsHost


@pytest.mark.parametrize(
    "feature,host_type",
    [
        ("migration/yaml-parity-v1/capture-ai.feature", AIHost),
        ("migration/yaml-parity-v1/capture-analytics-v1-batching.feature", AnalyticsWireHost),
        ("migration/yaml-parity-v1/capture-analytics-v1-outcomes.feature", AnalyticsWireHost),
        ("migration/yaml-parity-v1/capture-analytics-v1-retry.feature", AnalyticsWireHost),
        ("migration/yaml-parity-v1/capture-analytics-v1.feature", AnalyticsWireHost),
        ("migration/yaml-parity-v1/capture-legacy.feature", LegacyCaptureHost),
        ("migration/yaml-parity-v1/local-evaluation-v1.feature", LocalParityHost),
        ("migration/yaml-parity-v1/remote-flags-v1.feature", RemoteFlagsHost),
    ],
)
async def test_complete_feature_through_public_http(specs, feature, host_type):
    contracts = Contracts()
    async with serve(contracts, host_type=host_type) as (host, url):
        report, diagnostics = await run(contracts, specs, [feature], url, host.profile["id"], timeout_ms=60000)
    assert strict_exit_code(contracts, report) == 0, report
    assert all(row["result"]["status"] in ("passed", "not_selected") for row in report["results"])
    assert len(host.closed) == sum(row["result"]["executed"] for row in report["results"])
    assert len({d["mock_url"] for d in diagnostics["cases"]}) == len(diagnostics["cases"])
