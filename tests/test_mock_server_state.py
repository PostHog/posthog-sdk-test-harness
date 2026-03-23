"""Tests for MockServerState, including partitioned (parallel) mode."""

import json

from posthog_test_harness.mock_server.state import TEST_ID_HEADER, MockServerState
from posthog_test_harness.types import MockResponse


def _make_headers(test_id: str | None = None) -> dict[str, str]:
    """Build a headers dict, optionally including X-Test-Id."""
    headers: dict[str, str] = {"content-type": "application/json"}
    if test_id is not None:
        headers[TEST_ID_HEADER] = test_id
    return headers


def _make_body(event_name: str = "test_event", distinct_id: str = "user1") -> bytes:
    """Build a JSON batch body."""
    return json.dumps(
        {"api_key": "phc_test", "batch": [{"event": event_name, "distinct_id": distinct_id}]}
    ).encode()


class TestGlobalModeUnchanged:
    """Verify that when test_id is None, behavior is identical to pre-partitioning."""

    def test_record_and_get_requests(self) -> None:
        state = MockServerState()
        state.record_request("POST", "/batch", _make_headers(), {}, _make_body())

        requests = state.get_requests()
        assert len(requests) == 1
        assert requests[0].method == "POST"
        assert requests[0].parsed_events is not None
        assert requests[0].parsed_events[0]["event"] == "test_event"

    def test_response_queue(self) -> None:
        state = MockServerState()
        state.set_response_queue([MockResponse(status_code=503)])

        recorded = state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        assert recorded.response_status == 503

        # Queue exhausted, should use default (200)
        recorded2 = state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        assert recorded2.response_status == 200

    def test_default_response(self) -> None:
        state = MockServerState()
        state.set_default_response(MockResponse(status_code=429))

        recorded = state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        assert recorded.response_status == 429

    def test_clear_requests(self) -> None:
        state = MockServerState()
        state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        assert len(state.get_requests()) == 1

        state.clear_requests()
        assert len(state.get_requests()) == 0

    def test_reset(self) -> None:
        state = MockServerState()
        state.set_response_queue([MockResponse(status_code=503)])
        state.record_request("POST", "/batch", _make_headers(), {}, _make_body())

        state.reset()
        assert len(state.get_requests()) == 0

        # Default response should be restored to 200
        recorded = state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        assert recorded.response_status == 200


class TestPartitionedRequests:
    """Verify that requests are isolated between partitions."""

    def test_requests_isolated_by_test_id(self) -> None:
        state = MockServerState()

        state.record_request("POST", "/batch", _make_headers("test-a"), {}, _make_body("event_a"))
        state.record_request("POST", "/batch", _make_headers("test-b"), {}, _make_body("event_b"))

        requests_a = state.get_requests(test_id="test-a")
        requests_b = state.get_requests(test_id="test-b")

        assert len(requests_a) == 1
        assert requests_a[0].parsed_events[0]["event"] == "event_a"

        assert len(requests_b) == 1
        assert requests_b[0].parsed_events[0]["event"] == "event_b"

    def test_partitioned_does_not_affect_global(self) -> None:
        state = MockServerState()

        state.record_request("POST", "/batch", _make_headers("test-a"), {}, _make_body("event_a"))
        state.record_request("POST", "/batch", _make_headers(), {}, _make_body("event_global"))

        assert len(state.get_requests()) == 1
        assert state.get_requests()[0].parsed_events[0]["event"] == "event_global"

        assert len(state.get_requests(test_id="test-a")) == 1

    def test_get_requests_unknown_partition_returns_empty(self) -> None:
        state = MockServerState()
        assert state.get_requests(test_id="nonexistent") == []


class TestPartitionedResponseQueues:
    """Verify that response queues are isolated between partitions."""

    def test_response_queues_isolated(self) -> None:
        state = MockServerState()

        state.set_response_queue([MockResponse(status_code=503)], test_id="test-a")
        state.set_response_queue([MockResponse(status_code=429)], test_id="test-b")

        rec_a = state.record_request("POST", "/batch", _make_headers("test-a"), {}, _make_body())
        rec_b = state.record_request("POST", "/batch", _make_headers("test-b"), {}, _make_body())

        assert rec_a.response_status == 503
        assert rec_b.response_status == 429

    def test_partition_queue_does_not_affect_global(self) -> None:
        state = MockServerState()

        state.set_response_queue([MockResponse(status_code=503)], test_id="test-a")

        # Global request should get default 200
        rec_global = state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        assert rec_global.response_status == 200

    def test_partition_default_response(self) -> None:
        state = MockServerState()

        state.set_default_response(MockResponse(status_code=429), test_id="test-a")

        rec_a = state.record_request("POST", "/batch", _make_headers("test-a"), {}, _make_body())
        rec_global = state.record_request("POST", "/batch", _make_headers(), {}, _make_body())

        assert rec_a.response_status == 429
        assert rec_global.response_status == 200


class TestPartitionedReset:
    """Verify that reset only affects the targeted partition."""

    def test_reset_partition_only(self) -> None:
        state = MockServerState()

        state.record_request("POST", "/batch", _make_headers("test-a"), {}, _make_body())
        state.record_request("POST", "/batch", _make_headers("test-b"), {}, _make_body())

        state.reset(test_id="test-a")

        assert len(state.get_requests(test_id="test-a")) == 0
        assert len(state.get_requests(test_id="test-b")) == 1

    def test_reset_partition_does_not_affect_global(self) -> None:
        state = MockServerState()

        state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        state.record_request("POST", "/batch", _make_headers("test-a"), {}, _make_body())

        state.reset(test_id="test-a")

        assert len(state.get_requests()) == 1
        assert len(state.get_requests(test_id="test-a")) == 0

    def test_global_reset_does_not_affect_partitions(self) -> None:
        state = MockServerState()

        state.record_request("POST", "/batch", _make_headers(), {}, _make_body())
        state.record_request("POST", "/batch", _make_headers("test-a"), {}, _make_body())

        state.reset()

        assert len(state.get_requests()) == 0
        assert len(state.get_requests(test_id="test-a")) == 1

    def test_reset_clears_response_queue_for_partition(self) -> None:
        state = MockServerState()

        state.set_response_queue([MockResponse(status_code=503)], test_id="test-a")
        state.reset(test_id="test-a")

        # After reset, partition should use fresh default (200)
        rec = state.record_request("POST", "/batch", _make_headers("test-a"), {}, _make_body())
        assert rec.response_status == 200


class TestHeaderExtraction:
    """Verify that test_id is correctly extracted from X-Test-Id header."""

    def test_header_routes_to_partition(self) -> None:
        state = MockServerState()

        headers = {"content-type": "application/json", TEST_ID_HEADER: "my-test"}
        state.record_request("POST", "/batch", headers, {}, _make_body())

        assert len(state.get_requests()) == 0
        assert len(state.get_requests(test_id="my-test")) == 1

    def test_no_header_uses_global(self) -> None:
        state = MockServerState()

        headers = {"content-type": "application/json"}
        state.record_request("POST", "/batch", headers, {}, _make_body())

        assert len(state.get_requests()) == 1
        assert len(state.get_requests(test_id="any")) == 0
