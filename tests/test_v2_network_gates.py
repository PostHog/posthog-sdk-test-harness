"""Real concurrent service requests, explicit release and failure attribution."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import aiohttp
import pytest

from posthog_test_harness.mock_server import MockServerState
from posthog_test_harness.types import MockResponse
from posthog_test_harness.v2 import network_gates
from posthog_test_harness.v2.contracts import BoundaryError
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.network_gates import ResponseGates


@asynccontextmanager
async def service():
    server, tasks = CaseServer(), []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:

        async def post(body, path="/flags/"):
            async with client.post(server.url + path, json=body) as response:
                return response.status, await response.json()

        def send(body, path="/flags/"):
            task = asyncio.create_task(post(body, path))
            tasks.append(task)
            return task

        try:
            yield server, send
        finally:
            server.retire()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.to_thread(server.close)


async def test_disjoint_requests_reach_barriers_and_release_independently():
    async with service() as (server, send):
        server.set_flags("one", {"flag": False}, {"flag": None})
        server.set_flags("two", {"flag": "blue"}, {"flag": 0})
        server.gates.arm("first", distinct_id="one", flag_keys=["a"])
        server.gates.arm("second", distinct_id="two", flag_keys=["b"])
        first = send({"distinct_id": "one", "flag_keys_to_evaluate": ["a"]})
        second = send({"distinct_id": "two", "flag_keys_to_evaluate": ["b"]})
        indexes = await server.gates.in_flight(["first", "second"])
        assert len(set(indexes)) == 2
        assert all(server.requests()[i]["status"] is None for i in indexes)
        assert not first.done() and not second.done()
        assert server.flag_requests() == []  # These responses have not been selected for dispatch yet.

        # An unrelated request and an ingestion failure cannot be trapped by or
        # steal either held response.
        server.fail_next_ingestion(503)
        assert (await send({"distinct_id": "other"}))[0] == 200
        assert (await send({"event": "test"}, "/capture/"))[0] == 503
        assert (await send({"event": "test"}, "/capture/"))[0] == 200
        server.set_flags("two", {"flag": True}, {"flag": "new"})
        server.gates.release("second")
        status, body = await second
        assert status == 200 and body["featureFlags"] == {"flag": "blue"}
        assert body["featureFlagPayloads"] == {"flag": "0"}
        assert not first.done()
        server.gates.release("first")
        status, body = await first
        assert status == 200 and body["featureFlags"] == {"flag": False}
        assert body["featureFlagPayloads"] == {"flag": "null"}
        assert server.gates.failures() == []
        assert [e["transition"] for e in server.gates.diagnostics() if e["gate_id"] == "first"] == [
            "armed",
            "arrived",
            "released",
            "responded",
        ]
        assert all(a["at_ms"] <= b["at_ms"] for a, b in zip(server.gates.diagnostics(), server.gates.diagnostics()[1:]))


async def test_matching_is_scoped_and_repeated_gates_are_fifo():
    async with service() as (server, send):
        for gate in ("first", "second"):
            server.gates.arm(gate, distinct_id="user", flag_keys=["a", "b"])
        for body in (
            {"distinct_id": "other", "flag_keys_to_evaluate": ["a", "b"]},
            {"distinct_id": "user"},
            {"distinct_id": "user", "flag_keys_to_evaluate": []},
            {"distinct_id": "user", "flag_keys_to_evaluate": ["a", "a"]},
        ):
            assert (await send(body))[0] == 200
        assert [e["transition"] for e in server.gates.diagnostics()] == ["armed", "armed"]
        body = {"distinct_id": "user", "flag_keys_to_evaluate": ["b", "a"]}
        first = send(body)
        first_index = await server.gates.in_flight(["first"])
        second = send(body)
        indexes = await server.gates.in_flight(["first", "second"])
        assert indexes[0] == first_index[0] and indexes[0] < indexes[1]
        server.gates.release("second")
        assert (await second)[0] == 200 and not first.done()
        server.gates.release("first")
        assert (await first)[0] == 200
        with pytest.raises(BoundaryError, match="already used"):
            server.gates.arm("first")
        with pytest.raises(BoundaryError, match="Only an arrived"):
            server.gates.release("first")


async def test_held_failure_is_not_consumed_by_other_flag_traffic():
    async with service() as (server, send):
        server.gates.arm("failure", distinct_id="user")
        server.fail_next_flags(503)
        failed = send({"distinct_id": "user"})
        await server.gates.in_flight(["failure"])
        assert (await send({"distinct_id": "other"}))[0] == 200
        server.gates.release("failure")
        assert (await failed)[0] == 503
        assert [r.response_status for r in server.flag_requests()] == [200, 503]


async def test_gate_expiration_is_a_fixture_error_not_a_remote_response_configuration():
    async with service() as (server, send):
        server.gates.arm("expired", timeout_ms=20)
        status, _ = await send({"distinct_id": "user"})
        assert status == 504
        assert [e.code for e in server.gates.failures()] == ["mock_gate_timeout"]
        assert [e["transition"] for e in server.gates.diagnostics()] == ["armed", "arrived", "timed_out", "responded"]
        with pytest.raises(BoundaryError) as error:
            await server.gates.in_flight(["expired"])
        assert error.value.code == "invalid_state"


async def test_arrival_timeout_does_not_release_or_consume_a_gate():
    async with service() as (server, send):
        server.gates.arm("waiting")
        with pytest.raises(BoundaryError) as error:
            await server.gates.in_flight(["waiting"], timeout_ms=10)
        assert error.value.code == "mock_arrival_timeout"
        task = send({"distinct_id": "user"})
        await server.gates.in_flight(["waiting"])
        assert not task.done()
        server.gates.release("waiting")
        assert (await task)[0] == 200


async def test_retirement_aborts_pending_requests_and_preserves_other_case():
    async with service() as (first, send_first), service() as (second, send_second):
        for server in (first, second):
            server.gates.arm("same", distinct_id="user")
        task = send_first({"distinct_id": "user"})
        await first.gates.in_flight(["same"])
        first.retire()
        assert (await task)[0] == 410
        await asyncio.to_thread(first.wait_idle)
        recorded = first.requests()
        assert (await send_first({"distinct_id": "late"}))[0] == 410
        assert first.requests() == recorded
        assert first.gates.failures() == []
        second_task = send_second({"distinct_id": "user"})
        await second.gates.in_flight(["same"])
        assert not second_task.done()
        second.gates.release("same")
        assert (await second_task)[0] == 200
        assert [e["transition"] for e in first.gates.diagnostics()] == ["armed", "arrived", "aborted", "responded"]


async def test_close_releases_a_live_request_before_stopping_the_server():
    async with service() as (server, send):
        server.gates.arm("held")
        task = send({"distinct_id": "user"})
        await server.gates.in_flight(["held"])
        await asyncio.to_thread(server.close)
        assert (await task)[0] == 410
        assert server.active_requests == 0 and not server.thread.is_alive()


async def test_cancelled_arrival_waiter_finishes_its_worker_on_retirement(monkeypatch):
    gates = ResponseGates(time.monotonic())
    gates.arm("held")
    waiting, finished = threading.Event(), threading.Event()
    original_wait = gates.condition.wait
    original_arrivals = gates.wait_for_arrivals

    def observed_wait(timeout=None):
        waiting.set()
        return original_wait(timeout)

    def observed_arrivals(*args):
        try:
            return original_arrivals(*args)
        finally:
            finished.set()

    monkeypatch.setattr(gates.condition, "wait", observed_wait)
    monkeypatch.setattr(gates, "wait_for_arrivals", observed_arrivals)
    task = asyncio.create_task(gates.in_flight(["held"]))
    try:
        assert await asyncio.to_thread(waiting.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        gates.retire()
        await asyncio.gather(task, return_exceptions=True)
    assert await asyncio.to_thread(finished.wait, 1)


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_ms": 0},
        {"timeout_ms": True},
        {"timeout_ms": 300001},
        {"timeout_ms": 1.5},
        {"flag_keys": "a"},
        {"flag_keys": ["a", "a"]},
        {"flag_keys": [1]},
        {"distinct_id": 1},
    ],
)
def test_invalid_gate_configuration(options):
    gates = ResponseGates(time.monotonic())
    with pytest.raises(BoundaryError) as error:
        gates.arm("gate", **options)
    assert error.value.code == "invalid_fixture"
    assert gates.diagnostics() == []


@pytest.mark.parametrize("ids", [[], ["unknown"], ["known", "known"]])
async def test_arrival_barrier_rejects_empty_unknown_or_duplicate_gate_selection(ids):
    gates = ResponseGates(time.monotonic())
    gates.arm("known")
    with pytest.raises(BoundaryError) as error:
        await gates.in_flight(ids)
    assert error.value.code == "invalid_fixture"


def test_release_after_deadline_cannot_win_a_race_with_expiration(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(network_gates, "time", SimpleNamespace(monotonic=lambda: now[0]))
    gates = ResponseGates(now[0])
    gates.arm("late", timeout_ms=20)
    gate = gates.claim({}, 0)
    now[0] += 0.021
    with pytest.raises(BoundaryError) as error:
        gates.release("late")
    assert error.value.code == "mock_gate_timeout"
    assert gates.wait(gate) == "timed_out"
    assert len(gates.failures()) == 1


@pytest.mark.parametrize("action", ["retire", "failures"])
def test_elapsed_deadline_survives_retirement_or_observation_before_waiter_runs(monkeypatch, action):
    now = [100.0]
    monkeypatch.setattr(network_gates, "time", SimpleNamespace(monotonic=lambda: now[0]))
    gates = ResponseGates(now[0])
    gates.arm("late", timeout_ms=20)
    gate = gates.claim({}, 0)
    now[0] += 0.021
    getattr(gates, action)()
    assert gates.wait(gate) == "timed_out"
    gates.retire()
    assert [e.code for e in gates.failures()] == ["mock_gate_timeout"]
    assert [e["transition"] for e in gates.diagnostics()] == ["armed", "arrived", "timed_out"]


@pytest.mark.parametrize("partition", [None, "one"])
def test_request_override_preserves_legacy_response_queue(partition):
    state = MockServerState()
    state.set_response_queue([MockResponse(status_code=503)], test_id=partition)
    args = {
        "method": "POST",
        "path": "/flags/",
        "headers": {"x-test-id": partition} if partition else {},
        "query_params": {},
        "body_raw": b"{}",
    }
    assert state.record_request(**args, response_override=MockResponse(status_code=429)).response_status == 429
    assert state.record_request(**args).response_status == 503
    assert state.record_request(**args).response_status == 200
    assert len(state.get_requests(test_id=partition)) == 3
