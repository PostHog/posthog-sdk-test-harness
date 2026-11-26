"""Optional real-SDK smoke test for the unsupported filter types suite; no installed SDK or production adapter changes.

Run with POSTHOG_LOCAL_EVALUATION_SDK_PATH pointing at a posthog-python checkout that treats unsupported
filter types as inconclusive, and PYTHONDONTWRITEBYTECODE=1. Requires that SDK's runtime dependencies.
The case that also needs known-absent property keys doesn't run.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.context import TestContext as HarnessContext
from posthog_test_harness.tests.suites.contract_suite import ContractTestSuite

SDK_PATH = os.environ.get("POSTHOG_LOCAL_EVALUATION_SDK_PATH")
pytestmark = pytest.mark.skipif(not SDK_PATH, reason="Set POSTHOG_LOCAL_EVALUATION_SDK_PATH for a real Python SDK")


@contextmanager
def live_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.asyncio
async def test_unsupported_filter_types_suite_against_real_python_sdk(monkeypatch):
    monkeypatch.syspath_prepend(SDK_PATH)
    import posthog
    from posthog.client import Client

    assert Path(posthog.__file__).resolve().is_relative_to(Path(SDK_PATH).resolve())
    app = Flask("temporary-real-python-unsupported-filter-types-adapter")
    sdk = None
    # Unresolved reporting is optional; check for the snapshot API itself.
    try:
        from posthog.feature_flag_evaluations import FeatureFlagEvaluations
    except ImportError:
        FeatureFlagEvaluations = None
    reports_reasons = hasattr(Client, "evaluate_flags") and hasattr(FeatureFlagEvaluations, "unresolved_flags")

    @app.post("/reset")
    def reset():
        nonlocal sdk
        if sdk is not None:
            sdk.shutdown()
        sdk = None
        return jsonify(success=True)

    @app.post("/init")
    def init():
        nonlocal sdk
        data = request.get_json()
        sdk = Client(
            data["api_key"],
            host=data["host"],
            secret_key=data["personal_api_key"],
            # Disable background polling only: use the explicit reload barrier for deterministic snapshots.
            enable_local_evaluation=False,
        )
        return jsonify(success=True)

    @app.post("/reload_feature_flag_definitions")
    def reload_definitions():
        sdk.load_feature_flags()
        return jsonify(success=True, ready=sdk.feature_flag_definitions() is not None)

    @app.post("/get_feature_flag")
    def get_feature_flag():
        data = request.get_json()
        local = bool(data.get("only_evaluate_locally"))
        fallback = bool(data.get("allow_remote_fallback"))
        assert local != fallback, "the suite uses exactly one of only_evaluate_locally and allow_remote_fallback"
        assert not data.get("force_remote")
        value = sdk.get_feature_flag(
            data["key"],
            data["distinct_id"],
            person_properties=data.get("person_properties"),
            groups=data.get("groups"),
            group_properties=data.get("group_properties"),
            only_evaluate_locally=local,
            send_feature_flag_events=False,
        )
        if local:
            if value is not None:
                return jsonify(success=True, value=value, locally_evaluated=True)
            if not reports_reasons:
                return jsonify(success=False, value=None)
            snapshot = sdk.evaluate_flags(
                data["distinct_id"],
                person_properties=data.get("person_properties"),
                groups=data.get("groups"),
                group_properties=data.get("group_properties"),
                only_evaluate_locally=True,
                flag_keys=[data["key"]],
            )
            reason = snapshot.unresolved_flags.get(data["key"])
            return jsonify(success=False, value=None, unresolved_reason=reason.value if reason else None)
        return jsonify(success=True, value=value)

    mock = MockServer()
    suite = ContractTestSuite("feature_flags_unsupported_filter_types", ContractExecutor())
    with live_server(mock.app) as mock_url, live_server(app) as adapter_url:
        ctx = HarnessContext(SDKAdapterClient(adapter_url), mock.state, mock_url)
        try:
            result = await suite.run(
                ctx,
                capabilities=[
                    "feature_flags_local_evaluation_v1",
                    "feature_flags_unsupported_filter_types_v1",
                    *(["feature_flags_unresolved_reasons_v1"] if reports_reasons else []),
                ],
            )
            assert result.total == (17 if reports_reasons else 14)
            assert result.failed == 0, [(r.name, r.message) for r in result.results if not r.passed]
        finally:
            await ctx.sdk_adapter.reset()
