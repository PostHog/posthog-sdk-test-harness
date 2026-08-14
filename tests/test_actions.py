"""Unit tests for new/changed assertion actions."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.actions import (
    AssertActionResultUuidMatchesEventAction,
    AssertBodyFieldAction,
    AssertEventFieldIsRfc3339Action,
    AssertEventOptionAction,
    AssertEventsInBatchCountAction,
    AssertHeaderIsRfc3339Action,
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
    async def test_absent_raises_when_present(self):
        requests = [_req(parsed_events=[{"options": {"cookieless_mode": True}}])]
        with pytest.raises(AssertionError):
            await AssertEventOptionAction().execute(
                {"option": "cookieless_mode", "absent": True}, _ctx(requests)
            )


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
