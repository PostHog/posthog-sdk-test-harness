"""Optional real-SDK smoke test for the known-absent suite; no installed SDK or production adapter is changed.

Run with POSTHOG_LOCAL_EVALUATION_SDK_PATH pointing at a posthog-python checkout that supports
known-absent property keys, and PYTHONDONTWRITEBYTECODE=1. Requires that SDK's runtime dependencies.
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

KNOWN_ABSENT_FIELDS = ("person_property_keys_known_absent", "group_property_keys_known_absent")


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


CAPABILITIES = ["feature_flags_local_evaluation_v1", "feature_flags_known_absent_v1"]
GROUP_KEY_CAPABILITIES = ["feature_flags_unusable_group_key_v1", "feature_flags_json_group_keys_v1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capabilities,expected_total",
    [(CAPABILITIES, 12), (CAPABILITIES + GROUP_KEY_CAPABILITIES, 20)],
    ids=["known_absent", "with_unusable_group_keys"],
)
async def test_known_absent_suite_against_real_python_sdk(monkeypatch, capabilities, expected_total):
    monkeypatch.syspath_prepend(SDK_PATH)
    import posthog
    from posthog.client import Client

    assert Path(posthog.__file__).resolve().is_relative_to(Path(SDK_PATH).resolve())
    app = Flask("temporary-real-python-known-absent-adapter")
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
            **{field: data[field] for field in KNOWN_ABSENT_FIELDS if field in data},
        )
        if local:
            return jsonify(success=value is not None, value=value, locally_evaluated=value is not None)
        return jsonify(success=True, value=value)

    mock = MockServer()
    suite = ContractTestSuite("feature_flags_known_absent", ContractExecutor())
    with live_server(mock.app) as mock_url, live_server(app) as adapter_url:
        ctx = HarnessContext(SDKAdapterClient(adapter_url), mock.state, mock_url)
        try:
            result = await suite.run(ctx, capabilities=capabilities)
            assert result.total == expected_total
            assert result.failed == 0, [(r.name, r.message) for r in result.results if not r.passed]
        finally:
            await ctx.sdk_adapter.reset()
