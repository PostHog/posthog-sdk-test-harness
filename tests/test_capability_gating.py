"""Tests for capability-based test gating in ContractTestSuite.collect_tests().

Covers the opt-out `incompatible_capabilities` field used to exclude server-only
tests from mobile SDK adapters without affecting any other adapter.
"""

from typing import Any, Dict, List, Optional

import pytest

from posthog_test_harness.tests.suites import ContractTestSuite


class _StubExecutor:
    """Minimal ContractExecutor stand-in exposing a single suite definition."""

    def __init__(self, suite_def: Dict[str, Any]) -> None:
        self._suite_def = suite_def

    def get_test_suites(self) -> Dict[str, Any]:
        return {"feature_flags": self._suite_def}


def _suite(tests: List[Dict[str, Any]]) -> ContractTestSuite:
    suite_def = {"categories": {"request_payload": {"tests": tests}}}
    return ContractTestSuite("feature_flags", _StubExecutor(suite_def))  # type: ignore[arg-type]


def _names(suite: ContractTestSuite, **kwargs: Any) -> List[str]:
    return [name for name, _ in suite.collect_tests(**kwargs)]


UNIVERSAL = {"name": "universal", "sdk_types": ["server"], "steps": []}
SERVER_ONLY = {
    "name": "server_only",
    "sdk_types": ["server"],
    "incompatible_capabilities": ["mobile_flag_eval"],
    "steps": [],
}


@pytest.mark.parametrize(
    "capabilities,expected_names",
    [
        # Adapter declares the incompatible capability → server-only test is skipped.
        (["capture_v0", "mobile_flag_eval"], ["request_payload.universal"]),
        # Backwards compatibility: adapter without the capability still runs the test.
        (["capture_v0"], ["request_payload.universal", "request_payload.server_only"]),
        # No capabilities declared at all → test still runs (backwards compatible).
        (None, ["request_payload.universal", "request_payload.server_only"]),
    ],
)
def test_incompatible_capability_gating(
    capabilities: Optional[List[str]],
    expected_names: List[str],
) -> None:
    suite = _suite([UNIVERSAL, SERVER_ONLY])
    kwargs: Dict[str, Any] = {"sdk_type": "server"}
    if capabilities is not None:
        kwargs["capabilities"] = capabilities
    assert _names(suite, **kwargs) == expected_names
