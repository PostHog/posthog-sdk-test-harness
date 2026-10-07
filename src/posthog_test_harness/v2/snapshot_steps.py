"""Request-local public snapshot reads and strict caller/wire observations."""

from .ai_steps import json_arguments
from .contracts import BoundaryError, decode_json, json_equal
from .local_parity_steps import STEPS as PREVIOUS_STEPS
from .remote_flag_steps import named_events
from .steps import Registry, expect

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


def representation(ctx, prefix, choices):
    selected = [choice for choice in choices if prefix + choice in ctx.profile["sdk_capabilities"]]
    if len(selected) != 1:
        raise BoundaryError(
            "snapshot_representation", "Declare exactly one " + prefix + " representation", "unsupported_binding"
        )
    return selected[0]


@STEPS.step("remote snapshot fixtures are:", "docString")
async def fixtures(ctx, step):
    args = json_arguments(step)
    for identity, flags in args.get("identities", {}).items():
        ctx.server.set_flags(
            identity,
            {key: flag["value"] for key, flag in flags.items()},
            {key: flag["payload"] for key, flag in flags.items() if "payload" in flag},
        )
    if "status" in args:
        ctx.server.fail_next_flags(args["status"])


@STEPS.step("the snapshot SDK is initialized with JSON configuration:", "docString", routes=("/setup",))
async def setup(ctx, step):
    await ctx.call(
        "/setup",
        {
            "project_token": "test-token",
            "config": {"host": ctx.server.url, "flush_at": 100, "flush_interval_ms": 0, **json_arguments(step)},
        },
    )


@STEPS.step("evaluate flags and read is called with JSON arguments:", "docString", routes=("/evaluate_flags/read",))
async def evaluate(ctx, step):
    args = json_arguments(step)
    outcome = await ctx.call("/evaluate_flags/read", args, check_result=False)
    expect(outcome["kind"] != "thrown", "snapshot_thrown", "Public snapshot evaluation or read threw")
    ctx.snapshot_read = (args["reads"], outcome)


def missing(ctx, outcome, payload=False):
    if payload and "flag_snapshot_payload_missing_empty_string" in ctx.profile["sdk_capabilities"]:
        return json_equal(outcome, {"kind": "value", "value": ""})
    shape = representation(ctx, "flag_snapshot_missing_", ["undefined", "null"])
    return json_equal(outcome, {"kind": "undefined"} if shape == "undefined" else {"kind": "value", "value": None})


def semantic_read(ctx, read, outcome, expected):
    expect(
        isinstance(outcome, dict)
        and (
            (outcome.get("kind") == "value" and set(outcome) == {"kind", "value"})
            or (outcome.get("kind") == "undefined" and set(outcome) == {"kind"})
        ),
        "snapshot_result",
        "Invalid native read outcome",
    )
    method = read["method"]
    payload_shape = None
    if method == "get_flag_payload":
        payload_shape = representation(ctx, "flag_snapshot_payload_", ["decoded", "serialized"])
        if (
            "flag_snapshot_payload_missing_empty_string" in ctx.profile["sdk_capabilities"]
            and payload_shape != "serialized"
        ):
            raise BoundaryError(
                "snapshot_representation",
                "Empty-string missing payload requires serialized representation",
                "unsupported_binding",
            )
    if method in ("only", "only_accessed"):
        compare_results(ctx, read["reads"], outcome, expected)
        return
    if expected.get("missing") is True:
        expect(missing(ctx, outcome, method == "get_flag_payload"), "snapshot_missing", "Native missing result differs")
        return
    expect(outcome.get("kind") == "value", "snapshot_result", "Expected native data result")
    value = outcome.get("value")
    if method == "keys":
        keys = expected["keys"]
        expect(
            isinstance(value, list)
            and all(isinstance(key, str) for key in value)
            and len(value) == len(set(value))
            and set(value) == set(keys),
            "snapshot_keys",
            "Public key set differs",
        )
    elif method == "get_flag_payload":
        if payload_shape == "serialized":
            expect(isinstance(value, str), "snapshot_payload", "Expected documented serialized JSON payload")
            try:
                value = decode_json(value)
            except BoundaryError:
                expect(False, "snapshot_payload", "Public payload is not valid serialized JSON")
        expect(json_equal(value, expected["payload"]), "snapshot_payload", "Public payload semantics differ")
    elif method == "get_flag":
        shape = representation(ctx, "flag_snapshot_value_", ["scalar", "rich"])
        if shape == "rich":
            expect(
                isinstance(value, dict)
                and value.get("key") == read["key"]
                and type(value.get("enabled")) is bool
                and "variant" in value
                and (value["variant"] is None or isinstance(value["variant"], str)),
                "snapshot_value_shape",
                "Rich public projection differs",
            )
            value = False if not value["enabled"] else value["variant"] if value["variant"] is not None else True
        expect(json_equal(value, expected["value"]), "snapshot_value", "Public flag value differs")
    else:
        expect(
            type(value) is bool and json_equal(value, expected["value"]),
            "snapshot_enablement",
            "Public enablement differs",
        )


def compare_results(ctx, reads, outcome, expected):
    expect(
        outcome.get("kind") == "value"
        and isinstance(outcome.get("value"), dict)
        and set(outcome["value"]) == {"results"},
        "snapshot_result",
        "Expected ordered native read outcomes",
    )
    actual = outcome["value"]["results"]
    wanted = expected["results"]
    expect(
        isinstance(actual, list) and len(actual) == len(reads) == len(wanted),
        "snapshot_result_count",
        "Read outcome count differs",
    )
    for read, result, target in zip(reads, actual, wanted):
        expect(isinstance(result, dict), "snapshot_result", "Invalid read outcome")
        semantic_read(ctx, read, result, target)


@STEPS.step("the public snapshot outcomes should have these semantics:", "docString")
async def outcomes(ctx, step):
    reads, outcome = ctx.snapshot_read
    compare_results(ctx, reads, outcome, json_arguments(step))


def subset(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and subset(actual[key], value) for key, value in expected.items()
        )
    return json_equal(actual, expected)


@STEPS.step("the flushed snapshot traffic should be:", "docString")
async def traffic(ctx, step):
    expected = json_arguments(step)
    observed = ctx.server.flag_requests()
    all_remote = [r for r in ctx.server.requests() if r["path"].rstrip("/") in ("/flags", "/decide")]
    expect(
        len(observed) == len(all_remote) == len(expected["requests"]),
        "snapshot_request_count",
        "Remote evaluation count differs",
    )
    for request, target in zip(observed, expected["requests"]):
        body = decode_json(request.body_decompressed or "")
        expect(
            request.response_status == target.get("status", 200),
            "snapshot_response_status",
            "Mock evaluation response status differs",
        )
        fields = {key: value for key, value in target.items() if key != "status"}
        if isinstance(body, dict) and "token" not in body and "api_key" in body:
            body = {**body, "token": body["api_key"]}
        scope = fields.pop("flag_keys_to_evaluate", None)
        expect(subset(body, fields), "snapshot_request_context", "Evaluation request context differs")
        if scope is not None:
            actual = body.get("flag_keys_to_evaluate")
            expect(
                isinstance(actual, list)
                and all(isinstance(key, str) for key in actual)
                and len(actual) == len(scope)
                and set(actual) == set(scope),
                "snapshot_request_scope",
                "Request-time key scope differs",
            )
    events = named_events(ctx, "$feature_flag_called")
    targets = expected["exposures"]
    expect(len(events) == len(targets), "snapshot_exposure_count", "Delivered exposure count differs")
    remaining = list(events)
    for target in targets:
        matches = [
            event
            for event in remaining
            if event.get("distinct_id") == target["distinct_id"]
            and event.get("properties", {}).get("$feature_flag") == target["key"]
        ]
        expect(len(matches) == 1, "snapshot_exposure_context", "Exposure identity/key or dedupe differs")
        event = matches[0]
        remaining.remove(event)
        props = event["properties"]
        if target.get("missing") is True:
            errors = props.get("$feature_flag_error")
            expect(
                isinstance(errors, str) and "flag_missing" in errors.split(","),
                "snapshot_exposure_missing",
                "Missing-key exposure metadata differs",
            )
            shape = representation(ctx, "flag_snapshot_exposure_missing_", ["absent", "null", "false"])
            expect(
                (
                    ("$feature_flag_response" not in props)
                    if shape == "absent"
                    else "$feature_flag_response" in props
                    and json_equal(props["$feature_flag_response"], None if shape == "null" else False)
                ),
                "snapshot_exposure_response",
                "Missing exposure sentinel differs",
            )
        else:
            expect(
                "$feature_flag_response" in props and json_equal(props["$feature_flag_response"], target["value"]),
                "snapshot_exposure_response",
                "Canonical exposure response differs",
            )
        if "groups" in target:
            expect(
                json_equal(props.get("$groups"), target["groups"]), "snapshot_exposure_groups", "Exposure groups differ"
            )
