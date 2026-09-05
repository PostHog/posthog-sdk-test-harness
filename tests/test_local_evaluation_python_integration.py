"""Optional real-SDK smoke test; no installed SDK or production adapter is changed.

Run with POSTHOG_LOCAL_EVALUATION_SDK_PATH pointing at a patched Python checkout
and PYTHONDONTWRITEBYTECODE=1. Requires that SDK's runtime dependencies.
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
pytestmark = pytest.mark.skipif(
    not SDK_PATH, reason="Set POSTHOG_LOCAL_EVALUATION_SDK_PATH for real patched Python SDK"
)


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
async def test_versioned_local_fixtures_against_real_python_sdk(monkeypatch):
    monkeypatch.syspath_prepend(SDK_PATH)
    import posthog
    from posthog.client import Client

    assert Path(posthog.__file__).resolve().is_relative_to(Path(SDK_PATH).resolve())
    app = Flask("temporary-real-python-local-adapter")
    sdk = None

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
            # Disable background polling only: use the public explicit reload
            # barrier for deterministic snapshots, with real HTTP and matching.
            enable_local_evaluation=False,
            flag_fallback_cache_url="memory://local/?ttl=300&size=10000",
        )
        return jsonify(success=True)

    @app.post("/reload_feature_flag_definitions")
    def reload_definitions():
        sdk.load_feature_flags()
        return jsonify(success=True, ready=sdk.feature_flag_definitions() is not None)

    @app.post("/get_feature_flag")
    def get_feature_flag():
        data = request.get_json()
        assert data["only_evaluate_locally"] is True
        assert not data.get("force_remote")
        value = sdk.get_feature_flag(
            data["key"],
            data["distinct_id"],
            person_properties=data.get("person_properties"),
            groups=data.get("groups"),
            group_properties=data.get("group_properties"),
            only_evaluate_locally=True,
            send_feature_flag_events=False,
        )
        # Public local-only API returns None on inconclusive, never a remote value.
        return jsonify(success=value is not None, value=value, locally_evaluated=value is not None)

    mock = MockServer()
    suite = ContractTestSuite("feature_flags_local_evaluation", ContractExecutor())
    with live_server(mock.app) as mock_url, live_server(app) as adapter_url:
        ctx = HarnessContext(SDKAdapterClient(adapter_url), mock.state, mock_url)
        try:
            result = await suite.run(ctx, capabilities=["feature_flags_local_evaluation_v1"])
            assert result.total == 4
            assert result.failed == 0, [(r.name, r.message) for r in result.results if not r.passed]
            assert mock.state.get_requests() == []
            assert len(mock.state.get_definition_requests()) == 5  # final version-only reload test
        finally:
            await ctx.sdk_adapter.reset()
