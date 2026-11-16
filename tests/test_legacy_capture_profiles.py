"""Legacy capture contracts distinguish delivery/retry policies without weakening defaults."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

import posthog_test_harness.actions as actions
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.tests.context import TestContext as HarnessContext
from posthog_test_harness.tests.suites import ContractTestSuite

BOUNDED = "retry_behavior.max_retries_respected"
UNBOUNDED = "retry_behavior.retries_until_recovery_after_retry_budget"
QUEUED = "batch_format.multiple_events_batched_together"
SYNC = "batch_format.synchronous_capture_delivers_each_event_immediately"


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = None
        self.sleeps = []

    async def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration
        if self.on_sleep:
            self.on_sleep(self.now)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    clock = VirtualClock()
    monkeypatch.setattr(actions, "monotonic", lambda: clock.now, raising=False)
    monkeypatch.setattr(actions, "asyncio", SimpleNamespace(sleep=clock.sleep, wait_for=actions.asyncio.wait_for))
    return clock


def definitions(*capabilities):
    return dict(ContractTestSuite("capture", ContractExecutor()).collect_tests("server", ["capture_v0", *capabilities]))


@pytest.mark.parametrize("capabilities", [[], ["bounded_retries"], ["encoding_gzip"]])
def test_default_and_bounded_profiles_keep_all_30_legacy_capture_tests(capabilities):
    tests = definitions(*capabilities)
    assert len(tests) == 30
    assert {BOUNDED, QUEUED} <= tests.keys()
    assert {UNBOUNDED, SYNC}.isdisjoint(tests)


@pytest.mark.parametrize(
    "capability,removed,added",
    [("unbounded_retries", BOUNDED, UNBOUNDED), ("synchronous_capture", QUEUED, SYNC)],
)
def test_profile_replaces_exactly_one_assertion_with_positive_counterpart(capability, removed, added):
    before, after = definitions(), definitions(capability)
    assert before.keys() - after.keys() == {removed}
    assert after.keys() - before.keys() == {added}
    assert len(after) == 30


@pytest.mark.parametrize("capabilities", [[], ["bounded_retries"], ["unbounded_retries"]])
def test_client_profiles_preserve_27_legacy_tests_and_retry_counterpart(capabilities):
    tests = dict(
        ContractTestSuite("capture", ContractExecutor()).collect_tests("client", ["capture_v0", *capabilities])
    )
    assert len(tests) == 27
    assert (UNBOUNDED in tests) is ("unbounded_retries" in capabilities)
    assert (BOUNDED in tests) is ("unbounded_retries" not in capabilities)


def test_independent_profile_dimensions_preserve_inventory():
    tests = definitions("unbounded_retries", "synchronous_capture")
    assert len(tests) == 30
    assert {UNBOUNDED, SYNC} <= tests.keys()
    assert {BOUNDED, QUEUED}.isdisjoint(tests)


class CapturePolicyAdapter:
    """Deterministic transport double; actual SDK profiles are exercised separately."""

    def __init__(self, state, *, unbounded=False, synchronous=False, broken=False):
        self.client = MockServer(state).app.test_client()
        self.unbounded = unbounded
        self.synchronous = synchronous
        self.broken = broken
        self.reset = AsyncMock(side_effect=self.clear)

    def clear(self):
        self.pending = []

    async def init(self, request):
        self.max_retries = request.max_retries if request.max_retries is not None else 3

    async def capture(self, request):
        self.pending.append(
            {
                "event": request.event,
                "distinct_id": request.distinct_id,
                "uuid": str(uuid5(NAMESPACE_URL, request.event)),
                "timestamp": "2025-01-02T03:04:05Z",
            }
        )
        if self.synchronous and not self.broken:
            await self.flush()

    async def flush(self):
        if not self.pending:
            return
        if self.broken and not self.synchronous:
            return  # A missing delivery must not make the contract green.
        attempts = 10 if self.unbounded else self.max_retries + 1
        for _ in range(attempts):
            response = self.client.post("/batch/", json={"api_key": "test", "batch": self.pending})
            if response.status_code == 200:
                break
        self.pending = []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability,name,unbounded,synchronous",
    [
        (None, BOUNDED, False, False),
        ("unbounded_retries", UNBOUNDED, True, False),
        (None, QUEUED, False, False),
        ("synchronous_capture", SYNC, False, True),
    ],
)
@pytest.mark.parametrize("broken", [False, True])
async def test_selected_policy_contract_accepts_delivery_and_rejects_broken_transport(
    capability, name, unbounded, synchronous, broken
):
    executor = ContractExecutor()
    executor.actions["wait"].execute = AsyncMock()
    suite = ContractTestSuite("capture", executor)
    tests = dict(suite.collect_tests("server", ["capture_v0", *([capability] if capability else [])]))
    assert name in tests
    state = MockServerState()
    adapter = CapturePolicyAdapter(state, unbounded=unbounded, synchronous=synchronous, broken=broken)
    ctx = HarnessContext(adapter, state, "http://mock")
    result = await suite.run_single_test(name, tests[name], ctx)
    assert result.passed is not broken, result.message
    if result.passed and unbounded:
        assert [r.response_status for r in state.get_requests()] == [503] * 5 + [200]
    if result.passed and synchronous:
        events = [r.parsed_events for r in state.get_requests()]
        assert len(events) == 5 and all(len(batch) == 1 for batch in events)
        assert [batch[0]["event"] for batch in events] == [f"test_event_{i}" for i in range(5)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability,name,unbounded,synchronous",
    [
        (None, BOUNDED, True, False),
        ("unbounded_retries", UNBOUNDED, False, False),
        (None, QUEUED, False, True),
        ("synchronous_capture", SYNC, False, False),
    ],
)
async def test_policy_assertions_reject_the_opposite_delivery_or_retry_policy(capability, name, unbounded, synchronous):
    executor = ContractExecutor()
    executor.actions["wait"].execute = AsyncMock()
    suite = ContractTestSuite("capture", executor)
    tests = dict(suite.collect_tests("server", ["capture_v0", *([capability] if capability else [])]))
    state = MockServerState()
    ctx = HarnessContext(
        CapturePolicyAdapter(state, unbounded=unbounded, synchronous=synchronous), state, "http://mock"
    )
    result = await suite.run_single_test(name, tests[name], ctx)
    assert not result.passed
    assert "request" in result.message.lower(), result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("after_attempt,restore_on_recovery", [(0, False), (4, False), (5, False), (4, True)])
@pytest.mark.parametrize(
    "mutation",
    ["empty", "replacement", "event", "distinct_id", "uuid", "timestamp", "missing_uuid", "missing_timestamp"],
)
async def test_recovery_rejects_missing_or_changed_event_identity_on_any_attempt(
    after_attempt, restore_on_recovery, mutation
):
    executor = ContractExecutor()
    executor.actions["wait"].execute = AsyncMock()
    suite = ContractTestSuite("capture", executor)
    test = dict(suite.collect_tests("server", ["capture_v0", "unbounded_retries"]))[UNBOUNDED]
    state = MockServerState()
    adapter = CapturePolicyAdapter(state, unbounded=True)

    async def corrupt_retry_payloads():
        original = adapter.pending[0].copy()
        for index in range(6):
            event = original.copy()
            batch = [event]
            if index >= after_attempt and not (restore_on_recovery and index == 5):
                if mutation == "empty":
                    batch = []
                elif mutation == "replacement":
                    batch = [
                        {
                            "event": "wrong-event",
                            "distinct_id": "wrong-user",
                            "uuid": "wrong-uuid",
                            "timestamp": "2026-11-15T00:00:00Z",
                        }
                    ]
                elif mutation.startswith("missing_"):
                    del event[mutation.removeprefix("missing_")]
                else:
                    event[mutation] = (
                        {
                            "uuid": "10000000-0000-4000-8000-000000000001",
                            "timestamp": "2025-01-02T03:04:06Z",
                        }.get(mutation, "changed-" + mutation)
                        if after_attempt
                        else "invalid-" + mutation
                    )
            adapter.client.post("/batch/", json={"api_key": "test", "batch": batch})
        adapter.pending = []

    adapter.flush = corrupt_retry_payloads
    ctx = HarnessContext(adapter, state, "http://mock")
    result = await suite.run_single_test(UNBOUNDED, test, ctx)
    assert len(state.get_requests()) == 6
    assert state.get_requests()[-1].response_status == 200
    assert not result.passed, f"Accepted {mutation} after attempt {after_attempt}: {result}"


@pytest.mark.parametrize("sdk_type", ["server", "client"])
@pytest.mark.parametrize("suite_name", ["capture", "feature_flags"])
def test_conflicting_retry_capabilities_are_rejected_before_selection(sdk_type, suite_name):
    suite = ContractTestSuite(suite_name, ContractExecutor())
    with pytest.raises(ValueError, match="bounded_retries.*unbounded_retries.*mutually exclusive"):
        suite.collect_tests(sdk_type, ["capture_v0", "bounded_retries", "unbounded_retries"])


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2])
@pytest.mark.parametrize("suite_names", [None, ["not-a-suite"]])
async def test_conflicting_retry_profile_cannot_obtain_even_an_empty_runner_acceptance(concurrency, suite_names):
    from posthog_test_harness.tests.runner import run_all_suites

    adapter = CapturePolicyAdapter(MockServerState())
    ctx = HarnessContext(adapter, MockServerState(), "http://mock")
    with pytest.raises(ValueError, match="bounded_retries.*unbounded_retries.*mutually exclusive"):
        await run_all_suites(
            ctx,
            suite_names=suite_names,
            concurrency=concurrency,
            supports_parallel=True,
            capabilities=["bounded_retries", "unbounded_retries"],
        )
    adapter.reset.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_distinct_id", [False, True])
@pytest.mark.parametrize("final_status,expected_pass", [(200, True), (503, False)])
async def test_retry_identity_assertion_checks_final_delivery_and_accepts_client_server_identity(
    client_distinct_id, final_status, expected_pass
):
    from posthog_test_harness.types import MockResponse

    state = MockServerState()
    state.set_response_queue([MockResponse(status_code=200), MockResponse(status_code=final_status)])
    client = MockServer(state).app.test_client()
    for index in range(2):
        event = {
            "event": "original",
            "uuid": "10000000-0000-4000-8000-000000000001",
            "timestamp": "2025-01-02T03:04:05Z",
            "properties": {"incidental": index},
        }
        if client_distinct_id:
            event["properties"]["distinct_id"] = "user"
        else:
            event["distinct_id"] = "user"
        client.post("/batch/", json={"batch": [event], "sent_at": index})
    action = ContractExecutor().actions["assert_event_identity_preserved_on_retry"]
    ctx = HarnessContext(None, state, "http://mock")
    if expected_pass:
        await action.execute({"event": "original", "distinct_id": "user"}, ctx)
    else:
        with pytest.raises(AssertionError, match="Final retry request did not successfully deliver"):
            await action.execute({"event": "original", "distinct_id": "user"}, ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("buffer_from", [None, 0, 1, 2, 3])
async def test_synchronous_contract_checks_delivery_after_every_capture(buffer_from):
    executor = ContractExecutor()
    suite = ContractTestSuite("capture", executor)
    test = definitions("synchronous_capture")[SYNC]
    state = MockServerState()
    adapter = CapturePolicyAdapter(state)
    enqueue = adapter.capture
    delivery_counts = []

    async def capture(request):
        await enqueue(request)
        index = len(delivery_counts)
        if buffer_from is None or index < buffer_from or index == 4:
            for event in adapter.pending:
                adapter.client.post("/batch/", json={"api_key": "test", "batch": [event]})
            adapter.pending = []
        delivery_counts.append(len(state.get_requests()))

    adapter.capture = capture
    result = await suite.run_single_test(SYNC, test, HarnessContext(adapter, state, "http://mock"))
    if buffer_from is None:
        assert result.passed, result.message
        assert delivery_counts == [1, 2, 3, 4, 5]
    else:
        assert not result.passed, f"Accepted buffered delivery counts {delivery_counts}"
        assert result.message == f"Expected {buffer_from + 1} requests, got {buffer_from}"
        assert len(delivery_counts) == buffer_from + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_unbounded_recovery_waits_for_the_async_backoff_sequence(clock, recovers):
    executor = ContractExecutor()
    suite = ContractTestSuite("capture", executor)
    test = definitions("unbounded_retries")[UNBOUNDED]
    state = MockServerState()
    adapter = CapturePolicyAdapter(state, unbounded=True)
    attempt_times = [0, 1, 3, 7, 15, 31] if recovers else [0, 1, 3, 7, 15]
    sent_at = []

    def send_due(now):
        while len(sent_at) < len(attempt_times) and attempt_times[len(sent_at)] <= now:
            adapter.client.post("/batch/", json={"api_key": "test", "batch": adapter.pending})
            sent_at.append(attempt_times[len(sent_at)])

    async def flush():
        send_due(clock.now)
        clock.on_sleep = send_due

    adapter.flush = flush
    result = await suite.run_single_test(UNBOUNDED, test, HarnessContext(adapter, state, "http://mock"))
    if recovers:
        assert result.passed, result.message
        assert sent_at == [0, 1, 3, 7, 15, 31]
        assert 31 <= clock.now < 32
        assert [request.response_status for request in state.get_requests()] == [503] * 5 + [200]
    else:
        assert not result.passed
        assert "Timed out" in result.message
        assert clock.now == pytest.approx(60)
        assert len(state.get_requests()) == 5


@pytest.mark.asyncio
async def test_request_wait_returns_immediately_when_the_minimum_is_already_present(clock):
    state = MockServerState()
    client = MockServer(state).app.test_client()
    for _ in range(3):
        client.post("/batch/", json={"batch": []})
    await ContractExecutor().execute_action(
        "wait_for_request_count", {"expected": 2}, HarnessContext(None, state, "http://mock")
    )
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_request_wait_filters_paths_and_stops_on_delivery(clock):
    state = MockServerState()
    client = MockServer(state).app.test_client()
    for _ in range(2):
        client.post("/flags/", json={})

    def deliver(now):
        if now >= 0.2 and len(state.get_requests()) == 2:
            client.post("/batch/", json={"batch": []})

    clock.on_sleep = deliver
    await ContractExecutor().execute_action(
        "wait_for_request_count",
        {"expected": 1, "path": "/batch", "timeout_ms": 1000},
        HarnessContext(None, state, "http://mock"),
    )
    assert clock.now == pytest.approx(0.2)
    assert len(state.get_requests()) == 3


@pytest.mark.asyncio
async def test_request_wait_does_not_sleep_past_its_deadline(clock):
    with pytest.raises(AssertionError, match="Timed out waiting for at least 1 requests, got 0"):
        await ContractExecutor().execute_action(
            "wait_for_request_count",
            {"expected": 1, "timeout_ms": 250},
            HarnessContext(None, MockServerState(), "http://mock"),
        )
    assert clock.sleeps == pytest.approx([0.1, 0.1, 0.05])
    assert clock.now == pytest.approx(0.25)
