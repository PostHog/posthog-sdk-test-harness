"""Unit tests for new/changed assertion actions."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.actions import (
    AssertBodyFieldAction,
    AssertEventOptionAction,
    AssertEventsInBatchCountAction,
)


def _ctx(requests):
    """Build a minimal context whose mock_server.get_requests() returns `requests`."""
    return SimpleNamespace(mock_server=SimpleNamespace(get_requests=lambda: requests))


def _req(parsed_events=None, body=None):
    return SimpleNamespace(parsed_events=parsed_events, body_decompressed=body)


class TestAssertEventsInBatchCount:
    @pytest.mark.asyncio
    async def test_defaults_to_last_request(self):
        # Original batch had 3 events; retried batch (last) had 1.
        requests = [_req(parsed_events=[{}, {}, {}]), _req(parsed_events=[{}])]
        await AssertEventsInBatchCountAction().execute({"expected": 1}, _ctx(requests))

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
