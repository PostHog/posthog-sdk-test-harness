"""Concurrent disjoint probes, witnessed by independently held HTTP requests."""

import asyncio
from copy import deepcopy

from .concurrent import CAPABILITY, invoke_concurrent
from .contracts import BoundaryError, require
from .flag_steps import state
from .local_flag_steps import DEFINITIONS, install
from .local_flag_steps import STEPS as LOCAL_STEPS
from .steps import Registry, expect

STEPS = Registry()
STEPS.definitions.extend(LOCAL_STEPS.definitions)
STEPS.requirements.update(LOCAL_STEPS.requirements)


@STEPS.step(r'no local feature flag definitions are loaded for "([^"]*)" and "([^"]*)"', fixtures=DEFINITIONS)
async def absent(ctx, step, first, second):
    for key in (first, second):
        state(ctx).definitions.pop(key, None)
    await install(ctx)


@STEPS.step(r'remote feature flag evaluation responses for "([^"]*)" and "([^"]*)" are delayed')
async def delayed(ctx, step, first, second):
    require(first != second, "invalid_step_data", "Expected disjoint flag scopes")
    ctx.probe_keys = [first, second]
    for index, key in enumerate(ctx.probe_keys):
        ctx.server.gates.arm(
            f"probe-{index}", distinct_id="user-123", flag_keys=[key], timeout_ms=min(ctx.timeout_ms + 1000, 300000)
        )


async def finish(ctx, invokes):
    receipts = await invoke_concurrent(ctx.fixture, ctx.profile, invokes, ctx.timeout_ms)
    failures = [r["completion"]["failure"] for r in receipts if r["completion"]["kind"] == "harness"]
    if failures:
        failure = next((f for f in failures if f["kind"] == "unsupported_binding"), failures[0])
        raise BoundaryError(failure["code"], failure["message"], failure["kind"])
    for receipt in receipts:
        expect(
            ctx.client.contracts.target_result_matches(receipt["route"], receipt["completion"]["outcome"]),
            "incorrect_result",
            "Concurrent native result differs from the catalog target",
        )
    return receipts


@STEPS.step(
    r'evaluate flags is started concurrently for disjoint scopes containing "([^"]*)" and "([^"]*)"',
    routes=("/evaluate_flags",),
    fixtures=(CAPABILITY,),
)
async def start(ctx, step, first, second):
    require(getattr(ctx, "probe_keys", None) == [first, second], "invalid_state", "Missing declared response gates")
    start_evaluations(ctx, step, [{"distinct_id": "user-123", "flag_keys": [key]} for key in (first, second)])


def start_evaluations(ctx, step, arguments):
    if CAPABILITY not in ctx.profile["fixture_capabilities"]:
        raise BoundaryError("missing_fixture", f"Required fixture capability: {CAPABILITY}", "blocked_fixture")
    invokes = []
    for args in arguments:
        ctx.call_index += 1
        call_id = f"{ctx.fixture.id}/call-{ctx.call_index}"
        invokes.append(
            {
                "call_id": call_id,
                "route": "/evaluate_flags",
                "receiver": deepcopy(ctx.fixture.receiver),
                "args": deepcopy(args),
            }
        )
        ctx.diagnostics["invocations"].append(
            {"call_id": call_id, "route": "/evaluate_flags", "step_index": ctx.step_index, "source": step.source}
        )
    task = asyncio.create_task(finish(ctx, invokes))
    ctx.pending_tasks.append(task)
    return task


async def wait_for_gates(ctx, gates):
    barrier = asyncio.create_task(ctx.server.gates.in_flight(gates, ctx.timeout_ms))
    try:
        done, _ = await asyncio.wait([barrier, ctx.pending_tasks[-1]], return_when=asyncio.FIRST_COMPLETED)
        if barrier not in done:
            await ctx.pending_tasks[-1]
            expect(False, "concurrent_requests", "Native calls completed before both probes were held")
        indexes = await barrier
    finally:
        if not barrier.done():
            barrier.cancel()
        await asyncio.gather(barrier, return_exceptions=True)
    return indexes


@STEPS.step("two remote feature flag evaluation requests should be in flight before either response is released")
async def overlapping(ctx, step):
    indexes = await wait_for_gates(ctx, ["probe-0", "probe-1"])
    traffic = ctx.server.requests()
    expect(
        len(set(indexes)) == 2 and all(traffic[i]["status"] is None for i in indexes),
        "concurrent_requests",
        "Both disjoint probes must be held simultaneously",
    )
    # The scenario ends at this observation. Release service fixtures, then await
    # the already-requested public operations; no extra SDK operations are called.
    for gate in ("probe-0", "probe-1"):
        ctx.server.gates.release(gate)
    for task in ctx.pending_tasks:
        await task
