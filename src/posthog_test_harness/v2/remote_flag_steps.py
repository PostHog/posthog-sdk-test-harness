"""Native getter migration and feature-flags subscription observations.

Wire assertions retain the pinned YAML helper scopes and Python equality.
Callback observations come from the installed native callback, never a getter.
"""

import json

from ..types import MockResponse
from .ai_steps import json_arguments, requests
from .capture_amendment_steps import STEPS as PREVIOUS_STEPS
from .contracts import BoundaryError, decode_json, json_equal
from .flag_steps import flag_table
from .steps import Registry, expect

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


@STEPS.step(
    "the server uses its native flag startup and getter behavior with no installed local definitions or results"
)
async def native_server(ctx, step):
    if ctx.profile.get("sdk_type") != "server":
        raise BoundaryError("sdk_type_unavailable", "Requires server SDK context", "unsupported_binding")


@STEPS.step("get feature flag is called with JSON arguments:", "docString", routes=("/get_feature_flag",))
async def getter(ctx, step):
    # YAML getter actions require successful completion; result expectations belong
    # to their explicit assertion steps, not the catalog's normal result target.
    ctx.remote_flag_arguments = json_arguments(step)
    ctx.remote_flag_outcome = await ctx.call("/get_feature_flag", ctx.remote_flag_arguments, check_result=False)
    expect(ctx.remote_flag_outcome["kind"] != "thrown", "flag_getter_thrown", "Native flag getter threw")


@STEPS.step("the mock serves these ordered flag responses:", "docString")
async def responses(ctx, step):
    args = json_arguments(step)
    ctx.server.use_response_queue_for_flags = True
    ctx.server.state.set_response_queue([MockResponse(**r) for r in args["responses"]])


def flags_requests(ctx):
    return [r for r in requests(ctx) if "/flags" in r.path]


@STEPS.step(r"exactly ([0-9]+) requests containing /flags should have been received")
async def count(ctx, step, expected):
    expect(len(flags_requests(ctx)) == int(expected), "flag_request_count", "Flags request count differs")


def first_flags(ctx):
    found = flags_requests(ctx)
    expect(bool(found), "flag_request_missing", "No flags request received")
    return found[0]


@STEPS.step(r'the first flags request field "([^"]*)" should equal JSON (.+)')
async def field(ctx, step, path, expected):
    request = first_flags(ctx)
    body = request.body_decompressed
    expect(bool(body), "flag_request_body", "Empty flags body")
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        expect(False, "flag_request_body", "Flags body is not JSON")
    expected = decode_json(expected)
    details = {"operation": request.path, "field": path, "expected": expected}
    for index, part in enumerate(path.split(".")):
        expect(
            isinstance(value, dict),
            "flag_request_field",
            "Cannot traverse flags field",
            details={**details, "actual": {"kind": "untraversable", "at": ".".join(path.split(".")[:index])}},
        )
        if index == 0 and part == "token" and "token" not in value and "api_key" in value:
            part = "api_key"
        expect(
            part in value,
            "flag_request_field",
            "Flags field missing",
            details={**details, "actual": {"kind": "missing"}},
        )
        value = value[part]
    expect(
        value == expected,
        "flag_request_field",
        "Flags field differs",
        details={**details, "actual": {"kind": "value", "value": value}},
    )


@STEPS.step(r'the first flags request query parameter "([^"]*)" should equal "([^"]*)"')
async def query(ctx, step, key, expected):
    expect(first_flags(ctx).query_params.get(key) == expected, "flag_request_query", "Flags query differs")


@STEPS.step(r"the public flag getter should return JSON (.+)")
async def result(ctx, step, encoded):
    outcome = ctx.remote_flag_outcome
    expected = decode_json(encoded)
    expect(
        outcome["kind"] == "value" and outcome["value"] == expected,
        "flag_value",
        "Getter value differs",
        details={
            "operation": "/get_feature_flag",
            "arguments": ctx.remote_flag_arguments,
            "expected": expected,
            "actual": outcome,
        },
    )


def named_events(ctx, name):
    return [
        e for r in requests(ctx) if "/flags" not in r.path for e in (r.parsed_events or []) if e.get("event") == name
    ]


@STEPS.step(r'exactly ([0-9]+) received events should be named "([^"]*)"')
async def event_count(ctx, step, expected, name):
    expect(len(named_events(ctx, name)) == int(expected), "flag_event_count", "Received named event count differs")


@STEPS.step(r'a received event named "([^"]*)" should have property "([^"]*)" equal to JSON (.+)')
async def event_property(ctx, step, name, key, encoded):
    expected = decode_json(encoded)
    found = named_events(ctx, name)
    expect(bool(found), "flag_event_property", "No matching received event")
    props = found[0].get("properties", {})
    expect(
        isinstance(props, dict) and key in props and props[key] == expected,
        "flag_event_property",
        "First matching event property differs",
    )


@STEPS.step(
    "a feature flag listener is registered", routes=("/on_feature_flags",), fixtures=("callbacks.continuation",)
)
async def register(ctx, step):
    plan = {
        "signature": "on_feature_flags",
        "max_invocations": 20,
        "calls": [],
        "returns": {"source": "literal", "outcome": {"kind": "void"}},
    }
    ctx.flag_callback = await ctx.fixture.reference("feature-flags-listener", {"kind": "callback", "plan": plan})
    ctx.flag_subscription = (await ctx.call("/on_feature_flags", {}, references={"/callback": ctx.flag_callback}))[
        "value"
    ]


@STEPS.step(r"feature flags are (?:already )?loaded with values:", "dataTable", routes=("/update_flags",))
async def loaded(ctx, step):
    values, payloads = flag_table(step)
    await ctx.call("/update_flags", {"flags": values, "payloads": payloads})


async def callbacks(ctx):
    await ctx.fixture.observe()
    return [o for o in ctx.fixture.observations if o["kind"] == "callback" and o["callback"] == ctx.flag_callback]


@STEPS.step("the feature flag listener should be invoked with flags:", "dataTable")
async def callback_values(ctx, step):
    expected, _ = flag_table(step)
    observed = await callbacks(ctx)
    expect(bool(observed), "flag_callback_missing", "Listener was not invoked")
    args = observed[-1]["args"]
    expect(
        len(args) in (2, 3)
        and args[0]["kind"] == args[1]["kind"] == "value"
        and json_equal(args[0]["value"], list(expected))
        and json_equal(args[1]["value"], expected),
        "flag_callback_values",
        "Native callback values differ",
    )


@STEPS.step("the feature flag listener is unsubscribed", routes=("/subscription/unsubscribe",))
async def unsubscribe(ctx, step):
    ctx.flag_callback_count = len(await callbacks(ctx))
    await ctx.call("/subscription/unsubscribe", {}, receiver=ctx.flag_subscription)


@STEPS.step("the feature flag listener should not be invoked again")
async def removed(ctx, step):
    expect(len(await callbacks(ctx)) == ctx.flag_callback_count, "flag_callback_unsubscribe", "Removed listener fired")
