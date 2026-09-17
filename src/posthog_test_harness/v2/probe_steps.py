"""Shared probe scenarios, with retained snapshot reads and full request history."""

from .concurrent import CAPABILITY
from .concurrent_steps import STEPS as CONCURRENT_STEPS
from .concurrent_steps import start_evaluations, wait_for_gates
from .contracts import json_equal, require
from .fixtures import FLAGS_PATHS
from .flag_steps import flag_table, flag_value, start_network_window
from .steps import Registry, expect

STEPS = Registry()
STEPS.definitions.extend(CONCURRENT_STEPS.definitions)
STEPS.requirements.update(CONCURRENT_STEPS.requirements)


def prepare(ctx, key=None, identity=None):
    start_network_window(ctx)
    ctx.probe_traffic_start = len(ctx.server.requests())
    ctx.server.gates.arm(
        "shared-probe",
        distinct_id=identity,
        flag_keys=None if key is None else [key],
        timeout_ms=min(ctx.timeout_ms + 1000, 300000),
    )


@STEPS.step(r'the clean remote response omitting "([^"]*)" is delayed')
async def omission(ctx, step, key):
    prepare(ctx, key=key)


@STEPS.step(r'the delayed remote feature flag evaluation response for distinct id "([^"]*)" returns:', "dataTable")
async def first_response(ctx, step, identity):
    values, payloads = flag_table(step)
    ctx.server.set_flags(identity, values, payloads)
    prepare(ctx, identity=identity)


@STEPS.step(r'the following remote feature flag evaluation response for distinct id "([^"]*)" returns:', "dataTable")
async def following_response(ctx, step, identity):
    ctx.server.set_flags(identity, *flag_table(step))


@STEPS.step(
    r'evaluate flags is started concurrently for distinct ids "([^"]*)" and "([^"]*)" with flag key "([^"]*)"',
    routes=("/evaluate_flags",),
    fixtures=(CAPABILITY,),
)
async def concurrent(ctx, step, first, second, key):
    require(hasattr(ctx, "probe_traffic_start"), "invalid_state", "No declared delayed probe")
    ctx.probe_task = start_evaluations(
        ctx, step, [{"distinct_id": identity, "flag_keys": [key]} for identity in (first, second)]
    )


def requests(ctx):
    return [
        (index, request)
        for index, request in enumerate(ctx.server.requests())
        if index >= ctx.probe_traffic_start and request["path"] in FLAGS_PATHS
    ]


@STEPS.step("exactly one remote feature flag evaluation request should be in flight")
async def held(ctx, step):
    indexes = await wait_for_gates(ctx, ["shared-probe"])
    ctx.held_probe_index = indexes[0]
    expect(
        len([r for _, r in requests(ctx) if r["status"] is None]) == 1,
        "probe_in_flight",
        "Expected exactly one held remote probe",
    )


@STEPS.step("the delayed remote feature flag evaluation response is released")
async def release(ctx, step):
    require(hasattr(ctx, "held_probe_index"), "invalid_state", "Probe arrival was not observed")
    ctx.server.gates.release("shared-probe")
    released_at = next(
        e["at_ms"]
        for e in ctx.server.gates.diagnostics()
        if e["gate_id"] == "shared-probe" and e["transition"] == "released"
    )
    receipts = await ctx.probe_task
    ctx.probe_snapshots = [r["completion"]["outcome"]["value"] for r in receipts]
    # An early second request can already be complete when the in-flight count is
    # observed. Inspect its arrival as well, after both native calls have settled.
    expect(
        all(index == ctx.held_probe_index or r["at_ms"] >= released_at for index, r in requests(ctx)),
        "probe_not_shared",
        "Another remote evaluation started before the shared probe was released",
    )


async def snapshot_read(ctx, route, args, reference):
    before = len(requests(ctx))
    outcome = await ctx.call(route, args, receiver=reference)
    expect(len(requests(ctx)) == before, "snapshot_network", "Retained snapshot read issued a remote evaluation")
    return outcome["value"]


@STEPS.step(r'both evaluation snapshots should not contain "([^"]*)"', routes=("/snapshot/keys",))
async def both_absent(ctx, step, key):
    snapshots = getattr(ctx, "probe_snapshots", [])
    require(len(snapshots) == 2, "invalid_state", "Expected two completed snapshots")
    for reference in snapshots:
        keys = await snapshot_read(ctx, "/snapshot/keys", {}, reference)
        expect(key not in keys, "snapshot_keys", "Omitted flag appears in an evaluation snapshot")


@STEPS.step(
    r'the (first|second) evaluation snapshot should contain "([^"]*)" with value (.+)', routes=("/snapshot/get_flag",)
)
async def value(ctx, step, ordinal, key, expected):
    snapshots = getattr(ctx, "probe_snapshots", [])
    require(len(snapshots) == 2, "invalid_state", "Expected two completed snapshots")
    actual = await snapshot_read(ctx, "/snapshot/get_flag", {"key": key}, snapshots[0 if ordinal == "first" else 1])
    expect(json_equal(actual, flag_value(expected)), "flag_value", "Evaluation snapshot value differs")


@STEPS.step("exactly two remote feature flag evaluation requests should have been sent")
async def two_requests(ctx, step):
    expect(len(requests(ctx)) == 2, "flag_request_count", "Expected two remote evaluations")
