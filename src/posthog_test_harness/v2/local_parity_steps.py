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


def no_remote(ctx, since=0):
    # Observe the whole fixture window, including initialization and reloads, or only the
    # traffic after the first ``since`` requests.
    expect(
        not any(r["path"].rstrip("/") in ("/flags", "/decide") for r in ctx.server.requests()[since:]),
        "local_remote_escape",
        "Local evaluation made a remote evaluation request",
    )


@STEPS.step("no remote flag evaluation path should have been requested")
async def no_remote_paths(ctx, step):
    no_remote(ctx)


@STEPS.step("the definitions service serves this typed document:", "docString")
async def definitions(ctx, step):
    """Serve ``definitions`` to every definitions request that a v=2 document does not answer.

    Optional ``api_key`` and ``personal_api_key`` set the credentials. Optional ``etag`` is
    sent with the document, and a request whose If-None-Match matches it gets 304.
    """
    args = json_arguments(step)
    ctx.server.state.set_definitions(**args)
    ctx.local_credentials = (args.get("api_key", "phc_test_key"), args.get("personal_api_key", "phx_test_key"))


@STEPS.step("the definitions service serves this typed document for v=2 requests:", "docString")
async def v2_definitions(ctx, step):
    """Serve ``definitions`` to a definitions request with exactly one ``v`` query parameter, equal to 2.

    Every other request gets the document of "the definitions service serves this typed
    document:", and so does a v=2 request when no v=2 document is set, as the flags service
    does for a project without config version 2 flags. The JSON fields are those of that step;
    given credentials replace the shared ones, and ``etag`` belongs to this document only.
    """
    args = json_arguments(step)
    ctx.server.state.set_v2_definitions(**args)
    token, secret = getattr(ctx, "local_credentials", ("phc_test_key", "phx_test_key"))
    ctx.local_credentials = (args.get("api_key", token), args.get("personal_api_key", secret))


async def initialize(ctx, step, *, allow_remote):
    ctx.local_evaluation = not allow_remote
    args = json_arguments(step)
    ctx.local_initial_definitions_before = len(ctx.server.state.get_definition_requests())
    await ctx.call("/setup", {**args, "config": {**args["config"], "host": ctx.server.url}})
    if not allow_remote:
        no_remote(ctx)


@STEPS.step("the SDK is initialized for native local evaluation with JSON arguments:", "docString", routes=("/setup",))
async def setup(ctx, step):
    await initialize(ctx, step, allow_remote=False)


@STEPS.step(
    "the SDK is initialized for native local evaluation with remote fallback and JSON arguments:",
    "docString",
    routes=("/setup",),
)
async def setup_with_fallback(ctx, step):
    """The native local evaluation setup without its ban on /flags and /decide requests.

    The scenario asserts remote traffic itself. Local getter steps still forbid it.
    """
    await initialize(ctx, step, allow_remote=True)


@STEPS.step(
    "local definitions are publicly reloaded within 5000 milliseconds",
    routes=("/reload_feature_flags",),
)
async def reload(ctx, step):
    """A fresh reload with no remote evaluation request during it."""
    fresh, _, traffic_before = await public_reload(ctx)
    expect(bool(fresh), "local_reload_fresh_fetch", "Reload did not fetch new authenticated HTTP 200 definitions")
    no_remote(ctx, since=traffic_before)


@STEPS.step(
    "local definitions are publicly reloaded within 5000 milliseconds and the definitions service answers 304",
    routes=("/reload_feature_flags",),
)
async def reload_not_modified(ctx, step):
    """A conditional reload: every definitions request it makes is authenticated and gets 304.

    Like the fresh reload step, it forbids remote evaluation requests only during the reload.
    """
    _, received, traffic_before = await public_reload(ctx)
    expect(
        bool(received) and all(r["authenticated"] and r["status"] == 304 for r in received),
        "local_reload_not_modified",
        "Reload did not make only authenticated definitions requests answered with 304",
        details={"requests": received},
    )
    no_remote(ctx, since=traffic_before)


@STEPS.step(r'every flag definitions request query parameter "([^"]*)" should equal "([^"]*)"')
async def definitions_query(ctx, step, name, expected):
    """At least one definitions request, and each one carries exactly one ``name`` parameter, equal to ``expected``."""
    received = ctx.server.state.get_definition_requests()
    details = {
        "parameter": name,
        "expected": expected,
        "requests": [{"path": r.path, "values": (r.query_param_lists or {}).get(name, [])} for r in received],
    }
    expect(bool(received), "definitions_request_missing", "No flag definitions request was received", details=details)
    expect(
        all(request["values"] == [expected] for request in details["requests"]),
        "definitions_request_query",
        "A flag definitions request did not carry the query parameter exactly once with the expected value",
        details=details,
    )


@STEPS.step(r'the definition request should include If-None-Match "([^"]*)"')
async def definitions_if_none_match(ctx, step, expected):
    """The latest definitions request sent this If-None-Match value."""
    received = ctx.server.state.get_definition_requests()
    expect(bool(received), "definitions_request_missing", "No flag definitions request was received")
    actual = received[-1].headers.get("if-none-match")
    expect(
        actual == expected,
        "definitions_request_if_none_match",
        "The latest flag definitions request did not send the expected If-None-Match",
        details={"path": received[-1].path, "expected": expected, "actual": actual},
    )


@STEPS.step("every flag definitions request after the first should have been answered with 304")
async def definitions_not_modified(ctx, step):
    """At least one definitions request, and every later one got 304: the SDK kept what it loaded."""
    received = ctx.server.state.get_definition_requests()
    details = {"requests": [{"path": r.path, "status": r.response_status} for r in received]}
    expect(bool(received), "definitions_request_missing", "No flag definitions request was received", details=details)
    expect(
        all(r.response_status == 304 for r in received[1:]),
        "definitions_request_not_modified",
        "A flag definitions request after the first was not answered with 304",
        details=details,
    )


async def public_reload(ctx):
    """Reload through the public SDK method within five seconds.

    Returns the fresh and all new definitions requests, and the traffic count before the reload.
    """
    before = len(ctx.server.state.get_definition_requests())
    traffic_before = len(ctx.server.requests())
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
    return fresh, requests, traffic_before


def definition_requests(ctx, before):
    received = ctx.server.state.get_definition_requests()[before:]
    token, secret = ctx.local_credentials
    authenticated = [
        r
        for r in received
        if r.query_params.get("token") == token and r.headers.get("authorization", "").split() == ["Bearer", secret]
    ]
    fresh = [r for r in authenticated if r.response_status == 200]
    return fresh, [
        {
            "path": r.path,
            "status": r.response_status,
            "authenticated": r in authenticated,
            "query": r.query_param_lists,
            "if_none_match": r.headers.get("if-none-match"),
            "body": None if r.response_body is None else decode_json(r.response_body),
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
            "actual": ctx.local_flag_outcome,
        },
    )
