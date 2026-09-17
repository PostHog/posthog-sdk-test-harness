"""Public analytics/profile calls and native queue observations.

Each call binding issues one public operation. Queue assertions use the declared
component observation, never request-derived counts or adapter shadow state.
"""

from datetime import datetime
from uuid import UUID

from .contracts import json_equal, require
from .steps import FLUSH_STEPS, Registry, expect, flush, table

STEPS = Registry()
STEPS.definitions.extend(FLUSH_STEPS.definitions)
STEPS.requirements.update(FLUSH_STEPS.requirements)


def properties(step):
    """These feature tables declare textual property values; JSON uses doc strings."""
    rows = table(step, {"property": "string", "value": "string"})
    require(len({r["property"] for r in rows}) == len(rows), "invalid_step_data", "Duplicate property key")
    return {r["property"]: r["value"] for r in rows}


@STEPS.step(
    r'capture is called with distinct id "([^"]*)", event "([^"]*)", and properties:', "dataTable", routes=("/capture",)
)
async def capture_explicit(ctx, step, identity, event):
    await ctx.call("/capture", {"distinct_id": identity, "event": event, "properties": properties(step)})


@STEPS.step(r'capture is called with event "([^"]*)"', routes=("/capture",))
async def capture(ctx, step, event):
    await ctx.call("/capture", {"event": event})


@STEPS.step(r'capture is called with event "([^"]*)" and properties:', "dataTable", routes=("/capture",))
async def capture_properties(ctx, step, event):
    await ctx.call("/capture", {"event": event, "properties": properties(step)})


@STEPS.step(r'identify is called with distinct id "([^"]*)" and properties:', "dataTable", routes=("/identify",))
async def identify(ctx, step, identity):
    await ctx.call("/identify", {"distinct_id": identity, "set": properties(step)})


@STEPS.step(r'identify is called with distinct id "([^"]*)"', routes=("/identify",))
async def identify_only(ctx, step, identity):
    await ctx.call("/identify", {"distinct_id": identity})


@STEPS.step(r'alias is called with previous distinct id "([^"]*)" and distinct id "([^"]*)"', routes=("/alias",))
async def alias(ctx, step, previous, identity):
    await ctx.call("/alias", {"distinct_id": previous, "alias": identity})


@STEPS.step("the SDK is flushed", routes=("/flush",), fixtures=("queue.snapshot.v1",))
async def sdk_flushed(ctx, step):
    await flush(ctx, step)


async def events(ctx):
    return [record["event"] for record in await ctx.controls.command("queue_snapshot")]


@STEPS.step(r'one event named "([^"]*)" should be enqueued', fixtures=("queue.snapshot.v1",))
async def enqueued(ctx, step, name):
    matches = [event for event in await events(ctx) if event.get("event") == name]
    expect(len(matches) == 1, "event_count", f"Expected exactly one queued event named {name}")
    ctx.selected_event = matches[0]


@STEPS.step("no event should be enqueued", fixtures=("queue.snapshot.v1",))
async def no_events(ctx, step):
    expect(not await events(ctx), "unexpected_event", "Expected an empty event queue")


@STEPS.step(r'no event named "([^"]*)" should be enqueued', fixtures=("queue.snapshot.v1",))
async def no_named_event(ctx, step, name):
    expect(
        not any(event.get("event") == name for event in await events(ctx)),
        "unexpected_event",
        f"Unexpected queued event named {name}",
    )


def selected(ctx):
    require(ctx.selected_event is not None, "invalid_state", "Select one observed event before asserting its fields")
    return ctx.selected_event


def property_value(event, path):
    # Dotted feature notation addresses nested profile maps such as $set.email.
    value = event.get("properties", {})
    require(isinstance(value, dict), "invalid_observation", "Event properties must be an object")
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


@STEPS.step(r'the enqueued event distinct id should be "([^"]*)"')
async def event_identity(ctx, step, identity):
    expect(selected(ctx).get("distinct_id") == identity, "event_identity", "Event distinct id differs")


@STEPS.step("the enqueued event properties should include:", "dataTable")
async def event_properties(ctx, step):
    for key, expected in properties(step).items():
        present, actual = property_value(selected(ctx), key)
        if key == "$feature_flag_response":
            expected = {"true": True, "false": False}.get(expected, expected)
        # The existing server capture feature uses 'any' specifically for $lib.
        matches = (
            isinstance(actual, str) and bool(actual)
            if key == "$lib" and expected == "any"
            else json_equal(actual, expected)
        )
        expect(present and matches, "event_property", f"Event property differs: {key}")


@STEPS.step(r'the enqueued event property "([^"]*)" should equal "([^"]*)"')
async def event_property(ctx, step, key, expected):
    present, actual = property_value(selected(ctx), key)
    expect(present and json_equal(actual, expected), "event_property", f"Event property differs: {key}")


@STEPS.step("the enqueued event should include an event uuid")
async def event_uuid(ctx, step):
    value = selected(ctx).get("uuid")
    try:
        valid = isinstance(value, str) and bool(UUID(value))
    except ValueError:
        valid = False
    expect(valid, "event_uuid", "Expected a valid event UUID")


@STEPS.step("the enqueued event should include a timestamp and uuid")
async def event_timestamp_uuid(ctx, step):
    await event_uuid(ctx, step)
    value = selected(ctx).get("timestamp")
    try:
        valid = isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        valid = False
    expect(valid, "event_timestamp", "Expected an offset-qualified event timestamp")
