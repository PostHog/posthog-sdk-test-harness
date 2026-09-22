"""Legacy capture wire assertions at the pinned first-request observation scope."""

import json

from .ai_steps import first_events
from .analytics_outcome_steps import STEPS as PREVIOUS_STEPS
from .analytics_wire_steps import first_request
from .steps import Registry, expect

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


@STEPS.step(r'the first received event should contain property "([^"]*)"')
async def property_presence(ctx, step, name):
    expect(name in first_events(ctx)[0].get("properties", {}), "event_property_missing", f"Missing property: {name}")


@STEPS.step(r'the first request should contain token "([^"]*)" at event token or body api_key or token')
async def request_token(ctx, step, token):
    request = first_request(ctx)
    if any(event.get("token") == token for event in (request.parsed_events or [])):
        return
    if request.body_decompressed:
        try:
            body = json.loads(request.body_decompressed)
        except json.JSONDecodeError:
            body = {}
        if isinstance(body, dict) and (body.get("api_key") == token or body.get("token") == token):
            return
    expect(False, "request_token", "Token not found at an accepted location in the first request")


@STEPS.step(r'an event in the first request should resolve token "([^"]*)" from event then property token or api_key')
async def event_token(ctx, step, token):
    request = first_request(ctx)
    for event in request.parsed_events or []:
        # Preserve the source's first-truthy precedence, not an any-field match.
        value = (
            event.get("token")
            or event.get("api_key")
            or event.get("properties", {}).get("token")
            or event.get("properties", {}).get("api_key")
        )
        if value == token:
            return
    expect(False, "event_token", "Token not found in any event of the first request")


@STEPS.step(r"the first request body should contain a batch array( and an api_key field)?")
async def batch_format(ctx, step, api_key):
    body = first_request(ctx).body_decompressed
    try:
        data = json.loads(body) if body else None
    except json.JSONDecodeError:
        data = None
    expect(isinstance(data, dict) and isinstance(data.get("batch"), list), "legacy_batch", "Missing batch array")
    if api_key:
        expect("api_key" in data, "legacy_batch", "Missing api_key field")
