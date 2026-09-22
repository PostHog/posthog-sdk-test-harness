"""Four local YAML origins: authenticated HTTP reloads and native getter evidence."""

import asyncio
import time

from .ai_steps import json_arguments
from .contracts import BoundaryError, decode_json
from .remote_flag_steps import STEPS as PREVIOUS_STEPS
from .steps import Registry, expect

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


def no_remote(ctx):
    # Observe the whole fixture window, including initialization and reloads.
    expect(
        not any(r["path"].rstrip("/") in ("/flags", "/decide") for r in ctx.server.requests()),
        "local_remote_escape",
        "Local evaluation made a remote evaluation request",
    )


@STEPS.step("no remote flag evaluation path should have been requested")
async def no_remote_paths(ctx, step):
    no_remote(ctx)


@STEPS.step("the definitions service serves this typed document:", "docString")
async def definitions(ctx, step):
    args = json_arguments(step)
    ctx.server.state.set_definitions(**args)
    ctx.local_credentials = (args.get("api_key", "phc_test_key"), args.get("personal_api_key", "phx_test_key"))


@STEPS.step("the SDK is initialized for native local evaluation with JSON arguments:", "docString", routes=("/setup",))
async def setup(ctx, step):
    ctx.local_evaluation = True
    args = json_arguments(step)
    ctx.local_initial_definitions_before = len(ctx.server.state.get_definition_requests())
    await ctx.call("/setup", {**args, "config": {**args["config"], "host": ctx.server.url}})
    no_remote(ctx)


@STEPS.step(
    "local definitions are publicly reloaded within 5000 milliseconds",
    routes=("/reload_feature_flags",),
)
async def reload(ctx, step):
    before = len(ctx.server.state.get_definition_requests())
    started = time.monotonic()
    original_timeout = ctx.timeout_ms
    deadline = started + 5
    try:
        async with asyncio.timeout_at(deadline):
            ctx.timeout_ms = min(original_timeout, 5000)
            await ctx.call("/reload_feature_flags", {})
            reload_call = ctx.last_receipt["call_id"]

    except TimeoutError:
        raise BoundaryError(
            "local_reload_deadline", "Public definitions reload exceeded 5000ms", "failed_assertion"
        ) from None
    finally:
        ctx.timeout_ms = original_timeout
    fresh, requests = definition_requests(ctx, before)
    ctx.diagnostics.setdefault("definition_reloads", []).append(
        {
            "call_id": reload_call,
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "deadline_ms": 5000,
            "requests": requests,
        }
    )
    expect(bool(fresh), "local_reload_fresh_fetch", "Reload did not fetch new authenticated HTTP 200 definitions")
    no_remote(ctx)


def definition_requests(ctx, before):
    received = ctx.server.state.get_definition_requests()[before:]
    token, secret = ctx.local_credentials
    fresh = [
        r
        for r in received
        if r.response_status == 200
        and r.query_params.get("token") == token
        and r.headers.get("authorization", "").split() == ["Bearer", secret]
    ]
    return fresh, [
        {
            "path": r.path,
            "status": r.response_status,
            "authenticated": r in fresh,
            "body": decode_json(r.response_body),
        }
        for r in received
    ]


@STEPS.step(
    "the local flag getter is called with JSON arguments:",
    "docString",
    routes=("/get_feature_flag",),
)
async def getter(ctx, step):
    args = json_arguments(step)
    ctx.local_flag_arguments = args
    outcome = await ctx.call("/get_feature_flag", args, check_result=False)
    no_remote(ctx)
    if ctx.local_initial_definitions_before is not None:
        fresh, requests = definition_requests(ctx, ctx.local_initial_definitions_before)
        ctx.diagnostics["initial_definitions"] = {"call_id": ctx.last_receipt["call_id"], "requests": requests}
        expect(
            bool(fresh), "local_initial_fetch", "Initial evaluation did not fetch authenticated HTTP 200 definitions"
        )
        ctx.local_initial_definitions_before = None
    expect(
        outcome["kind"] == "value" and type(outcome["value"]) in (bool, str),
        "local_inconclusive",
        "Local getter must return a conclusive boolean or string",
        details={
            "operation": "/get_feature_flag",
            "arguments": args,
            "expected": "conclusive boolean or string value",
            "actual": outcome,
        },
    )
    ctx.local_flag_outcome = outcome


@STEPS.step(r"the local flag getter should return JSON (.+)")
async def result(ctx, step, encoded):
    # Keep the source helper's equality after the conclusive-kind checks.
    expected = decode_json(encoded)
    actual = ctx.local_flag_outcome["value"]
    expect(
        actual == expected,
        "local_flag_value",
        "Local getter value differs",
        details={
            "operation": "/get_feature_flag",
            "arguments": ctx.local_flag_arguments,
            "expected": expected,
            "actual": actual,
        },
    )
