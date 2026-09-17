"""Approved capture-amendment-v1 inputs with unchanged YAML wire observation scopes."""

from .ai_steps import first_events, requests
from .analytics_wire_steps import first_request
from .contracts import decode_json
from .legacy_capture_steps import STEPS as PREVIOUS_STEPS
from .steps import Registry, expect

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


@STEPS.step(
    r'the SDK is initialized with token "([^"]*)" and compression "(gzip|deflate|br|zstd)"'
    r"(?: and flush threshold ([0-9]+))?",
    routes=("/setup",),
)
async def setup_compression(ctx, step, token, encoding, threshold):
    config = {"host": ctx.server.url, "compression": encoding}
    if threshold is not None:
        config["flush_at"] = int(threshold)
    await ctx.call("/setup", {"project_token": token, "config": config})


@STEPS.step(
    r'the SDK is initialized with token "([^"]*)", flush threshold ([0-9]+), and GeoIP disabled',
    routes=("/setup",),
)
async def setup_geoip(ctx, step, token, threshold):
    await ctx.call(
        "/setup",
        {"project_token": token, "config": {"host": ctx.server.url, "flush_at": int(threshold), "disable_geoip": True}},
    )


@STEPS.step(r'a received request header "([^"]*)" should equal "([^"]*)"')
async def any_header(ctx, step, name, expected):
    expect(
        any(
            {k.lower(): v for k, v in request.headers.items()}.get(name.lower()) == expected
            for request in requests(ctx)
        ),
        "request_header",
        f"No received request has the expected {name}",
    )


@STEPS.step("the first encoded request should decompress to parseable events")
async def decompressed_events(ctx, step):
    request = first_request(ctx)
    expect(bool(request.headers.get("content-encoding")), "compression_encoding", "First request lacks encoding")
    expect(bool(request.body_decompressed), "compression_body", "First encoded body could not be decompressed")
    expect(bool(request.parsed_events), "compression_events", "Decompressed body has no parseable events")


@STEPS.step(r'the first received event option "([^"]*)" should equal JSON (.+)')
async def option_json(ctx, step, name, encoded):
    options = first_events(ctx)[0].get("options") or {}
    # Preserve pinned value equality (including boolean/number equality), not a schema assertion.
    expect(
        isinstance(options, dict) and options.get(name) == decode_json(encoded),
        "event_option",
        f"First event option differs: {name}",
    )
