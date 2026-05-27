"""Tests for capability-based test gating in ContractTestSuite.collect_tests().

Covers the opt-out `incompatible_capabilities` field used to exclude server-only
tests from mobile SDK adapters without affecting any other adapter.
"""

from typing import Any, Dict, List

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


def test_incompatible_capability_skips_test_when_adapter_declares_it() -> None:
    suite = _suite([UNIVERSAL, SERVER_ONLY])
    names = _names(suite, sdk_type="server", capabilities=["capture_v0", "mobile_flag_eval"])
    assert names == ["request_payload.universal"]


def test_incompatible_capability_runs_test_when_adapter_does_not_declare_it() -> None:
    # Backwards compatibility: an adapter that doesn't declare the capability
    # (e.g. every existing server SDK) still runs the test.
    suite = _suite([UNIVERSAL, SERVER_ONLY])
    names = _names(suite, sdk_type="server", capabilities=["capture_v0"])
    assert names == ["request_payload.universal", "request_payload.server_only"]


def test_incompatible_capability_runs_test_when_no_capabilities_declared() -> None:
    suite = _suite([UNIVERSAL, SERVER_ONLY])
    names = _names(suite, sdk_type="server")
    assert names == ["request_payload.universal", "request_payload.server_only"]
