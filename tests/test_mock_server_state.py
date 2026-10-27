"""Tests for MockServerState, including partitioned (parallel) mode."""

import json

import pytest

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


V1_ANALYTICS = "/i/v1/analytics/events"
V1_AI = "/i/v1/ai/events"
UUID_A = "0198c0de-0000-7000-8000-00000000000a"
UUID_B = "0198c0de-0000-7000-8000-00000000000b"


def _v1_body(*events: tuple[str, str]) -> bytes:
    """Build a capture v1 body from (uuid, event name) pairs."""
    batch = [{"uuid": uuid, "event": name, "distinct_id": "user1"} for uuid, name in events]
    return json.dumps({"created_at": "2026-10-27T00:00:00Z", "batch": batch}).encode()


def _v1_results(state: MockServerState, body: bytes, path: str = V1_ANALYTICS) -> dict:
    recorded = state.record_request("POST", path, _make_headers(), {}, body)
    assert recorded.response_status == 200
    return json.loads(recorded.response_body)["results"]


class TestV1DuplicateUuids:
    """The mock answers repeated uuids the way capture does: keep the first copy, count the rest."""

    @pytest.mark.parametrize("path", [V1_ANALYTICS, V1_AI])
    def test_default_response_counts_dropped_copies(self, path: str) -> None:
        state = MockServerState()
        body = _v1_body((UUID_A, "first"), (UUID_B, "other"), (UUID_A, "second"), (UUID_A.upper(), "third"))

        assert _v1_results(state, body, path) == {
            UUID_A: {"result": "ok", "duplicates_dropped": 2},
            UUID_B: {"result": "ok"},
        }

    def test_configured_results_apply_to_the_first_copy(self) -> None:
        state = MockServerState()
        state.set_response_queue([MockResponse(v1_event_results=["retry", "drop", "ok"])])
        recorded = state.record_request(
            "POST", V1_ANALYTICS, _make_headers(), {}, _v1_body((UUID_A, "first"), (UUID_A, "second"), (UUID_B, "b"))
        )

        assert json.loads(recorded.response_body)["results"] == {
            UUID_A: {"result": "retry", "details": "not_persisted", "duplicates_dropped": 1},
            UUID_B: {"result": "ok"},
        }
        assert recorded.response_headers["Retry-After"] == "1"


    def test_wildcard_result_applies_to_every_event(self) -> None:
        state = MockServerState()
        entry = {"result": "drop", "details": "misrouted_event"}
        state.set_response_queue([MockResponse(v1_event_results={"*": entry})])

        assert _v1_results(state, _v1_body((UUID_A, "a"), (UUID_B, "b")), V1_AI) == {UUID_A: entry, UUID_B: entry}


class TestV1ResultShorthands:
    @pytest.mark.parametrize(
        "shorthand,details",
        [
            ("ok", None),
            ("retry", "not_persisted"),
            ("drop", "invalid_event"),
            ("warning", "person_processing_disabled"),
            ("limited", "person_processing_disabled"),
        ],
    )
    def test_shorthand_details(self, shorthand: str, details: str | None) -> None:
        state = MockServerState()
        state.set_response_queue([MockResponse(v1_event_results=[shorthand])])

        entry = _v1_results(state, _v1_body((UUID_A, "e")))[UUID_A]

        assert entry["result"] == shorthand
        assert entry.get("details") == details


class TestPathScopedResponses:
    def test_scoped_response_waits_for_its_path(self) -> None:
        state = MockServerState()
        state.set_response_queue(
            [MockResponse(status_code=503, path=V1_AI), MockResponse(status_code=400, path=V1_AI)]
        )
        body = _v1_body((UUID_A, "e"))

        analytics = state.record_request("POST", V1_ANALYTICS, _make_headers(), {}, body)
        first_ai = state.record_request("POST", V1_AI + "/", _make_headers(), {}, body)
        second_ai = state.record_request("POST", V1_AI, _make_headers(), {}, body)
        third_ai = state.record_request("POST", V1_AI, _make_headers(), {}, body)

        assert [analytics.response_status, first_ai.response_status, second_ai.response_status] == [200, 503, 400]
        assert third_ai.response_status == 200

    def test_unscoped_responses_keep_fifo_order_across_paths(self) -> None:
        state = MockServerState()
        state.set_response_queue(
            [MockResponse(status_code=500), MockResponse(status_code=503, path=V1_AI), MockResponse(status_code=504)]
        )
        body = _v1_body((UUID_A, "e"))

        statuses = [
            state.record_request("POST", path, _make_headers(), {}, body).response_status
            for path in (V1_ANALYTICS, V1_ANALYTICS, V1_AI, V1_ANALYTICS)
        ]

        assert statuses == [500, 504, 503, 200]
