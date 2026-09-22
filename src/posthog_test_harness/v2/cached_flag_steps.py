"""Cached flag reads prepared through the public update_flags operation.

The controlled stateful profile proves these paths. Request-scoped cache and
local-definition fixtures are separate from this cached installation state.
"""

from .analytics_steps import events
from .contracts import decode_json, json_equal, require
from .flag_steps import STEPS as SNAPSHOT_STEPS
from .flag_steps import flag_table, flag_value, network, start_network_window
from .steps import Registry, expect, no_throw, table

STEPS = Registry()
STEPS.definitions.extend(SNAPSHOT_STEPS.definitions)
STEPS.requirements.update(SNAPSHOT_STEPS.requirements)


@STEPS.step("cached feature flags are:", "dataTable", routes=("/update_flags",))
async def prepare_flags(ctx, step):
    values, payloads = flag_table(step)
    start_network_window(ctx)
    await ctx.call("/update_flags", {"flags": values, "payloads": payloads})


@STEPS.step("cached feature flags are empty", routes=("/update_flags",))
async def prepare_empty_flags(ctx, step):
    start_network_window(ctx)
    await ctx.call("/update_flags", {"flags": {}, "payloads": {}})


@STEPS.step(r'get feature flag "([^"]*)" is called(?: again)?', routes=("/get_feature_flag",))
async def value_read(ctx, step, key):
    await ctx.call("/get_feature_flag", {"key": key})


@STEPS.step(r'get feature flag "([^"]*)" is called with tracking disabled', routes=("/get_feature_flag",))
async def value_read_silent(ctx, step, key):
    await ctx.call("/get_feature_flag", {"key": key, "send_event": False})


@STEPS.step(r'get feature flag "([^"]*)" is called for distinct id "([^"]*)"', routes=("/get_feature_flag",))
async def value_read_identity(ctx, step, key, identity):
    await ctx.call("/get_feature_flag", {"key": key, "distinct_id": identity})


@STEPS.step(r'is feature enabled "([^"]*)" is called', routes=("/is_feature_enabled",))
async def enabled_read(ctx, step, key):
    await ctx.call("/is_feature_enabled", {"key": key})


@STEPS.step(r'is feature enabled "([^"]*)" is called with default value (true|false)', routes=("/is_feature_enabled",))
async def enabled_read_default(ctx, step, key, default):
    await ctx.call("/is_feature_enabled", {"key": key, "default_value": flag_value(default)})


@STEPS.step(r'is feature enabled "([^"]*)" is called with tracking disabled', routes=("/is_feature_enabled",))
async def enabled_read_silent(ctx, step, key):
    await ctx.call("/is_feature_enabled", {"key": key, "send_event": False})


@STEPS.step(r'get feature flag payload "([^"]*)" is called', routes=("/get_feature_flag_payload",))
async def payload_read(ctx, step, key):
    await ctx.call("/get_feature_flag_payload", {"key": key})


@STEPS.step(r'get feature flag result "([^"]*)" is called', routes=("/get_feature_flag_result",))
async def result_read(ctx, step, key):
    await ctx.call("/get_feature_flag_result", {"key": key})


@STEPS.step("get feature flags is called", routes=("/get_feature_flags",))
async def bulk_read(ctx, step):
    await ctx.call("/get_feature_flags", {})


@STEPS.step(r'get feature flags is called for distinct id "([^"]*)"', routes=("/get_feature_flags",))
async def bulk_read_identity(ctx, step, identity):
    await ctx.call("/get_feature_flags", {"distinct_id": identity})


@STEPS.step("get feature flags and payloads is called", routes=("/get_feature_flags_and_payloads",))
async def paired_read(ctx, step):
    await ctx.call("/get_feature_flags_and_payloads", {})


def returned(ctx, route):
    receipt = ctx.last_receipt
    require(receipt is not None and receipt["route"] == route, "invalid_state", "Expected a preceding matching getter")
    outcome = receipt["completion"].get("outcome", {})
    require(outcome.get("kind") == "value", "invalid_state", "Expected a native value outcome")
    return outcome["value"]


@STEPS.step(r"the returned feature flag value should be (.+)")
async def value_assertion(ctx, step, expected):
    expect(json_equal(returned(ctx, "/get_feature_flag"), decode_json(expected)), "flag_value", "Flag value differs")


@STEPS.step(r"the returned enabled value should be (true|false)")
async def enabled_assertion(ctx, step, expected):
    expect(
        json_equal(returned(ctx, "/is_feature_enabled"), flag_value(expected)), "flag_value", "Flag enablement differs"
    )


@STEPS.step("no payload should be returned")
async def no_payload(ctx, step):
    expect(returned(ctx, "/get_feature_flag_payload") is None, "flag_payload", "Expected an unavailable payload")


@STEPS.step("the returned payload should include:", "dataTable")
async def payload_assertion(ctx, step):
    payload = returned(ctx, "/get_feature_flag_payload")
    for row in table(step, {"field": "string", "value": "string"}):
        expect(
            isinstance(payload, dict) and row["field"] in payload and json_equal(payload[row["field"]], row["value"]),
            "flag_payload",
            "Payload field differs",
        )


@STEPS.step("no exception should be thrown")
async def no_exception(ctx, step):
    await no_throw(ctx, step)


@STEPS.step("the returned feature flag result should include:", "dataTable")
async def result_fields(ctx, step):
    value = returned(ctx, "/get_feature_flag_result")
    for row in table(step, {"field": "string", "value": "string"}):
        key, expected = row["field"], row["value"]
        require(key in ("key", "enabled", "variant", "payload"), "invalid_step_data", "Unknown flag result field")
        if key in ("enabled", "payload"):
            expected = decode_json(expected)
        expect(
            isinstance(value, dict) and key in value and json_equal(value[key], expected),
            "flag_result",
            "Structured flag result field differs",
        )


@STEPS.step("the returned feature flag result should not include a variant")
async def no_variant(ctx, step):
    # The selected FlagResult shape represents no variant as a required null field.
    value = returned(ctx, "/get_feature_flag_result")
    expect(
        isinstance(value, dict) and "variant" in value and value["variant"] is None,
        "flag_result",
        "Expected no variant",
    )


@STEPS.step("no feature flag result should be returned")
async def no_result(ctx, step):
    expect(returned(ctx, "/get_feature_flag_result") is None, "flag_result", "Expected an unavailable flag result")


@STEPS.step("the returned feature flags should be:", "dataTable")
async def bulk_values(ctx, step):
    expected, _ = flag_table(step)
    expect(json_equal(returned(ctx, "/get_feature_flags"), expected), "flag_values", "Bulk flag values differ")


@STEPS.step("the returned feature flag values should be:", "dataTable")
async def paired_values(ctx, step):
    expected, _ = flag_table(step)
    expect(
        json_equal(returned(ctx, "/get_feature_flags_and_payloads")["flags"], expected),
        "flag_values",
        "Paired flag values differ",
    )


@STEPS.step("the returned feature flag payloads should be:", "dataTable")
async def paired_payloads(ctx, step):
    rows = table(step, {"key": "string", "payload": "json"})
    require(len({row["key"] for row in rows}) == len(rows), "invalid_step_data", "Duplicate payload key")
    expected = {row["key"]: row["payload"] for row in rows}
    expect(
        json_equal(returned(ctx, "/get_feature_flags_and_payloads")["payloads"], expected),
        "flag_payloads",
        "Paired flag payloads differ",
    )


@STEPS.step("the returned feature flag values should be empty")
async def empty_values(ctx, step):
    expect(
        json_equal(returned(ctx, "/get_feature_flags_and_payloads")["flags"], {}), "flag_values", "Expected empty flags"
    )


@STEPS.step("the returned feature flag payloads should be empty")
async def empty_payloads(ctx, step):
    expect(
        json_equal(returned(ctx, "/get_feature_flags_and_payloads")["payloads"], {}),
        "flag_payloads",
        "Expected empty payloads",
    )


@STEPS.step(r'cached feature flag "([^"]*)" changes to "([^"]*)"', routes=("/update_flags",))
async def change_flag(ctx, step, key, value):
    await ctx.call("/update_flags", {"flags": {key: flag_value(value)}, "merge": True})


async def exposure_count(ctx, key, count):
    matches = [
        event
        for event in await events(ctx)
        if event.get("event") == "$feature_flag_called" and event.get("properties", {}).get("$feature_flag") == key
    ]
    expect(len(matches) == count, "flag_exposure_count", "Flag exposure count differs")


@STEPS.step(
    r'exactly one event named "\$feature_flag_called" should be enqueued for flag "([^"]*)"',
    fixtures=("queue.snapshot.v1",),
)
async def one_exposure(ctx, step, key):
    await exposure_count(ctx, key, 1)


@STEPS.step(
    r'two "\$feature_flag_called" events should be enqueued for flag "([^"]*)"', fixtures=("queue.snapshot.v1",)
)
async def two_exposures(ctx, step, key):
    await exposure_count(ctx, key, 2)


@STEPS.step("no feature flag network request should be sent")
async def no_flags_network(ctx, step):
    expect(not network(ctx), "flag_request_count", "Unexpected flag request during cached access")


@STEPS.step("reset is called", routes=("/reset",))
async def reset(ctx, step):
    await ctx.call("/reset", {})


@STEPS.step("cached feature flags should be empty", routes=("/get_feature_flags",))
async def cache_empty(ctx, step):
    outcome = await ctx.call("/get_feature_flags", {})
    expect(json_equal(outcome["value"], {}), "flag_values", "Expected empty cached flag values")
