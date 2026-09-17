"""Remote evaluation and retained-snapshot Gherkin bindings.

Service fixtures supply data, not SDK answers. All snapshot observations invoke
public retained-object operations; capture passes the original reference.
"""

from dataclasses import dataclass, field

from .analytics_steps import STEPS as ANALYTICS_STEPS
from .analytics_steps import events, property_value
from .contracts import decode_json, json_equal, require
from .steps import Registry, expect, table

STEPS = Registry()
STEPS.definitions.extend(ANALYTICS_STEPS.definitions)
STEPS.requirements.update(ANALYTICS_STEPS.requirements)


@dataclass
class FlagState:
    snapshot: dict | None = None
    filtered: dict | None = None
    network_start: int | None = None
    evaluation_requests: int | None = None
    expected_values: dict = field(default_factory=dict)
    expected_payloads: dict = field(default_factory=dict)
    reads: dict = field(default_factory=dict)
    captured: dict | None = None
    definitions: dict = field(default_factory=dict)
    activity_before: dict | None = None
    expected_failure_status: int | None = None


def state(ctx):
    if ctx.flags is None:
        ctx.flags = FlagState()
    return ctx.flags


def flag_value(text):
    # The feature vocabulary declares bool-or-variant, not arbitrary JSON.
    return {"true": True, "false": False}.get(text, text)


def flag_table(step):
    headers = [cell["value"] for cell in step.argument["dataTable"]["rows"][0]["cells"]]
    types = {"key": "string", "value": "string"}
    if "payload" in headers:
        types["payload"] = "string"
    rows = table(step, types)
    require(len({row["key"] for row in rows}) == len(rows), "invalid_step_data", "Duplicate flag key")
    values = {row["key"]: flag_value(row["value"]) for row in rows}
    payloads = {row["key"]: decode_json(row["payload"]) for row in rows if row.get("payload", "") != ""}
    return values, payloads


def start_network_window(ctx):
    flags = state(ctx)
    if flags.network_start is None:
        flags.network_start = len(ctx.server.flag_requests())
        ctx.diagnostics["flag_window_start"] = flags.network_start


def network(ctx):
    start = state(ctx).network_start
    require(start is not None, "invalid_state", "No remote evaluation observation window")
    return ctx.server.flag_requests()[start:]


@STEPS.step(r'remote feature flag evaluation for distinct id "([^"]*)" (?:returns|can return):', "dataTable")
async def remote_flags(ctx, step, identity):
    flags = state(ctx)
    start_network_window(ctx)
    values, payloads = flag_table(step)
    flags.expected_values, flags.expected_payloads = values, payloads
    ctx.server.set_flags(identity, values, payloads)


async def evaluate(ctx, args):
    flags = state(ctx)
    start_network_window(ctx)
    request_start = len(network(ctx))
    outcome = await ctx.call("/evaluate_flags", args)
    if flags.expected_failure_status is not None:
        expect(
            any(request.response_status == flags.expected_failure_status for request in network(ctx)[request_start:]),
            "flag_failure_not_exercised",
            "Evaluation did not encounter the configured remote failure",
        )
        flags.expected_failure_status = None
    flags.snapshot = outcome["value"]
    flags.filtered = None
    flags.reads.clear()
    flags.evaluation_requests = len(network(ctx))


@STEPS.step(r'evaluate flags is called for distinct id "([^"]*)"', routes=("/evaluate_flags",))
async def evaluate_identity(ctx, step, identity):
    await evaluate(ctx, {"distinct_id": identity})


@STEPS.step(
    r'evaluate flags is called for distinct id "([^"]*)" with flag keys:', "dataTable", routes=("/evaluate_flags",)
)
async def evaluate_keys(ctx, step, identity):
    keys = [row["key"] for row in table(step, {"key": "string"})]
    await evaluate(ctx, {"distinct_id": identity, "flag_keys": keys})


async def snapshot_call(ctx, operation, args, *, filtered=False):
    reference = state(ctx).filtered if filtered else state(ctx).snapshot
    require(reference is not None, "invalid_state", "No retained snapshot for this step")
    outcome = await ctx.call("/snapshot/" + operation, args, receiver=reference)
    return outcome["value"]


READS = {"enablement": "is_enabled", "value": "get_flag", "payload": "get_flag_payload"}


async def read_snapshot(ctx, kind, key):
    value = await snapshot_call(ctx, READS[kind], {"key": key})
    state(ctx).reads.setdefault((kind, key), []).append(value)


@STEPS.step(r'snapshot enablement is read(?: again)? for "([^"]*)"', routes=("/snapshot/is_enabled",))
async def read_enablement(ctx, step, key):
    await read_snapshot(ctx, "enablement", key)


@STEPS.step(r'snapshot value is read(?: again)? for "([^"]*)"', routes=("/snapshot/get_flag",))
async def read_value(ctx, step, key):
    await read_snapshot(ctx, "value", key)


@STEPS.step(r'snapshot payload is read for "([^"]*)"', routes=("/snapshot/get_flag_payload",))
async def read_payload(ctx, step, key):
    await read_snapshot(ctx, "payload", key)


@STEPS.step("snapshot keys are enumerated", routes=("/snapshot/keys",))
async def read_keys(ctx, step):
    state(ctx).reads["keys"] = await snapshot_call(ctx, "keys", {})


def reads_equal(reads, kind, key, expected):
    values = reads.get((kind, key), [])
    return bool(values) and all(json_equal(value, expected) for value in values)


@STEPS.step(r'the returned enabled value for "([^"]*)" should be (true|false)')
async def enabled_result(ctx, step, key, expected):
    reads = state(ctx).reads
    expect(
        reads_equal(reads, "enablement", key, flag_value(expected)),
        "flag_value",
        "Snapshot enablement differs",
    )


@STEPS.step(r'the returned snapshot value for "([^"]*)" should be (.+)')
async def value_result(ctx, step, key, expected):
    reads = state(ctx).reads
    expect(
        reads_equal(reads, "value", key, decode_json(expected)),
        "flag_value",
        "Snapshot value differs",
    )


@STEPS.step(r'the returned snapshot payload for "([^"]*)" should be `(.+)`')
async def payload_result(ctx, step, key, expected):
    reads = state(ctx).reads
    expect(
        reads_equal(reads, "payload", key, decode_json(expected)),
        "flag_payload",
        "Snapshot payload differs",
    )


@STEPS.step(r'snapshot enablement for "([^"]*)" should be (true|false)', routes=("/snapshot/is_enabled",))
async def enabled_assertion(ctx, step, key, expected):
    value = await snapshot_call(ctx, "is_enabled", {"key": key})
    expect(json_equal(value, flag_value(expected)), "flag_value", "Snapshot enablement differs")


@STEPS.step(r'snapshot value for "([^"]*)" should be (.+)', routes=("/snapshot/get_flag",))
async def value_assertion(ctx, step, key, expected):
    value = await snapshot_call(ctx, "get_flag", {"key": key})
    expected_value = None if expected == "absent" else decode_json(expected)
    expect(json_equal(value, expected_value), "flag_value", "Snapshot value differs")


@STEPS.step("exactly one remote feature flag evaluation request should have been sent")
async def one_evaluation(ctx, step):
    expect(len(network(ctx)) == 1, "flag_request_count", "Expected exactly one remote evaluation request")


@STEPS.step("no additional remote feature flag evaluation request should have been sent")
async def no_additional_evaluation(ctx, step):
    count = state(ctx).evaluation_requests
    require(count is not None, "invalid_state", "No completed evaluation to compare")
    expect(len(network(ctx)) == count, "flag_request_count", "Snapshot operation performed another evaluation")


@STEPS.step(r'the remote feature flag evaluation request should include only flag keys "([^"]*)" and "([^"]*)"')
async def request_keys(ctx, step, first, second):
    requests = network(ctx)
    expect(len(requests) == 1, "flag_request_count", "Expected exactly one scoped evaluation")
    body = decode_json(requests[0].body_decompressed)
    keys = body.get("flag_keys_to_evaluate") if isinstance(body, dict) else None
    expect(
        isinstance(keys, list)
        and len(keys) == 2
        and all(isinstance(k, str) for k in keys)
        and set(keys) == {first, second},
        "flag_request_keys",
        "Remote request has the wrong evaluation scope",
    )


@STEPS.step(
    "snapshot only accessed is called(?: before a value or enablement read)?", routes=("/snapshot/only_accessed",)
)
async def only_accessed(ctx, step):
    state(ctx).filtered = await snapshot_call(ctx, "only_accessed", {})


@STEPS.step(r'snapshot only is called with flag keys "([^"]*)" and "([^"]*)"', routes=("/snapshot/only",))
async def only(ctx, step, first, second):
    state(ctx).filtered = await snapshot_call(ctx, "only", {"keys": [first, second]})


async def snapshot_keys(ctx, *, filtered=False):
    keys = await snapshot_call(ctx, "keys", {}, filtered=filtered)
    expect(len(keys) == len(set(keys)), "snapshot_keys", "Snapshot keys contain duplicates")
    return keys


@STEPS.step("the filtered snapshot should contain no flags", routes=("/snapshot/keys",))
async def filtered_empty(ctx, step):
    expect(await snapshot_keys(ctx, filtered=True) == [], "snapshot_keys", "Filtered snapshot is not empty")


@STEPS.step("snapshot only accessed should return no flags", routes=("/snapshot/only_accessed", "/snapshot/keys"))
async def only_accessed_empty(ctx, step):
    await only_accessed(ctx, step)
    await filtered_empty(ctx, step)


@STEPS.step("the filtered snapshot should contain flags:", "dataTable", routes=("/snapshot/keys", "/snapshot/get_flag"))
async def filtered_values(ctx, step):
    expected, _ = flag_table(step)
    keys = await snapshot_keys(ctx, filtered=True)
    expect(set(keys) == set(expected), "snapshot_keys", "Filtered snapshot key set differs")
    for key, expected_value in expected.items():
        actual = await snapshot_call(ctx, "get_flag", {"key": key}, filtered=True)
        expect(json_equal(actual, expected_value), "flag_value", "Filtered snapshot value differs")


@STEPS.step(r'the (filtered snapshot|snapshot) should not contain "([^"]*)"', routes=("/snapshot/keys",))
async def absent_key(ctx, step, which, key):
    expect(
        key not in await snapshot_keys(ctx, filtered=which == "filtered snapshot"),
        "snapshot_keys",
        "Snapshot contains an excluded key",
    )


@STEPS.step(r'the filtered snapshot should contain only "([^"]*)"', routes=("/snapshot/keys",))
async def only_key(ctx, step, key):
    expect(await snapshot_keys(ctx, filtered=True) == [key], "snapshot_keys", "Filtered snapshot key set differs")


@STEPS.step(
    r'an event named "([^"]*)" is captured for distinct id "([^"]*)" with the evaluation snapshot',
    routes=("/capture",),
    fixtures=("queue.snapshot.v1",),
)
async def capture_snapshot(ctx, step, event, identity):
    reference = state(ctx).snapshot
    require(reference is not None, "invalid_state", "No snapshot to capture")
    await ctx.call("/capture", {"event": event, "distinct_id": identity}, references={"/flags": reference})
    matches = [e for e in await events(ctx) if e.get("event") == event]
    expect(len(matches) == 1, "event_count", "Expected exactly one captured snapshot event")
    state(ctx).captured = matches[0]


def captured(ctx):
    event = state(ctx).captured
    require(event is not None, "invalid_state", "No observed snapshot capture")
    return event


@STEPS.step(r'the captured event should have property "([^"]*)" equal to (.+)')
async def capture_value(ctx, step, key, expected):
    present, value = property_value(captured(ctx), key)
    expect(present and json_equal(value, decode_json(expected)), "event_property", "Captured flag value differs")


@STEPS.step(r'the captured event property "([^"]*)" should (not )?contain "([^"]*)"')
async def capture_membership(ctx, step, key, negate, expected):
    present, value = property_value(captured(ctx), key)
    expect(
        present and isinstance(value, list) and ((expected in value) == (not negate)),
        "event_property",
        "Captured active flags differ",
    )


@STEPS.step("the snapshot should expose the boolean, value, payload, and both keys from the same evaluation")
async def consistent_reads(ctx, step):
    flags = state(ctx)
    expected = flags.expected_values
    reads = flags.reads
    keys = reads.get("keys")
    expect(
        isinstance(keys, list) and len(keys) == len(expected) and set(keys) == set(expected),
        "snapshot_keys",
        "Enumerated snapshot keys differ",
    )
    for key, value in expected.items():
        kind = "enablement" if isinstance(value, bool) else "value"
        expect(reads_equal(reads, kind, key, value), "flag_value", "Snapshot reads differ")
    for key, value in flags.expected_payloads.items():
        expect(
            reads_equal(reads, "payload", key, value),
            "flag_payload",
            "Snapshot payload reads differ",
        )


@STEPS.step(
    r'(?:a|only one deduped) "\$feature_flag_called" event should be enqueued for flag "([^"]*)" with value "([^"]*)"',
    fixtures=("queue.snapshot.v1",),
)
async def exposure(ctx, step, key, expected):
    matches = [
        e
        for e in await events(ctx)
        if e.get("event") == "$feature_flag_called" and e.get("properties", {}).get("$feature_flag") == key
    ]
    expect(len(matches) == 1, "flag_exposure_count", "Expected one deduplicated flag exposure")
    props = matches[0].get("properties", {})
    expect(
        "$feature_flag_response" in props and json_equal(props["$feature_flag_response"], flag_value(expected)),
        "flag_exposure_value",
        "Flag exposure value differs",
    )
