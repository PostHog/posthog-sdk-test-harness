"""Analytics-v1 retry observations, including explicitly mock-authored responses.

These preserve the pinned source's selected-request and missing-field semantics;
they are not comprehensive delivery or per-event acknowledgement proofs.
"""

from ..types import MockResponse
from .ai_steps import requests
from .analytics_wire_steps import STEPS as PREVIOUS_STEPS
from .analytics_wire_steps import first_request
from .contracts import decode_json
from .steps import Registry, expect, table

STEPS = Registry()
STEPS.definitions.extend(PREVIOUS_STEPS.definitions)
STEPS.requirements.update(PREVIOUS_STEPS.requirements)


@STEPS.step("the mock serves these ordered analytics responses:", "dataTable")
async def responses(ctx, step):
    rows = table(step, {"status": "json", "headers": "json", "body": "json", "event_results": "json"})
    ctx.server.state.set_response_queue(
        [
            MockResponse(
                status_code=r["status"], headers=r["headers"], body=r["body"], v1_event_results=r["event_results"]
            )
            for r in rows
        ]
    )


@STEPS.step(r'the SDK is initialized with token "([^"]*)" and maximum retries ([0-9]+)', routes=("/setup",))
async def setup_retries(ctx, step, token, count):
    await ctx.call("/setup", {"project_token": token, "config": {"host": ctx.server.url, "max_retries": int(count)}})


def retry_requests(ctx):
    observed = requests(ctx)
    expect(len(observed) >= 2, "retry_requests", "Need at least two recorded requests")
    return observed


@STEPS.step(r'the present event "(uuid|timestamp)" lists in requests zero and one should be identical')
async def preserved_events(ctx, step, field):
    observed = retry_requests(ctx)
    values = [[event[field] for event in (request.parsed_events or []) if field in event] for request in observed[:2]]
    expect(values[0] == values[1], "retry_event_" + field, f"First two requests differ in present {field} lists")


@STEPS.step("every received batch should have no duplicate nonempty UUIDs")
async def no_duplicates(ctx, step):
    observed = requests(ctx)
    expect(bool(observed), "missing_request", "No requests recorded")
    for request in observed:
        values = [event["uuid"] for event in (request.parsed_events or []) if event.get("uuid")]
        expect(len(values) == len(set(values)), "batch_duplicate_uuid", "Duplicate nonempty UUID in a batch")


@STEPS.step("all recorded request attempts should be consecutive integers starting at one")
async def attempts(ctx, step):
    for index, request in enumerate(retry_requests(ctx), 1):
        value = request.headers.get("posthog-attempt")
        try:
            actual = int(value) if value is not None else None
        except ValueError:
            actual = None
        expect(actual == index, "retry_attempt", f"Request {index - 1} attempt is not {index}")


@STEPS.step("all recorded request IDs should equal the nonempty first request ID")
async def preserved_request_id(ctx, step):
    observed = retry_requests(ctx)
    identity = observed[0].headers.get("posthog-request-id")
    expect(bool(identity), "retry_request_id", "First request ID is missing")
    expect(
        all(r.headers.get("posthog-request-id") == identity for r in observed[1:]),
        "retry_request_id",
        "Request ID changed on retry",
    )


@STEPS.step(
    r'the first two request headers "(posthog-request-id|posthog-request-timestamp)" should be nonempty and different'
)
async def different_headers(ctx, step, name):
    observed = retry_requests(ctx)
    first, second = [r.headers.get(name) for r in observed[:2]]
    expect(
        bool(first) and bool(second) and first != second,
        "different_request_header",
        f"First two {name} values must differ",
    )


@STEPS.step("at least one recorded response should have status 200")
async def any_success(ctx, step):
    expect(any(r.response_status == 200 for r in requests(ctx)), "retry_success", "No recorded response has status 200")


@STEPS.step(r"the first inter-request delay should be at least ([0-9]+) milliseconds")
async def first_delay(ctx, step, minimum):
    observed = retry_requests(ctx)
    delay = observed[1].timestamp_ms - observed[0].timestamp_ms
    expect(delay >= int(minimum), "retry_delay", f"First inter-request delay {delay}ms is below {minimum}ms")


@STEPS.step("exactly one recorded request excluding paths containing /flags should have been received")
async def no_retry(ctx, step):
    observed = [r for r in requests(ctx) if "/flags" not in r.path]
    expect(len(observed) == 1, "terminal_request_count", "Expected exactly one request excluding /flags paths")


def response_body(ctx):
    body = first_request(ctx).response_body
    expect(bool(body), "mock_response_body", "First mock-authored response has no body")
    try:
        return decode_json(body)
    except (ValueError, TypeError):
        expect(False, "mock_response_body", "First mock-authored response is not JSON")


def response_results(ctx):
    body = response_body(ctx)
    results = body.get("results") if isinstance(body, dict) else None
    expect(isinstance(results, dict), "mock_results_map", "First mock-authored response results is not an object")
    return results


@STEPS.step(r"the first mock-authored response should have status ([0-9]+)")
async def response_status(ctx, step, status):
    expect(
        first_request(ctx).response_status == int(status), "mock_response_status", "First mock response status differs"
    )


@STEPS.step("the first mock-authored response should contain a results object")
async def results_map(ctx, step):
    response_results(ctx)


@STEPS.step(r'every first mock-authored response result should equal "([^"]*)"')
async def results_equal(ctx, step, expected):
    for entry in response_results(ctx).values():
        expect(
            (entry.get("result") if isinstance(entry, dict) else entry) == expected,
            "mock_result_value",
            "First mock response result differs",
        )


@STEPS.step(r"the first mock-authored response should contain exactly ([0-9]+) results")
async def results_count(ctx, step, count):
    body = response_body(ctx)
    expect(
        len(body.get("results", {})) == int(count), "mock_results_count", "First mock response results count differs"
    )


@STEPS.step(r"the first mock-authored response Retry-After should be (present|absent)")
async def response_retry_after(ctx, step, presence):
    headers = first_request(ctx).response_headers
    value = headers.get("Retry-After") or headers.get("retry-after")
    expect(
        (value is not None) == (presence == "present"),
        "mock_retry_after",
        f"First mock Retry-After should be {presence}",
    )


@STEPS.step("the first mock-authored response should echo the nonempty sent request ID")
async def response_echo(ctx, step):
    request = first_request(ctx)
    sent = request.headers.get("posthog-request-id")
    headers = request.response_headers
    echoed = headers.get("PostHog-Request-Id") or headers.get("posthog-request-id")
    expect(
        bool(sent) and echoed is not None and echoed == sent,
        "mock_request_id_echo",
        "Mock response does not echo sent request ID",
    )
