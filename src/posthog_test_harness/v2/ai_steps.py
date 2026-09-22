"""AI YAML-parity public calls and received-wire assertions.

The native string/null capture_ai result replaces the old adapter result envelope;
no synthetic success field is supplied by the binding. Observations include capture
traffic from initialization through flush, not only the flush call window.
"""

import re
from datetime import datetime
from uuid import UUID

from .contracts import json_equal, require
from .data import doc_string
from .probe_steps import STEPS as PREVIOUS_STEPS
from .steps import Registry, expect, table

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


def json_arguments(step):
    doc = step.argument["docString"]
    value = doc_string(doc["content"], doc.get("mediaType"))
    require(isinstance(value, dict), "invalid_step_data", "Expected JSON argument object")
    return value


@STEPS.step("an isolated SDK instance")
async def isolated_instance(ctx, step):
    require(ctx.fixture is None, "invalid_state", "A case can allocate only one fixture")
    ctx.fixture = await ctx.client.allocate(
        ctx.diagnostics["fixture_id"], ctx.case.id, ctx.profile["id"], ctx.timeout_ms
    )


@STEPS.step("an isolated SDK with empty persistent storage", fixtures=("storage.empty.v1",))
async def isolated(ctx, step):
    await isolated_instance(ctx, step)


@STEPS.step(r'the SDK is initialized with token "([^"]*)" and flush threshold ([0-9]+)', routes=("/setup",))
async def setup(ctx, step, token, threshold):
    await ctx.call("/setup", {"project_token": token, "config": {"host": ctx.server.url, "flush_at": int(threshold)}})


@STEPS.step("capture_ai is called with JSON arguments:", "docString", routes=("/capture_ai",))
async def capture_ai(ctx, step):
    ctx.ai_outcome = await ctx.call("/capture_ai", json_arguments(step))


@STEPS.step("capture is called with JSON arguments:", "docString", routes=("/capture",))
async def capture(ctx, step):
    await ctx.call("/capture", json_arguments(step))


@STEPS.step("identify is called with JSON arguments:", "docString", routes=("/identify",))
async def identify(ctx, step):
    await ctx.call("/identify", json_arguments(step))


@STEPS.step("alias is called with JSON arguments:", "docString", routes=("/alias",))
async def alias(ctx, step):
    await ctx.call("/alias", json_arguments(step))


@STEPS.step("pending captures are flushed", routes=("/flush",))
async def flush(ctx, step):
    # YAML flush actions do not assert delivery success or a native return value.
    # Keep rejected flushes observable so the following retry/delivery assertions run.
    await ctx.call("/flush", {}, check_result=False)


def requests(ctx):
    return ctx.server.state.get_requests()


def first_events(ctx):
    observed = requests(ctx)
    expect(bool(observed) and bool(observed[0].parsed_events), "missing_event", "No events in first capture request")
    return observed[0].parsed_events


@STEPS.step(r"exactly ([0-9]+) capture request should have been received")
async def count(ctx, step, expected):
    expect(len(requests(ctx)) == int(expected), "request_count", f"Expected {expected} capture requests")


@STEPS.step("every capture request path should be one of:", "dataTable")
async def paths(ctx, step):
    allowed = {r["path"].rstrip("/") for r in table(step, {"path": "string"})}
    observed = requests(ctx)
    expect(bool(observed), "request_path", "No capture requests received")
    expect(all(r.path.rstrip("/") in allowed for r in observed), "request_path", "Unexpected capture request path")


@STEPS.step(r'no capture request should use "([^"]*)"')
async def forbidden_path(ctx, step, path):
    expect(all(r.path.rstrip("/") != path.rstrip("/") for r in requests(ctx)), "request_path", "Forbidden capture path")


@STEPS.step(r'the first received event field "([^"]*)" should equal "([^"]*)"')
async def event_field(ctx, step, key, value):
    expect(json_equal(first_events(ctx)[0].get(key), value), "event_field", f"Received event field differs: {key}")


@STEPS.step(r'the first received event property "([^"]*)" should equal "([^"]*)"')
async def event_property(ctx, step, key, value):
    expect(
        json_equal(first_events(ctx)[0].get("properties", {}).get(key), value),
        "event_property",
        f"Received property differs: {key}",
    )


def ai_result(ctx):
    outcome = getattr(ctx, "ai_outcome", None)
    require(outcome is not None, "invalid_state", "No AI capture result retained")
    expect(
        outcome["kind"] == "value" and isinstance(outcome["value"], str) and bool(outcome["value"]),
        "ai_not_admitted",
        "AI capture did not return an admitted event UUID",
    )
    return outcome["value"]


@STEPS.step("AI capture should return an admitted event UUID")
async def admitted(ctx, step):
    ai_result(ctx)


@STEPS.step(r'the AI capture return should equal "([^"]*)"')
async def returned_uuid(ctx, step, expected):
    expect(ai_result(ctx) == expected, "returned_uuid", "AI capture returned a different UUID")


@STEPS.step("the first received event UUID should be valid")
async def valid_uuid(ctx, step):
    value = first_events(ctx)[0].get("uuid")
    try:
        valid = isinstance(value, str) and bool(UUID(value))
    except ValueError:
        valid = False
    expect(valid, "event_uuid", "Received event UUID is missing or invalid")


@STEPS.step("the AI capture return should equal the first received event UUID")
async def uuid_equal(ctx, step):
    expect(
        ai_result(ctx) == first_events(ctx)[0].get("uuid"), "uuid_mismatch", "Returned UUID differs from received UUID"
    )


def utc_instant(value):
    """Legacy UTC syntax and lossless fractional equality (no microsecond truncation)."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)", value
    ):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    base, _, fraction = (value[:-1] if value.endswith("Z") else value[:-6]).partition(".")
    return base, fraction.rstrip("0")


@STEPS.step(r'every event in the first capture request should have UTC timestamp "([^"]*)"')
async def timestamp(ctx, step, expected):
    instant = utc_instant(expected)
    require(instant is not None, "invalid_step_data", "Expected a canonical UTC timestamp")
    for event in first_events(ctx):
        expect(
            utc_instant(event.get("timestamp")) == instant,
            "event_timestamp",
            "Received timestamp is not the expected UTC instant",
        )
