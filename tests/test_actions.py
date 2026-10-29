"""Unit tests for new/changed assertion actions."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.actions import (
    AssertActionResultUuidMatchesEventAction,
    AssertBackoffImplementedAction,
    AssertBodyFieldAction,
    AssertDropReportedOrLoggedAction,
    AssertEventFieldIsRfc3339Action,
    AssertEventOptionAction,
    AssertEventPropertyAction,
    AssertEventsInBatchCountAction,
    AssertEventsPropertyValuesAction,
    AssertHeaderIsRfc3339Action,
    AssertLogOutputAction,
    AssertReportedDropAction,
    AssertRequestBodyMaxBytesAction,
    AssertRequestCountAction,
    AssertRequestReceivedBeforeAction,
    AssertUuidFormatAction,
    AssertV1BodyFormatAction,
    AssertV1CreatedAtRecentAction,
    _is_rfc3339,
)


def _ctx(requests, last_action_result=None):
    """Build a minimal context whose mock_server.get_requests() returns `requests`."""
    return SimpleNamespace(
        mock_server=SimpleNamespace(get_requests=lambda: requests),
        last_action_result=last_action_result,
    )


def _req(parsed_events=None, body=None, headers=None):
    return SimpleNamespace(
        parsed_events=parsed_events,
        body_decompressed=body,
        headers=headers or {},
    )


@pytest.mark.parametrize(
    "value",
    [
        "2025-01-02T03:04:05Z",
        "2025-01-02T03:04:05.123456789Z",
        "2025-01-02T03:04:05+00:00",
        "2025-01-02T03:04:05.1+00:00",
        "2024-02-29T23:59:59Z",
    ],
)
def test_is_rfc3339_accepts_canonical_utc_timestamps(value):
    assert _is_rfc3339(value)


@pytest.mark.parametrize(
    "value",
    [
        "2025-01-02T03:04:05",
        "2025-01-02T03:04:05+05:30",
        "2025-01-02T03:04:05-07:00",
        "2025-01-02T03:04:05-00:00",
        "2025-01-02 03:04:05Z",
        "2025-01-02t03:04:05Z",
        "2025-01-02T03:04:05z",
        "20250102T030405Z",
        "2025-01-02T03:04Z",
        "2025-01-02T03:04:05,123Z",
        "2025-01-02T03:04:05+0000",
        "2025-01-02T03:04:05+00",
        "2025-01-02T03:04:05. Z",
        "2025-02-29T03:04:05Z",
        "2025-13-02T03:04:05Z",
        "2025-01-02T24:04:05Z",
        "2025-01-02T03:60:05Z",
        "2025-01-02T03:04:60Z",
        "not-a-timestamp",
    ],
)
def test_is_rfc3339_rejects_noncanonical_non_utc_or_invalid_timestamps(value):
    assert not _is_rfc3339(value)


class TestUtcTimestampActions:
    @pytest.mark.asyncio
    async def test_header_rejects_nonzero_offset(self):
        request = _req(headers={"posthog-request-timestamp": "2025-01-02T08:34:05+05:30"})
        with pytest.raises(AssertionError):
            await AssertHeaderIsRfc3339Action().execute({"header": "PostHog-Request-Timestamp"}, _ctx([request]))

    @pytest.mark.asyncio
    async def test_v1_body_rejects_naive_created_at(self):
        body = json.dumps({"created_at": "2025-01-02T03:04:05", "batch": [{}]})
        with pytest.raises(AssertionError):
            await AssertV1BodyFormatAction().execute({}, _ctx([_req(body=body)]))

    @pytest.mark.asyncio
    async def test_v1_recent_created_at_rejects_non_string_value(self):
        body = json.dumps({"created_at": 1735787045, "batch": [{}]})
        with pytest.raises(AssertionError, match="Invalid UTC created_at"):
            await AssertV1CreatedAtRecentAction().execute({}, _ctx([_req(body=body)]))

    @pytest.mark.asyncio
    async def test_event_timestamp_rejects_nonzero_offset(self):
        request = _req(parsed_events=[{"timestamp": "2025-01-02T08:34:05+05:30"}])
        with pytest.raises(AssertionError):
            await AssertEventFieldIsRfc3339Action().execute({"field": "timestamp"}, _ctx([request]))

    @pytest.mark.asyncio
    async def test_event_timestamp_matches_expected_utc_instant(self):
        request = _req(parsed_events=[{"timestamp": "2025-01-02T03:04:05+00:00"}])
        await AssertEventFieldIsRfc3339Action().execute(
            {"field": "timestamp", "expected": "2025-01-02T03:04:05Z"},
            _ctx([request]),
        )

    @pytest.mark.asyncio
    async def test_event_timestamp_rejects_different_utc_instant(self):
        request = _req(parsed_events=[{"timestamp": "2025-01-02T03:04:06Z"}])
        with pytest.raises(AssertionError, match="instant"):
            await AssertEventFieldIsRfc3339Action().execute(
                {"field": "timestamp", "expected": "2025-01-02T03:04:05Z"},
                _ctx([request]),
            )

    @pytest.mark.asyncio
    async def test_event_timestamp_compares_sub_microsecond_precision(self):
        request = _req(parsed_events=[{"timestamp": "2025-01-02T03:04:05.123456789Z"}])
        with pytest.raises(AssertionError, match="instant"):
            await AssertEventFieldIsRfc3339Action().execute(
                {"field": "timestamp", "expected": "2025-01-02T03:04:05.123456788Z"},
                _ctx([request]),
            )


class TestAssertEventsInBatchCount:
    @pytest.mark.asyncio
    async def test_defaults_to_first_request(self):
        requests = [_req(parsed_events=[{}, {}, {}]), _req(parsed_events=[{}])]
        await AssertEventsInBatchCountAction().execute({"expected": 3}, _ctx(requests))

    @pytest.mark.asyncio
    async def test_negative_index_targets_last(self):
        requests = [_req(parsed_events=[{}, {}, {}]), _req(parsed_events=[{}])]
        await AssertEventsInBatchCountAction().execute(
            {"expected": 1, "request_index": -1}, _ctx(requests)
        )

    @pytest.mark.asyncio
    async def test_explicit_request_index(self):
        requests = [_req(parsed_events=[{}, {}, {}]), _req(parsed_events=[{}])]
        await AssertEventsInBatchCountAction().execute(
            {"expected": 3, "request_index": 0}, _ctx(requests)
        )

    @pytest.mark.asyncio
    async def test_mismatch_raises(self):
        requests = [_req(parsed_events=[{}])]
        with pytest.raises(AssertionError):
            await AssertEventsInBatchCountAction().execute({"expected": 2}, _ctx(requests))

    @pytest.mark.asyncio
    async def test_gte_operator(self):
        requests = [_req(parsed_events=[{}, {}, {}])]
        await AssertEventsInBatchCountAction().execute(
            {"expected": 2, "operator": "gte"}, _ctx(requests)
        )


class TestAssertEventOption:
    @pytest.mark.asyncio
    async def test_expected_value(self):
        requests = [_req(parsed_events=[{"options": {"cookieless_mode": True}}])]
        await AssertEventOptionAction().execute(
            {"option": "cookieless_mode", "expected": True}, _ctx(requests)
        )

    @pytest.mark.asyncio
    async def test_absent_with_empty_options(self):
        requests = [_req(parsed_events=[{"options": {}}])]
        await AssertEventOptionAction().execute(
            {"option": "cookieless_mode", "absent": True}, _ctx(requests)
        )

    @pytest.mark.asyncio
    async def test_absent_when_no_options_key(self):
        requests = [_req(parsed_events=[{}])]
        await AssertEventOptionAction().execute(
            {"option": "cookieless_mode", "absent": True}, _ctx(requests)
        )

    @pytest.mark.asyncio
    async def test_expected_mismatch_raises(self):
        requests = [_req(parsed_events=[{"options": {"process_person_profile": True}}])]
        with pytest.raises(AssertionError):
            await AssertEventOptionAction().execute(
                {"option": "process_person_profile", "expected": False}, _ctx(requests)
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sent,expected", [(False, 0.0), (True, 1), (0, False), (1.0, True)])
    async def test_bool_never_matches_number(self, sent, expected):
        requests = [_req(parsed_events=[{"options": {"disable_skew_correction": sent}}])]
        with pytest.raises(AssertionError):
            await AssertEventOptionAction().execute(
                {"option": "disable_skew_correction", "expected": expected}, _ctx(requests)
            )

    @pytest.mark.asyncio
    async def test_absent_raises_when_present(self):
        requests = [_req(parsed_events=[{"options": {"cookieless_mode": True}}])]
        with pytest.raises(AssertionError):
            await AssertEventOptionAction().execute(
                {"option": "cookieless_mode", "absent": True}, _ctx(requests)
            )


class TestAssertEventPropertyAbsent:
    @pytest.mark.asyncio
    async def test_passes_when_property_is_missing(self):
        requests = [_req(parsed_events=[{"properties": {"plan": "pro"}}])]
        await AssertEventPropertyAction().execute({"property": "$cookieless_mode", "absent": True}, _ctx(requests))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [True, None])
    async def test_raises_when_property_is_present(self, value):
        requests = [_req(parsed_events=[{"properties": {"$cookieless_mode": value}}])]
        with pytest.raises(AssertionError):
            await AssertEventPropertyAction().execute({"property": "$cookieless_mode", "absent": True}, _ctx(requests))


class TestAssertBodyField:
    @pytest.mark.asyncio
    async def test_present_with_value(self):
        body = json.dumps({"historical_migration": True, "batch": []})
        await AssertBodyFieldAction().execute(
            {"field": "historical_migration", "expected": True}, _ctx([_req(body=body)])
        )

    @pytest.mark.asyncio
    async def test_presence_only(self):
        body = json.dumps({"historical_migration": False, "batch": []})
        await AssertBodyFieldAction().execute(
            {"field": "historical_migration"}, _ctx([_req(body=body)])
        )

    @pytest.mark.asyncio
    async def test_missing_raises(self):
        body = json.dumps({"batch": []})
        with pytest.raises(AssertionError):
            await AssertBodyFieldAction().execute(
                {"field": "historical_migration"}, _ctx([_req(body=body)])
            )

    @pytest.mark.asyncio
    async def test_value_mismatch_raises(self):
        body = json.dumps({"historical_migration": False})
        with pytest.raises(AssertionError):
            await AssertBodyFieldAction().execute(
                {"field": "historical_migration", "expected": True}, _ctx([_req(body=body)])
            )


class TestAssertUuidFormat:
    @pytest.mark.asyncio
    async def test_valid_uuid_passes(self):
        requests = [_req(parsed_events=[{"uuid": "0198c0de-0000-7000-8000-000000000abc"}])]
        await AssertUuidFormatAction().execute({"field": "uuid"}, _ctx(requests))

    @pytest.mark.asyncio
    async def test_wrong_length_raises(self):
        requests = [_req(parsed_events=[{"uuid": "not-a-uuid"}])]
        with pytest.raises(AssertionError):
            await AssertUuidFormatAction().execute({"field": "uuid"}, _ctx(requests))

    @pytest.mark.asyncio
    async def test_right_shape_but_invalid_hex_raises(self):
        requests = [_req(parsed_events=[{"uuid": "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"}])]
        with pytest.raises(AssertionError):
            await AssertUuidFormatAction().execute({"field": "uuid"}, _ctx(requests))


class TestAssertActionResultUuidMatchesEvent:
    @pytest.mark.asyncio
    async def test_matching_uuids_pass(self):
        uuid = "0198c0de-0000-7000-8000-000000000abc"
        requests = [_req(parsed_events=[{"uuid": uuid}])]
        await AssertActionResultUuidMatchesEventAction().execute(
            {}, _ctx(requests, last_action_result={"success": True, "uuid": uuid})
        )

    @pytest.mark.asyncio
    async def test_mismatched_uuids_raise(self):
        requests = [_req(parsed_events=[{"uuid": "0198c0de-0000-7000-8000-000000000abc"}])]
        with pytest.raises(AssertionError):
            await AssertActionResultUuidMatchesEventAction().execute(
                {}, _ctx(requests, last_action_result={"success": True, "uuid": "other-uuid"})
            )

    @pytest.mark.asyncio
    async def test_result_missing_uuid_raises(self):
        requests = [_req(parsed_events=[{"uuid": "0198c0de-0000-7000-8000-000000000abc"}])]
        with pytest.raises(AssertionError):
            await AssertActionResultUuidMatchesEventAction().execute(
                {}, _ctx(requests, last_action_result={"success": True})
            )

    @pytest.mark.asyncio
    async def test_non_dict_result_raises(self):
        requests = [_req(parsed_events=[{"uuid": "0198c0de-0000-7000-8000-000000000abc"}])]
        with pytest.raises(AssertionError):
            await AssertActionResultUuidMatchesEventAction().execute(
                {}, _ctx(requests, last_action_result="not-a-dict")
            )


def _timed(path, timestamp_ms):
    return SimpleNamespace(path=path, timestamp_ms=timestamp_ms, parsed_events=[])


class TestAssertBackoffImplemented:
    @pytest.mark.parametrize(
        "times,ok",
        [
            ([0, 75, 230, 520], True),
            ([0, 69, 230, 520], False),
            ([0, 75, 210, 520], False),
            ([0, 75, 230, 500], False),
            ([0, 75, 230], False),
        ],
    )
    async def test_min_delays_bound_each_gap(self, times, ok):
        ctx = _ctx([_timed("/i/v1/analytics/events", t) for t in times])
        run = AssertBackoffImplementedAction().execute({"min_delays_ms": [70, 140, 280]}, ctx)
        if ok:
            await run
        else:
            with pytest.raises(AssertionError):
                await run


class TestPathFilteredAssertions:
    AI = "/i/v1/ai/events"
    ANALYTICS = "/i/v1/analytics/events"

    async def test_request_count_counts_only_the_path(self):
        ctx = _ctx([_timed(self.AI, 0), _timed(self.ANALYTICS + "/", 5), _timed(self.AI, 1000)])
        await AssertRequestCountAction().execute({"path": self.ANALYTICS, "expected": 1}, ctx)
        with pytest.raises(AssertionError):
            await AssertRequestCountAction().execute({"path": self.AI, "expected": 1}, ctx)

    @pytest.mark.parametrize("analytics_ms,ok", [(5, True), (1000, True), (1001, False)])
    async def test_received_before_compares_first_request_with_indexed_other(self, analytics_ms, ok):
        ctx = _ctx([_timed(self.AI, 0), _timed(self.AI, 1000), _timed(self.ANALYTICS, analytics_ms)])
        run = AssertRequestReceivedBeforeAction().execute(
            {"path": self.ANALYTICS, "other_path": self.AI, "other_index": 1}, ctx
        )
        if ok:
            await run
        else:
            with pytest.raises(AssertionError):
                await run

    async def test_received_before_fails_without_enough_other_requests(self):
        ctx = _ctx([_timed(self.AI, 0), _timed(self.ANALYTICS, 5)])
        with pytest.raises(AssertionError, match="at least 2 requests"):
            await AssertRequestReceivedBeforeAction().execute(
                {"path": self.ANALYTICS, "other_path": self.AI, "other_index": 1}, ctx
            )


def _state_ctx(**state):
    async def get_state():
        return SimpleNamespace(**{"reported_drops": [], "log_lines": [], **state})

    return SimpleNamespace(sdk_adapter=SimpleNamespace(get_state=get_state))


class TestAdapterDiagnostics:
    DROPS = [{"uuid": "u1", "event": "first", "details": "invalid_options"}, {"uuid": "u2", "event": "second"}]

    @pytest.mark.parametrize(
        "params,ok",
        [
            ({"event": "first", "details": "invalid_options"}, True),
            ({"event": "second"}, True),
            ({"event": "first", "details": "duplicate_event_uuid"}, False),
            ({"uuid": "u3"}, False),
        ],
    )
    async def test_reported_drop_matches_every_given_field(self, params, ok):
        run = AssertReportedDropAction().execute(params, _state_ctx(reported_drops=self.DROPS))
        if ok:
            await run
        else:
            with pytest.raises(AssertionError):
                await run

    @pytest.mark.parametrize(
        "params,ok",
        [
            ({"contains": ["$ai_trace"], "not_contains": ["SECRET"]}, True),
            ({"contains": ["missing"]}, False),
            ({"not_contains": ["9437184"]}, False),
        ],
    )
    async def test_log_output(self, params, ok):
        ctx = _state_ctx(log_lines=["dropping $ai_trace", "event is 9437184 bytes"])
        run = AssertLogOutputAction().execute(params, ctx)
        if ok:
            await run
        else:
            with pytest.raises(AssertionError):
                await run


class TestPayloadAssertions:
    AI = "/i/v1/ai/events"

    def _ctx(self, *bodies):
        requests = [
            SimpleNamespace(
                path=self.AI,
                body_decompressed=json.dumps({"batch": events}),
                parsed_events=events,
                timestamp_ms=0,
            )
            for events in bodies
        ]
        return _ctx(requests)

    async def test_body_size_is_checked_per_request(self):
        small = [{"properties": {"case": "a"}}]
        ctx = self._ctx(small, [{"properties": {"case": "b" * 100}}])
        await AssertRequestBodyMaxBytesAction().execute({"path": self.AI, "max_bytes": 200}, ctx)
        with pytest.raises(AssertionError, match="Request 1"):
            await AssertRequestBodyMaxBytesAction().execute({"path": self.AI, "max_bytes": 100}, ctx)

    @pytest.mark.parametrize(
        "expected,any_order,ok",
        [
            (["g0", "g1", "g2"], False, True),
            (["g0", "g2", "g1"], False, False),
            (["g0"], False, False),
            (["g2", "g0", "g1"], True, True),
            (["g0", "g1"], True, False),
            (["g0", "g1", "g1"], True, False),
        ],
    )
    async def test_property_values_follow_send_order_unless_any_order(self, expected, any_order, ok):
        first, rest = [{"properties": {"case": "g0"}}], [{"properties": {"case": c}} for c in ("g1", "g2")]
        params = {"path": self.AI, "property": "case", "expected": expected, "any_order": any_order}
        run = AssertEventsPropertyValuesAction().execute(params, self._ctx(first, rest))
        if ok:
            await run
        else:
            with pytest.raises(AssertionError):
                await run


@pytest.mark.parametrize(
    "drops,lines,ok",
    [
        ([{"event": "$ai_trace"}], [], True),
        ([], ["dropping $ai_trace (9437184 bytes)"], True),
        ([{"event": "$ai_generation"}], ["capture-ai: message exceeds maximum size"], False),
    ],
)
async def test_drop_reported_or_logged_accepts_either_surface(drops, lines, ok):
    run = AssertDropReportedOrLoggedAction().execute(
        {"event": "$ai_trace"}, _state_ctx(reported_drops=drops, log_lines=lines)
    )
    if ok:
        await run
    else:
        with pytest.raises(AssertionError):
            await run
