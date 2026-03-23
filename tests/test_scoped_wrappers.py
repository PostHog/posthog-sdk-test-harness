"""Tests for scoped wrappers (ScopedMockServerState, ScopedSDKAdapterClient, TestContext)."""

import json

from posthog_test_harness.mock_server.scoped import ScopedMockServerState
from posthog_test_harness.mock_server.state import TEST_ID_HEADER, MockServerState
from posthog_test_harness.sdk_adapter.client import ScopedSDKAdapterClient, SDKAdapterClient
from posthog_test_harness.tests.context import TestContext
from posthog_test_harness.types import MockResponse


def _make_headers(test_id: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {"content-type": "application/json"}
    if test_id is not None:
        headers[TEST_ID_HEADER] = test_id
    return headers


def _make_body(event_name: str = "test_event") -> bytes:
    return json.dumps(
        {"api_key": "phc_test", "batch": [{"event": event_name, "distinct_id": "user1"}]}
    ).encode()


class TestScopedMockServerState:
    """Verify ScopedMockServerState delegates to the correct partition."""

    def test_get_requests_scoped(self) -> None:
        state = MockServerState()
        scoped = ScopedMockServerState(state, "test-1")

        # Record via raw state with matching header
        state.record_request("POST", "/batch", _make_headers("test-1"), {}, _make_body("ev1"))
        state.record_request("POST", "/batch", _make_headers("test-2"), {}, _make_body("ev2"))

        # Scoped should only see test-1
        requests = scoped.get_requests()
        assert len(requests) == 1
        assert requests[0].parsed_events[0]["event"] == "ev1"

    def test_set_response_queue_scoped(self) -> None:
        state = MockServerState()
        scoped = ScopedMockServerState(state, "test-1")

        scoped.set_response_queue([MockResponse(status_code=503)])

        # Request with test-1 header should get 503
        rec = state.record_request("POST", "/batch", _make_headers("test-1"), {}, _make_body())
        assert rec.response_status == 503

        # Global request should get 200
        rec_global = state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        assert rec_global.response_status == 200

    def test_reset_scoped(self) -> None:
        state = MockServerState()
        scoped_1 = ScopedMockServerState(state, "test-1")
        scoped_2 = ScopedMockServerState(state, "test-2")

        state.record_request("POST", "/batch", _make_headers("test-1"), {}, _make_body())
        state.record_request("POST", "/batch", _make_headers("test-2"), {}, _make_body())

        scoped_1.reset()

        assert len(scoped_1.get_requests()) == 0
        assert len(scoped_2.get_requests()) == 1

    def test_set_default_response_scoped(self) -> None:
        state = MockServerState()
        scoped = ScopedMockServerState(state, "test-1")

        scoped.set_default_response(MockResponse(status_code=429))

        rec = state.record_request("POST", "/batch", _make_headers("test-1"), {}, _make_body())
        assert rec.response_status == 429

    def test_clear_requests_scoped(self) -> None:
        state = MockServerState()
        scoped = ScopedMockServerState(state, "test-1")

        state.record_request("POST", "/batch", _make_headers("test-1"), {}, _make_body())
        assert len(scoped.get_requests()) == 1

        scoped.clear_requests()
        assert len(scoped.get_requests()) == 0


class TestScopedSDKAdapterClient:
    """Verify ScopedSDKAdapterClient builds URLs with test_id."""

    def test_url_includes_test_id(self) -> None:
        base = SDKAdapterClient("http://localhost:8080")

        url = base._url("/init", "test-42")
        assert url == "http://localhost:8080/init?test_id=test-42"

    def test_url_without_test_id(self) -> None:
        base = SDKAdapterClient("http://localhost:8080")

        url = base._url("/init")
        assert url == "http://localhost:8080/init"


class TestContextWithTestId:
    """Verify TestContext wraps adapter and mock_server when test_id is set."""

    def test_without_test_id_uses_raw(self) -> None:
        adapter = SDKAdapterClient("http://localhost:8080")
        state = MockServerState()

        ctx = TestContext(adapter, state, "http://localhost:8081")

        assert ctx.sdk_adapter is adapter
        assert ctx.mock_server is state
        assert ctx.test_id is None

    def test_with_test_id_wraps_adapter(self) -> None:
        adapter = SDKAdapterClient("http://localhost:8080")
        state = MockServerState()

        ctx = TestContext(adapter, state, "http://localhost:8081", test_id="test-1")

        assert isinstance(ctx.sdk_adapter, ScopedSDKAdapterClient)
        assert ctx.test_id == "test-1"

    def test_with_test_id_wraps_mock_server(self) -> None:
        adapter = SDKAdapterClient("http://localhost:8080")
        state = MockServerState()

        ctx = TestContext(adapter, state, "http://localhost:8081", test_id="test-1")

        assert isinstance(ctx.mock_server, ScopedMockServerState)
        assert ctx.test_id == "test-1"
