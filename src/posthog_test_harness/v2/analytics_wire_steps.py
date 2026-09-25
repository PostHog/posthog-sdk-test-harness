"""Analytics-v1 parity assertions on accumulated received requests.

Header/body checks observe request zero; event shape/timestamps observe all its
parsed events, while UUID, identity type and placement observe only its first event.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from uuid import UUID

from .ai_steps import STEPS as PREVIOUS_STEPS
from .ai_steps import first_events, json_arguments, requests, utc_instant
from .contracts import decode_json, json_equal
from .steps import Registry, expect, table

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


def first_request(ctx):
    observed = requests(ctx)
    expect(bool(observed), "missing_request", "No requests received")
    return observed[0]


def header(ctx, name):
    value = first_request(ctx).headers.get(name.lower())
    expect(value is not None, "missing_header", f"First request is missing {name}")
    return value


@STEPS.step(r'the first request should authenticate with bearer token "([^"]*)"')
async def bearer(ctx, step, token):
    value = header(ctx, "Authorization")
    parts = value.split(None, 1)
    expect(
        value.lower().startswith("bearer ") and len(parts) == 2 and parts[1].strip() == token,
        "bearer_token",
        "First request bearer authentication differs",
    )


@STEPS.step(r'the first request header "([^"]*)" should match "([^"]*)"')
async def header_matches(ctx, step, name, pattern):
    expect(re.match(pattern, header(ctx, name)) is not None, "header_pattern", f"Header does not match: {name}")


@STEPS.step(r'the first request header "([^"]*)" should be integer ([0-9]+)')
async def header_integer(ctx, step, name, expected):
    value = header(ctx, name)
    try:
        actual = int(value)
    except ValueError:
        actual = None
    expect(actual == int(expected), "header_integer", f"Header integer differs: {name}")


@STEPS.step(r'the first request header "([^"]*)" should be a valid UUID')
async def header_uuid(ctx, step, name):
    value = header(ctx, name)
    try:
        valid = bool(UUID(value))
    except ValueError:
        valid = False
    expect(valid, "header_uuid", f"Header is not a UUID: {name}")


@STEPS.step(r'the first request header "([^"]*)" should be a canonical UTC timestamp')
async def header_timestamp(ctx, step, name):
    expect(utc_instant(header(ctx, name)) is not None, "header_timestamp", f"Header is not canonical UTC: {name}")


@STEPS.step("the first request body should have a canonical UTC created_at and a nonempty batch array")
async def body_format(ctx, step):
    body = first_request(ctx).body_decompressed
    try:
        data = json.loads(body) if body else None
    except json.JSONDecodeError:
        data = None
    expect(isinstance(data, dict), "body_format", "First request body is not a JSON object")
    expect(utc_instant(str(data.get("created_at"))) is not None, "body_format", "Missing or non-UTC created_at")
    expect(isinstance(data.get("batch"), list) and bool(data["batch"]), "body_format", "Missing or empty batch array")


@STEPS.step("the first request body should omit these root fields:", "dataTable")
async def body_omissions(ctx, step):
    fields = [row["field"] for row in table(step, {"field": "string"})]
    body = first_request(ctx).body_decompressed
    # The source omission assertion is not a JSON-format assertion.
    if not body:
        return
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return
    for field in fields:
        expect(field not in data, "body_field_present", f"First request body contains {field}")


@STEPS.step("every event in the first capture request should contain these root fields:", "dataTable")
async def event_fields(ctx, step):
    required = {row["field"] for row in table(step, {"field": "string"})}
    for event in first_events(ctx):
        expect(required <= event.keys(), "event_fields", "Received event is missing required root fields")


@STEPS.step("every event in the first capture request should have a canonical UTC timestamp")
async def event_timestamps(ctx, step):
    for event in first_events(ctx):
        expect(utc_instant(str(event.get("timestamp"))) is not None, "event_timestamp", "Event timestamp is not UTC")


@STEPS.step(r'the first received event field "([^"]*)" should be a string')
async def event_string(ctx, step, name):
    expect(isinstance(first_events(ctx)[0].get(name), str), "event_string", f"Event field is not a string: {name}")


@STEPS.step(r'the first received event should contain "([^"]*)" at root and not in properties')
async def event_placement(ctx, step, name):
    event = first_events(ctx)[0]
    props = event.get("properties", {})
    expect(
        name in event and not (isinstance(props, dict) and name in props),
        "event_placement",
        f"Event field is missing at root or present in properties: {name}",
    )


@STEPS.step(r'the SDK is initialized with token "([^"]*)" and no additional configuration', routes=("/setup",))
async def setup_defaults(ctx, step, token):
    await ctx.call("/setup", {"project_token": token, "config": {"host": ctx.server.url}})


@STEPS.step(
    r"capture is called sequentially ([0-9]+) times with zero-based top-level index substitution:",
    "docString",
    routes=("/capture",),
)
async def capture_sequence(ctx, step, count):
    template = json_arguments(step)
    for index in range(int(count)):
        args = {key: value.format(index=index) if isinstance(value, str) else value for key, value in template.items()}
        await ctx.call("/capture", args)


@STEPS.step(r'the first received event property "([^"]*)" should equal JSON (.+)')
async def property_json(ctx, step, name, encoded):
    expected = decode_json(encoded)
    properties = first_events(ctx)[0].get("properties", {})
    expect(
        isinstance(properties, dict) and name in properties and json_equal(properties[name], expected),
        "event_property",
        f"Received property differs: {name}",
    )


@STEPS.step(r'the first received event property "([^"]*)" should be an object')
async def property_object(ctx, step, name):
    properties = first_events(ctx)[0].get("properties", {})
    expect(
        isinstance(properties, dict) and isinstance(properties.get(name), dict),
        "event_property_object",
        f"Property is not an object: {name}",
    )


@STEPS.step(r'the first received event should contain root field "([^"]*)"')
async def event_presence(ctx, step, name):
    expect(name in first_events(ctx)[0], "event_field_missing", f"Event is missing root field: {name}")


@STEPS.step(r"the first request should contain exactly ([0-9]+) parsed events")
async def batch_count(ctx, step, count):
    expect(len(first_request(ctx).parsed_events or []) == int(count), "batch_count", f"Expected {count} parsed events")


@STEPS.step(r"at least ([0-9]+) capture request should have been received")
async def count_at_least(ctx, step, count):
    expect(len(requests(ctx)) >= int(count), "request_count", f"Expected at least {count} capture requests")


@STEPS.step(r"([0-9]+) milliseconds elapse without a public SDK call")
async def elapsed(ctx, step, milliseconds):
    await asyncio.sleep(int(milliseconds) / 1000)


@STEPS.step(r"the first request created_at should be within ([0-9]+) seconds of the current wall clock")
async def created_at_recent(ctx, step, seconds):
    body = first_request(ctx).body_decompressed
    try:
        data = json.loads(body) if body else None
    except json.JSONDecodeError:
        data = None
    value = data.get("created_at") if isinstance(data, dict) else None
    expect(utc_instant(value) is not None, "created_at_recent", "Missing or invalid UTC created_at")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    distance = abs((datetime.now(timezone.utc) - instant).total_seconds())
    expect(distance <= int(seconds), "created_at_recent", f"created_at differs from wall clock by {distance}s")


def received_uuids(ctx):
    """The source uniqueness checks collect present UUID fields across all requests."""
    return [event["uuid"] for request in requests(ctx) for event in (request.parsed_events or []) if "uuid" in event]


@STEPS.step("all present UUIDs across received requests should be unique")
async def unique_uuids(ctx, step):
    values = received_uuids(ctx)
    expect(len(set(values)) == len(values), "uuid_unique", "Received UUIDs are not unique")


@STEPS.step("the first two present UUIDs across received requests should differ")
async def different_uuids(ctx, step):
    values = received_uuids(ctx)
    expect(len(values) >= 2, "uuid_pair", "Need at least two present UUIDs")
    expect(values[0] != values[1], "uuid_pair", "First two present UUIDs are equal")
