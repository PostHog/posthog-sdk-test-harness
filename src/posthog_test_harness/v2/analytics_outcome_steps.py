"""Analytics-v1 partial outcomes and default omissions at pinned observation scopes."""

import json

from .ai_steps import first_events, requests
from .analytics_retry_steps import STEPS as PREVIOUS_STEPS
from .analytics_retry_steps import response_results, retry_requests
from .analytics_wire_steps import first_request
from .steps import Registry, expect

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


@STEPS.step(
    r'the SDK is initialized with token "([^"]*)", flush threshold ([0-9]+), and compression disabled',
    routes=("/setup",),
)
async def setup_uncompressed(ctx, step, token, threshold):
    await ctx.call(
        "/setup",
        {"project_token": token, "config": {"host": ctx.server.url, "flush_at": int(threshold), "compression": "none"}},
    )


@STEPS.step(
    r'the SDK is initialized with token "([^"]*)", flush threshold ([0-9]+), and historical migration enabled',
    routes=("/setup",),
)
async def setup_historical(ctx, step, token, threshold):
    await ctx.call(
        "/setup",
        {
            "project_token": token,
            "config": {"host": ctx.server.url, "flush_at": int(threshold), "historical_migration": True},
        },
    )


@STEPS.step("the second request should retain first-response retry UUIDs and omit its terminal UUIDs")
async def partial_pruning(ctx, step):
    observed = retry_requests(ctx)
    results = response_results(ctx)
    expect(bool(results), "partial_results", "First response has no results")
    retry, terminal = set(), set()
    for identity, outcome in results.items():
        (retry if isinstance(outcome, dict) and outcome.get("result") == "retry" else terminal).add(identity)
    second = {event.get("uuid") for event in (observed[1].parsed_events or [])}
    expect(not second & terminal, "partial_terminal_uuid", "Second request retains terminal UUIDs")
    expect(not retry - second, "partial_missing_uuid", "Second request is missing retry UUIDs")


@STEPS.step(r"the last request should contain exactly ([0-9]+) parsed events")
async def last_count(ctx, step, count):
    observed = requests(ctx)
    expect(bool(observed), "missing_request", "No requests recorded")
    expect(len(observed[-1].parsed_events or []) == int(count), "last_batch_count", "Last batch event count differs")


@STEPS.step(r'the first request header "([^"]*)" should be absent')
async def absent_header(ctx, step, name):
    expect(first_request(ctx).headers.get(name.lower()) is None, "header_present", f"First request contains {name}")


@STEPS.step(r'the first received event option "([^"]*)" should be absent')
async def absent_option(ctx, step, name):
    options = first_events(ctx)[0].get("options") or {}
    expect(name not in options, "event_option_present", f"First event contains option {name}")


@STEPS.step("the first request body should contain historical_migration equal to true")
async def historical_body(ctx, step):
    body = first_request(ctx).body_decompressed
    try:
        data = json.loads(body) if body else None
    except json.JSONDecodeError:
        data = None
    expect(isinstance(data, dict), "historical_body", "First request body is not a JSON object")
    expect("historical_migration" in data, "historical_body", "First request lacks historical_migration")
    # The source helper compares values, including Python's boolean/number equality.
    expect(data["historical_migration"] == True, "historical_body", "historical_migration differs")  # noqa: E712
