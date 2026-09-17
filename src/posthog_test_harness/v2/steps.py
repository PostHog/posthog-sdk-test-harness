"""Shared registry, execution context, and public flush bindings."""

import re
from copy import deepcopy

from ..assertions import assert_request_count
from .contracts import BoundaryError, json_equal, require
from .data import cell
from .fixtures import INGESTION_PATHS


def expect(condition, code, message):
    if not condition:
        raise BoundaryError(code, message, "failed_assertion")


class Registry:
    def __init__(self):
        self.definitions = []
        self.requirements = {}

    def step(self, pattern, argument=None, *, routes=(), fixtures=()):
        def register(handler):
            self.definitions.append((re.compile(pattern), handler, argument))
            self.requirements[handler] = {"routes": list(routes), "fixtures": list(fixtures)}
            return handler

        return register

    def bind(self, step):
        matches = [
            (handler, match, argument)
            for regex, handler, argument in self.definitions
            if (match := regex.fullmatch(step.text))
        ]
        require(bool(matches), "undefined_step", f"No step definition at {step.source['path']}:{step.source['line']}")
        require(len(matches) == 1, "ambiguous_step", "Multiple step definitions match")
        handler, match, argument = matches[0]
        require(
            set(step.argument) == ({argument} if argument else set()), "invalid_step_data", "Unexpected step argument"
        )
        return handler, match.groups()


def table(step, types):
    rows = step.argument["dataTable"]["rows"]
    headers = [c["value"] for c in rows[0]["cells"]]
    require(
        len(headers) == len(set(headers)) and set(headers) == set(types),
        "invalid_step_data",
        "Unexpected or duplicate table columns",
    )
    require(len(rows) > 1, "invalid_step_data", "Table has no data rows")
    result = []
    for row in rows[1:]:
        require(len(row["cells"]) == len(headers), "invalid_step_data", "Table row width mismatch")
        result.append({key: cell(c["value"], types[key])["value"] for key, c in zip(headers, row["cells"])})
    return result


def contains_events(actual, expected):
    """Distinct matching records in a single batch; repeated names need repeated events."""
    remaining = list(actual or [])
    for wanted in expected:
        for index, event in enumerate(remaining):
            if isinstance(event, dict) and all(
                key in event and json_equal(event[key], value) for key, value in wanted.items()
            ):
                remaining.pop(index)
                break
        else:
            return False
    return True


class Context:
    def __init__(self, client, case, profile, server, timeout_ms, diagnostics):
        self.client, self.case, self.profile, self.server = client, case, profile, server
        self.timeout_ms, self.diagnostics = timeout_ms, diagnostics
        self.fixture = self.controls = self.last_receipt = None
        self.before_records = []
        self.flush_traffic = self.flush_requests = None
        self.failed_status = None
        self.step_index = 0
        self.call_index = 0
        self.selected_event = None
        self.flags = None
        self.pending_tasks = []

    async def call(self, route, args, *, receiver=None, references=None, check_result=True):
        require(self.fixture is not None, "invalid_state", "Fresh harness step must allocate a receiver first")
        self.call_index += 1
        call_id = f"{self.fixture.id}/call-{self.call_index}"
        self.diagnostics["invocations"].append(
            {
                "call_id": call_id,
                "route": route,
                "step_index": self.step_index,
                "source": self.case.steps[self.step_index].source,
            }
        )
        if receiver is not None:
            self.diagnostics["invocations"][-1]["receiver"] = deepcopy(receiver)
        if references is not None:
            self.diagnostics["invocations"][-1]["references"] = deepcopy(references)
        receipt = await self.fixture.invoke(
            call_id, route, args, receiver=receiver, references=references, timeout_ms=self.timeout_ms
        )
        self.last_receipt = receipt
        completion = receipt["completion"]
        if completion["kind"] == "harness":
            failure = completion["failure"]
            raise BoundaryError(failure["code"], failure["message"], failure["kind"])
        return completion["outcome"]

    def ingestion(self):
        require(self.flush_requests is not None, "invalid_state", "No flush observation window")
        return [r for r in self.flush_requests if r.path in INGESTION_PATHS]


FLUSH_STEPS = Registry()


@FLUSH_STEPS.step("a fresh SDK acceptance test harness", fixtures=("scheduler.manual.v1",))
async def fresh(ctx, step):
    from .fixtures import FlushControls

    require(ctx.fixture is None, "invalid_state", "A case can allocate only one fresh harness")
    fixture_id = ctx.diagnostics["fixture_id"]
    ctx.fixture = await ctx.client.allocate(fixture_id, ctx.case.id, ctx.profile["id"], ctx.timeout_ms)
    ctx.controls = FlushControls(ctx.fixture, ctx.profile, ctx.timeout_ms, ctx.diagnostics["controls"])
    await ctx.controls.command("scheduler_manual")


@FLUSH_STEPS.step(r'the SDK clock is fixed at "([^"]*)"', fixtures=("clock.fixed.v1",))
async def clock(ctx, step, timestamp):
    await ctx.controls.command("clock_fixed", timestamp=timestamp)


@FLUSH_STEPS.step("persistent storage is empty", fixtures=("storage.empty.v1",))
async def storage(ctx, step):
    await ctx.controls.command("storage_empty")


@FLUSH_STEPS.step("the mock PostHog server is reset")
async def reset_server(ctx, step):
    ctx.server.reset()


@FLUSH_STEPS.step(r'the SDK is initialized with token "([^"]*)"', routes=("/setup",))
async def setup(ctx, step, token):
    await ctx.call("/setup", {"project_token": token, "config": {"host": ctx.server.url}})


@FLUSH_STEPS.step(
    "the event queue contains events:", "dataTable", routes=("/capture",), fixtures=("queue.snapshot.v1",)
)
async def queued(ctx, step):
    events = table(step, {"event": "string", "distinct_id": "string"})
    expect(await ctx.controls.command("queue_snapshot") == [], "queue_not_empty", "Fresh queue is not empty")
    for event in events:
        await ctx.call("/capture", event)
    records = await ctx.controls.command("queue_snapshot")
    expect(
        len(records) == len(events) and contains_events([r["event"] for r in records], events),
        "queue_precondition",
        "Captured events are not present in the actual queue",
    )
    ctx.before_records = deepcopy(records)


@FLUSH_STEPS.step("the event queue is empty", fixtures=("queue.snapshot.v1",))
async def empty_queue(ctx, step):
    expect(await ctx.controls.command("queue_snapshot") == [], "queue_not_empty", "Expected an empty queue")


@FLUSH_STEPS.step(r"the mock server will fail the next ingestion request with status ([0-9]+)")
async def fail_ingestion(ctx, step, status):
    status = int(status)
    require(400 <= status <= 599, "invalid_step_data", "Expected a failing HTTP status")
    ctx.server.fail_next_ingestion(status)
    ctx.failed_status = status


@FLUSH_STEPS.step("flush is called", routes=("/flush",), fixtures=("queue.snapshot.v1",))
async def flush(ctx, step):
    if ctx.failed_status is not None and not ctx.before_records:
        ctx.before_records = deepcopy(await ctx.controls.command("queue_snapshot"))
    traffic_start = len(ctx.server.requests())
    request_start = len(ctx.server.state.get_requests())
    ctx.diagnostics["flush_window_start"] = traffic_start
    try:
        await ctx.call("/flush", {})
    finally:
        ctx.flush_traffic = ctx.server.requests()[traffic_start:]
        ctx.flush_requests = ctx.server.state.get_requests()[request_start:]
        ctx.diagnostics["flush_window_end"] = traffic_start + len(ctx.flush_traffic)


@FLUSH_STEPS.step("the mock server should receive a batch containing events:", "dataTable")
async def received(ctx, step):
    events = table(step, {"event": "string"})
    expect(
        any(contains_events(r.parsed_events, events) for r in ctx.ingestion()),
        "missing_event",
        "No single ingestion batch contains all expected events",
    )


@FLUSH_STEPS.step("the event queue should be empty after a successful flush", fixtures=("queue.snapshot.v1",))
async def drained(ctx, step):
    requests = ctx.ingestion()
    expect(
        requests and all(200 <= r.response_status < 300 for r in requests),
        "delivery_not_successful",
        "No successful flush delivery was observed",
    )
    expect(await ctx.controls.command("queue_snapshot") == [], "queue_not_drained", "Delivered records remain queued")


@FLUSH_STEPS.step("the call should not throw")
async def no_throw(ctx, step):
    require(ctx.last_receipt is not None, "invalid_state", "No SDK call to assert")
    completion = ctx.last_receipt["completion"]
    expect(
        completion["kind"] == "sdk" and completion["outcome"]["kind"] != "thrown", "unexpected_throw", "SDK call threw"
    )


@FLUSH_STEPS.step("no network request should be sent")
async def no_network(ctx, step):
    require(ctx.flush_traffic is not None, "invalid_state", "No flush observation window")
    assert_request_count(ctx.flush_traffic, 0)


@FLUSH_STEPS.step(r'the event named "([^"]*)" should remain queued for retry', fixtures=("queue.snapshot.v1",))
async def retryable(ctx, step, name):
    expect(
        ctx.failed_status is not None
        and any(
            r.response_status == ctx.failed_status and contains_events(r.parsed_events, [{"event": name}])
            for r in ctx.ingestion()
        ),
        "failure_not_exercised",
        "The queued event did not encounter the configured failure",
    )
    original = [r for r in ctx.before_records if r["event"].get("event") == name]
    records = await ctx.controls.command("queue_snapshot")
    expect(
        bool(original) and all(any(json_equal(old, current) for current in records) for old in original),
        "retry_record_missing",
        "The original failed event record is not retained for retry",
    )
