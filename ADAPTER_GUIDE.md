# SDK Adapter Implementation Guide

This guide explains how to create an SDK adapter for the PostHog test harness.

## What is an SDK Adapter?

An SDK adapter is a thin HTTP wrapper around your SDK that:
1. Exposes a standard REST API for the test harness to control
2. Tracks internal SDK state for test assertions
3. Allows the test harness to exercise your SDK programmatically

## Quick Start

See `examples/minimal_adapter/` for a complete, working example.

## Required Endpoints

Your adapter must implement these endpoints. See [CONTRACT.yaml](CONTRACT.yaml) for the complete specification.

### `GET /health`

Health check endpoint. The `capabilities` array controls which test suites are run against your adapter.

**Response:**
```json
{
  "sdk_name": "posthog-python",
  "sdk_version": "3.0.0",
  "adapter_version": "1.0.0",
  "capabilities": ["capture_v0", "capture_v1", "capture_ai_v0", "encoding_gzip"]
}
```

**Capabilities:**

| Capability | Description |
|------------|-------------|
| `capture_v0` | Original capture protocol (`POST /batch`) |
| `capture_v1` | Capture Analytics V1 protocol (`POST /i/v1/analytics/events`) |
| `capture_ai_v0` | Dedicated AI capture endpoint (`POST /i/v0/ai/batch/`) |
| `capture_ai_v1` | Capture V1 AI endpoint (`POST /i/v1/ai/events`) |
| `flags_response_v4` | Remote `/flags` requests send `?v=4` and read the v4 flag record. Without it, the harness requires `?v=2` |
| `feature_flag_payloads` | `POST /get_feature_flag_payload` reads a flag's payload with the SDK's legacy payload accessor. Only used with `flags_response_v4`, where the harness checks the payloads of v4 records |
| `feature_flags_local_evaluation_v1` | Opt-in local flag evaluation controls. See [Optional local feature flag evaluation](#optional-local-feature-flag-evaluation-protocol-v1) |
| `feature_flags_known_absent_v1` | `/get_feature_flag` passes `person_property_keys_known_absent` and `group_property_keys_known_absent` to the SDK. Runs only together with `feature_flags_local_evaluation_v1`. See [Known-absent property keys](#optional-known-absent-property-keys-protocol-v1) |
| `feature_flags_unresolved_reasons_v1` | When an `only_evaluate_locally` `/get_feature_flag` request has no value, the response includes `unresolved_reason`, read from the SDK's unresolved flags API. See [Unsupported property-filter types](#optional-unsupported-property-filter-types-protocol-v1) |
| `feature_flags_unsupported_filter_types_v1` | The SDK treats a property filter type other than `person`, `group`, `cohort` or `flag` (such as `person_metadata`) as inconclusive, and `/get_feature_flag` honors `allow_remote_fallback`. Runs only together with `feature_flags_local_evaluation_v1`. See [Unsupported property-filter types](#optional-unsupported-property-filter-types-protocol-v1) |
| `feature_flags_unusable_group_key_v1` | The SDK treats an omitted group or an empty string group key as no group, so a group flag is `false` locally with no `/flags` request. Runs only together with `feature_flags_local_evaluation_v1` and `feature_flags_known_absent_v1`. See [Unusable group keys](#unusable-group-keys) |
| `feature_flags_json_group_keys_v1` | `/get_feature_flag` passes JSON `null`, `true`, `[]` and `{}` group keys to the SDK unchanged, and the SDK treats them as no group. Runs only together with `feature_flags_unusable_group_key_v1` |
| `feature_flags_local_evaluation_v2` | The SDK requests the definitions feed with `v=2`, evaluates config version 2 flags locally, and `/get_feature_flag` honors `allow_remote_fallback`. Runs only together with `feature_flags_local_evaluation_v1`. See [Local evaluation of config version 2 flags](#optional-local-evaluation-of-config-version-2-flags) |
| `event_options` | The SDK sends caller event options unchanged, and a legacy `$` property fills only an unset (missing or `null`) option |
| `event_uuid_override` | The adapter passes a caller-supplied `uuid` on `/capture` and `/capture_ai` to the SDK, which sends it in canonical lowercase form |
| `bounded_retries` | The SDK stops retrying after `max_retries` retries (`max_retries` + 1 attempts) |
| `unbounded_retries` | Capture retains/retries transient failures beyond `max_retries` until recovery. Replaces the legacy bounded-attempt assertion with a recovery/identity-preservation test that polls for delivery for up to 60 seconds; declaring together with `bounded_retries` is rejected as an invalid profile before selection/acceptance |
| `synchronous_capture` | Each capture delivers immediately without queue batching. Replaces the legacy multi-event batching assertion with delivery checks after every capture and no-extra-send-on-flush coverage |
| `disable_geoip` | The adapter maps `disable_geoip` on `/init` to the SDK's GeoIP opt-out |
| `historical_migration` | The adapter maps `historical_migration` on `/init` to the SDK's historical migration setting |
| `reported_drops` | `/state` returns `reported_drops`: every event the SDK reported as dropped through its error surface (a callback, error event or error list), whether capture dropped it or the SDK did |
| `log_capture` | `/state` returns `log_lines`: every line the SDK logged since `/init` |
| `generated_payloads` | `/capture` and `/capture_ai` honor `pad_properties_to_bytes` and `marker` |
| `property_layers` | `/init` honors `super_properties` and `before_send` |
| `fail_open` | `/init` succeeds with an empty or personal (`phx_`) API key and passes it to the SDK |
| `encoding_gzip` | Gzip compression support |
| `encoding_zstd` | Zstd compression support |
| `encoding_br` | Brotli compression support |
| `encoding_deflate` | Deflate compression support |

For legacy capture compatibility, adapters that declare neither `unbounded_retries` nor `synchronous_capture` retain the original bounded-retry and queued-batching tests. Declaring `bounded_retries` is not required retroactively. Declare profile semantics truthfully, not to suppress a failed assertion; each replacement keeps a positive test in the inventory. These capabilities describe capture delivery, not flag-request retries.

The harness skips any suite or test whose `requires` field isn't satisfied by the adapter's capabilities. If `capabilities` is omitted, only tests with no `requires` field will run. A test can also list `excludes`: the harness skips it when the adapter declares any of those capabilities. This lets a capability replace a default assertion, such as `flags_response_v4` replacing the `?v=2` check on `/flags` with a `?v=4` check.

## Choosing `sdk-type`

The harness `sdk-type` option selects tests based on the SDK's capture wire format. It is **not** a product/platform classification such as frontend vs backend, mobile vs server, or browser vs native.

Choose the mode by inspecting what the SDK sends to the mock PostHog server:

| `sdk-type` | Use when the SDK sends | Typical assertions |
|------------|------------------------|--------------------|
| `client` | Client-style capture requests to `/e/`, where event data such as `token` and `distinct_id` lives in the event/properties payload | Client event shape, token/distinct ID in event properties |
| `server` | Server-style capture requests to `/batch`, with a body shaped like `{ "api_key": "...", "batch": [...] }` | Server batch envelope, root event fields, `Content-Encoding` compression |

Examples:

- Browser SDKs that post to `/e/` usually use `client`.
- Backend SDKs that post `{ "api_key": "...", "batch": [...] }` to `/batch` use `server`.
- Mobile/native SDKs can still use `server` if their wire format is `/batch` with the server batch envelope.

### `POST /init`

Initialize the SDK with configuration.

**Request:**
```json
{
  "api_key": "phc_test_key",
  "host": "http://mock-server:8081",
  "flush_at": 100,
  "flush_interval_ms": 500,
  "max_retries": 3,
  "enable_compression": false
}
```

**Response:**
```json
{
  "success": true
}
```

**Notes:**
- All params except `api_key` and `host` are optional
- Use defaults from your SDK if params aren't provided
- Don't send events with `null` values - omit them instead
- With `property_layers`: register `super_properties` as the SDK's global (super) properties, and when `before_send` is present install a hook that sets each key of `before_send.set_properties` on the event's properties and returns the event
- With `fail_open`: pass `api_key` to the SDK as given, including `""` and `phx_...`, and return success

### `POST /capture`

Capture a single event.

**Request:**
```json
{
  "distinct_id": "user123",
  "event": "test_event",
  "properties": {"key": "value"},
  "timestamp": "2024-01-01T12:00:00Z"
}
```

**Response:**
```json
{
  "success": true,
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Notes:**
- `properties`, `timestamp`, `options`, and `uuid` are optional
- UTC is preferred for `timestamp`; convert a supplied non-UTC offset to the equivalent UTC instant before sending
- Pass `options`, when present, to the SDK's event options unchanged
- Pass `uuid`, when present, to the SDK as given (only tests that require `event_uuid_override` send it)
- Return the UUID generated by your SDK

### `POST /capture_ai`

Capture a single event on the dedicated AI capture endpoint. Only required if your adapter advertises `capture_ai_v0` or `capture_ai_v1`.

**Request:**
```json
{
  "distinct_id": "user123",
  "event": "$ai_generation",
  "properties": {"key": "value"},
  "timestamp": "2024-01-01T12:00:00Z",
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response:**
```json
{
  "success": true,
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Notes:**
- `properties`, `timestamp`, `options`, and `uuid` are optional
- UTC is preferred for `timestamp`; convert a supplied non-UTC offset to the equivalent UTC instant before sending
- When `uuid` is supplied, pass it to the SDK as given; the SDK sends it in canonical lowercase form instead of generating a new one
- With `generated_payloads`, `pad_properties_to_bytes` asks the adapter to add one string property `$test_padding` so that `properties`, written as compact JSON (no spaces) in UTF-8, is exactly that many bytes. When `marker` is set, the padding string starts with it. The harness cannot send multi-megabyte bodies through the adapter, so the adapter builds them
- Return the UUID actually sent on the wire, matching `/capture`'s response contract
- Route this call through your SDK's dedicated AI capture method so it reaches `POST /i/v0/ai/batch/` (`capture_ai_v0`) or `POST /i/v1/ai/events` (`capture_ai_v1`), never a normal capture endpoint
- Pass `options`, when present, to the SDK's event options unchanged

### `POST /flush`

Flush all pending events and wait for completion.

**Response:**
```json
{
  "success": true,
  "events_flushed": 5
}
```

**Notes:**
- This should block until all events are sent
- Return the number of events that were flushed

### `GET /state`

Get internal SDK state for test assertions.

**Response:**
```json
{
  "pending_events": 0,
  "total_events_captured": 10,
  "total_events_sent": 10,
  "total_retries": 2,
  "last_error": null,
  "requests_made": [
    {
      "timestamp_ms": 1704110400000,
      "status_code": 200,
      "retry_attempt": 0,
      "event_count": 5,
      "uuid_list": ["uuid1", "uuid2", ...]
    }
  ]
}
```

`reported_drops` (with `reported_drops`) lists `{"uuid": ..., "event": ..., "details": ...}` per dropped event; `details` is capture's per-event tag (such as `invalid_options` or `duplicate_event_uuid`) or the SDK's own reason. `log_lines` (with `log_capture`) lists the SDK's log lines as strings.

**Notes:**
- You'll need to intercept HTTP requests to track `requests_made`
- Each retry of the same batch should increment `retry_attempt`
- See `examples/minimal_adapter/adapter.py` for implementation details

### `POST /get_feature_flag`

Evaluate a feature flag.

**Request:**
```json
{
  "key": "my-flag",
  "distinct_id": "user123",
  "person_properties": {"email": "user@example.com"},
  "groups": {"company": "acme"},
  "group_properties": {"company": {"plan": "enterprise"}},
  "disable_geoip": true,
  "force_remote": true
}
```

**Response:**
```json
{
  "success": true,
  "value": true
}
```

**Notes:**
- All params except `key` and `distinct_id` are optional
- Omit optional params instead of sending `null`
- `distinct_id` is always a top-level adapter parameter
- If your SDK supports local evaluation or cached flag definitions, honor `force_remote=true` by bypassing local evaluation and issuing a fresh remote `/flags` request
- Remote `/flags` requests should retry transient HTTP failures such as 502 Bad Gateway and 504 Gateway Timeout; the feature flag contract asserts that a `502 -> 200` or `504 -> 200` response sequence produces two `/flags` requests and returns the successful retry value

### `POST /get_feature_flag_payload` (optional)

Only for adapters that declare `feature_flag_payloads`. Read a flag's payload with the SDK's payload accessor, evaluating remotely. The request has the same fields as `/get_feature_flag`.

**Response:**
```json
{
  "success": true,
  "value": {"columns": 2}
}
```

Return the payload as a JSON value: parse a JSON-encoded payload string before returning it, and return `null` when there is no payload. The harness compares payloads as parsed JSON values, so number formatting may differ.

### Remote `/flags?v=4` suite

`feature_flags_v4` reads mock v4 responses built from the [feature flag rules v2 contract](contracts/feature_flag_rules_v2/README.md): every reader case in `fixtures/wire/readers.json` and every successful case of `corpus/v2_value_evaluation.json` runs as its own test, named `<test>[<case id>]`. Each test makes one remote read with `force_remote: true`. The case's response is queued for up to 10 `/flags` requests, so an accessor may make more than one. The adapter must report `success: true`, and the SDK must read the case's response with `/flags?v=4`. Run it with `--suite feature_flags_v4`.

| Tests | Requires | Checks |
|-------|----------|--------|
| Legacy getters | `flags_response_v4` | `get_feature_flag` returns the legacy rendering of the record value: booleans and strings as is, `null` as `false`, numbers and objects as `true`; `false` for a failed or unreadable record; no value for a missing flag; `variant ?? enabled` for an older server's record. Types are strict: `true` is not `1` |
| Legacy payloads | `flags_response_v4`, `feature_flag_payloads` | The payload accessor returns a config version 2 number or object value as the payload and no payload for other values; a config version 1 or older server's record keeps its decoded `metadata.payload`; a missing flag, or a failed or unreadable record, has no payload. A failed record yields no payload even when it carries a `metadata.payload` (`readers.v1_failed` declares `legacy_payload: null`) |

**Harness rule for legacy getters.** The contract defines the legacy rendering of a successful value, and says a typed accessor returns the caller default on a reader error. It does not say what a legacy getter such as `get_feature_flag` returns on a reader error. This suite uses one rule:

- A missing flag (`FLAG_NOT_FOUND`) reads as no value: the adapter returns `null`.
- Any other reader error reads as `false`. This covers a failed record, an unreadable record and a known field with the wrong JSON type.

This matches how posthog-python's legacy getters behave today.

### `POST /reset`

Reset SDK state and clear all pending events.

**Response:**
```json
{
  "success": true
}
```

## Implementation Tips

### 1. State Tracking

You need to track:
- Events captured vs sent
- HTTP requests made (with status codes and UUIDs)
- Retry attempts
- Errors

Example (Python):
```python
class AdapterState:
    def __init__(self):
        self.lock = threading.Lock()
        self.total_events_captured = 0
        self.total_events_sent = 0
        self.requests_made = []
```

### 2. Request Interception

To track HTTP requests, intercept your SDK's HTTP client:

Example (Python, monkey-patching):
```python
original_post = sdk.http_client.post

def tracked_post(*args, **kwargs):
    response = original_post(*args, **kwargs)
    state.record_request(response.status_code, ...)
    return response

sdk.http_client.post = tracked_post
```

### 3. Handling Optional Parameters

Don't send `null` values in requests:

```python
# Bad
payload = {"flush_at": None}  # Prevents adapter defaults from working

# Good
payload = {}
if flush_at is not None:
    payload["flush_at"] = flush_at
```

### 4. Thread Safety

Use locks when tracking state from background threads:

```python
with state.lock:
    state.total_events_sent += 1
```

## Testing Your Adapter

### Locally with Docker

```bash
# 1. Create a Dockerfile for your adapter
cat > Dockerfile << 'EOF'
FROM python:3.12
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "adapter.py"]
EOF

# 2. Test it
git clone https://github.com/PostHog/posthog-sdk-test-harness.git
cd posthog-sdk-test-harness

# 3. Create docker-compose.override.yml
cp docker-compose.override.yml.example docker-compose.override.yml
# Edit to point to your adapter

# 4. Run tests
bin/test --build
```

### In CI/CD

Add to your `.github/workflows/test.yml`:

```yaml
- name: Run SDK Compliance Tests
  uses: PostHog/posthog-sdk-test-harness/.github/workflows/test-sdk-action.yml@v1
  with:
    adapter-dockerfile: "sdk_adapter/Dockerfile"
    adapter-context: "."
```

## Supporting Parallel Test Execution

The test harness supports running tests in parallel to speed up large test suites. This is entirely optional and requires adapter cooperation.

### How It Works

1. The harness sends `--concurrency N` to run N tests simultaneously
2. Each test gets a unique `test_id` (e.g., `t-550e8400-...`)
3. The harness appends `?test_id=<id>` to all adapter requests
4. The adapter manages a separate SDK instance per `test_id`
5. Each SDK instance sends an `X-Test-Id: <id>` header to the mock server
6. The mock server partitions recorded requests by this header

### Opting In

Return `supports_parallel: true` in your `/health` response:

```json
{
  "sdk_name": "posthog-python",
  "sdk_version": "3.0.0",
  "adapter_version": "1.0.0",
  "supports_parallel": true
}
```

### Implementation Pattern

Manage a dict of SDK instances keyed by `test_id`:

```python
from flask import request

instances = {}  # test_id -> SDKState

def get_instance():
    """Get the SDK instance for the current request."""
    test_id = request.args.get("test_id")
    if test_id is None:
        return global_instance
    if test_id not in instances:
        instances[test_id] = SDKState()
    return instances[test_id]

@app.route("/init", methods=["POST"])
def init():
    instance = get_instance()
    data = request.json
    # Configure this instance to send X-Test-Id header
    test_id = request.args.get("test_id")
    extra_headers = {"X-Test-Id": test_id} if test_id else {}
    instance.configure(data, extra_headers=extra_headers)
    return jsonify({"success": True})

@app.route("/reset", methods=["POST"])
def reset():
    test_id = request.args.get("test_id")
    if test_id and test_id in instances:
        instances[test_id].reset()
        del instances[test_id]
    elif test_id is None:
        global_instance.reset()
    return jsonify({"success": True})
```

### Key Requirements

- The `X-Test-Id` header **must** be sent with every request the SDK makes to the mock server
- Each `test_id` instance must be fully independent (separate event queue, retry state, etc.)
- When `test_id` is absent, behave exactly as before (single global instance)
- The harness sends a scoped `/reset?test_id=...` before and after each test (including failed tests); release that `test_id`'s SDK client, queues, and background workers on reset so memory doesn't grow with suite size
- This is fully backwards compatible — adapters that don't support parallel will never receive `test_id`

## Common Issues

### "Expected N requests, got 0"

Your adapter is sending events to the wrong host. Make sure you're using the `host` parameter from `/init`.

### "UUID not preserved on retry"

Make sure UUIDs are generated once and reused on retry attempts, not regenerated.

### "Retry delay too short"

Implement exponential backoff and respect `Retry-After` headers.

## Full Examples

See [examples/minimal_adapter/](examples/minimal_adapter/) for a sequential adapter with event queueing, retry logic, and state tracking.

See [examples/parallel_adapter/](examples/parallel_adapter/) for an adapter that supports parallel test execution with per-test-id instance isolation.

## Optional local feature flag evaluation (protocol v1)

Only adapters that explicitly advertise `feature_flags_local_evaluation_v1` in
`/health.capabilities` run the `feature_flags_local_evaluation` suite. **The `v1`
suffix versions the adapter protocol, not property matching:** opting in requires
both released legacy matching (missing/1) and explicit matching (2). It is never
inferred from `sdk_type`, and the harness does not advertise or enable it in any
existing adapter. Without opt-in, all new tests/actions are skipped and existing
adapter request JSON is unchanged.

To opt in, implement these optional extensions (see `CONTRACT.yaml`):

- `/init` accepts `personal_api_key` and configures the SDK's privileged
  definitions loader, using the supplied `host` and project `api_key`. Map this
  to the SDK's equivalent option (for example Python's `secret_key`). Omit this
  field entirely for ordinary remote/capture tests.
- `POST /reload_feature_flag_definitions` accepts `{"timeout_ms": 5000}`. Force
  a **fresh definitions GET**, then wait until that new snapshot is usable by
  local evaluation. Return `{"success": true, "ready": true}` only at that
  point. A queued reload or readiness of an older snapshot is insufficient.
  Return an error/false on failure; never wait indefinitely. The harness bounds
  the entire call (default 5 seconds, configurable 1–30000 ms) and checks that a
  successful definitions GET occurred during it. Use this as the initial
  readiness barrier as well as for version-only refreshes. Disable long-lived
  polling if needed in test mode, but do not replace the real loader/evaluator.
- `/get_feature_flag` accepts optional `only_evaluate_locally`. When true, use
  the SDK's **local-only** API, not just `force_remote=false` (which may fall
  back). Reject `only_evaluate_locally=true` plus `force_remote=true`. Return
  `{"success": true, "value": false, "locally_evaluated": true}` for a
  conclusive local false result, likewise for true/variants. Missing properties
  or other inconclusive outcomes must not be reported as local false. Legacy
  responses need not contain `locally_evaluated`.

The harness checks the local-result marker, conclusive value and **zero remote
`/flags` requests over the whole test**, including initialization
and reload. Adapters must call the real SDK and must not synthesize evaluations
from fixtures. Reset must stop polling/dispose the previous SDK instance.

### Definitions mock and fixture coverage

`configure_local_evaluation_definitions` replaces an isolated complete envelope:

```yaml
- action: configure_local_evaluation_definitions
  params:
    definitions:
      flags: []
      cohorts: {}
      group_type_mapping: {}
      property_matching_version: 2
```

The mock serves GET `/flags/definitions` and legacy
`/api/feature_flag/local_evaluation/`, including trailing-slash aliases. It
requires query `token=<project api_key>` and header
`Authorization: Bearer <personal_api_key>` (fixture defaults `phc_test_key` and
`phx_test_key`; both configurable in the action). The version is a **top-level**
definitions field, not an individual flag version or `/flags?v=2` format.
Omitting it on replacement removes the previous selector.

Definitions GETs use separate response state and `get_definition_requests()`
accounting: they do not consume `configure_mock_responses` queues and are not
capture events, generic recorded requests, or remote flag calls. Parallel
adapters must propagate `X-Test-Id` to definitions GETs too; the optional reload
adapter endpoint receives the usual `?test_id=...`. State and reset are isolated
by that same test ID.

The suite covers missing/1/2, six distinguishing rows and their `is_not`
complements, null/array/normalization cases, group and recursive cohort leaves,
and otherwise identical definitions switching 1→2→1→2→missing. Existing remote
fixtures are untouched. Shared-cache persistence and failure/304 policies remain
SDK-specific tests, not coverage claimed by this suite.

For a real patched Python SDK smoke run (temporary in-test HTTP adapter, no
production adapter modifications), install its runtime dependencies into the
harness environment and run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
POSTHOG_LOCAL_EVALUATION_SDK_PATH=/path/to/patched/posthog-python \
python -m pytest tests/test_local_evaluation_python_integration.py -q
```

Without that explicit path this integration test skips; ordinary harness unit
tests require no SDK installation.

`tests/test_known_absent_python_integration.py` runs the
`feature_flags_known_absent` suite the same way. Point
`POSTHOG_LOCAL_EVALUATION_SDK_PATH` at a posthog-python checkout that supports
known-absent property keys.

`tests/test_unsupported_filter_types_python_integration.py` runs the
`feature_flags_unsupported_filter_types` suite the same way. Point
`POSTHOG_LOCAL_EVALUATION_SDK_PATH` at a posthog-python checkout that treats
unsupported filter types as inconclusive, and set
`POSTHOG_UNSUPPORTED_FILTER_TYPES_SMOKE=1`.

## Optional known-absent property keys (protocol v1)

Only adapters that advertise **both** `feature_flags_local_evaluation_v1` and
`feature_flags_known_absent_v1` run the `feature_flags_known_absent` suite. It
covers the sdk-specs local-feature-flag-evaluator rules for property keys the
caller knows are absent, and the matching `/flags` request fields.

`/get_feature_flag` accepts three optional params:

- `person_property_keys_known_absent`: array of person property key strings
- `group_property_keys_known_absent`: object mapping a group type name to an
  array of group property key strings
- `allow_remote_fallback`: boolean, see below

Pass each one to the SDK's known-absence input only when the request has it,
unchanged: don't drop empty values, dedupe or truncate, so the SDK's own
validation is what runs. Don't send either field to the SDK when the request
omits it.

The suite uses two evaluation modes:

- `only_evaluate_locally: true` for definitive results. A listed key resolves
  `is_not_set`, `is_not`, `not_icontains`, `not_regex`, `not_starts_with` and
  `not_ends_with` to true, and `is_set`, `exact`, `icontains`, `gt`,
  `semver_neq` and `is_date_before` to false, with no `/flags` request.
- `allow_remote_fallback: true` for fallback cases. Call the SDK's **default**
  getter on the client that has local definitions: evaluate locally and fall
  back to `/flags` when local evaluation is inconclusive. Pass the known-absent
  params through. Return 400 when it is combined with `force_remote: true` or
  `only_evaluate_locally: true`, like the existing conflict. When the param is
  absent, keep every existing behavior, including what an explicit
  `force_remote: false` means for your adapter. When a listed key settles the
  result locally, the harness expects no `/flags` request. Only an inconclusive
  result makes one. Then the harness expects exactly one `/flags` request and
  checks its body: `person_property_keys_known_absent` and
  `group_property_keys_known_absent` carry the caller's lists, and neither field
  has a value when the caller listed nothing.

The harness doesn't check the 1000-key limit. Adapters report SDK errors in
different ways, so the harness can't tell an SDK rejection apart from an
unrelated adapter failure. Cover the limit in the SDK's own tests.

Operator support is checked before the known-absent table. An operator the SDK
doesn't implement locally keeps the flag `unsupported_definition` even when the
key is listed, so it falls back to `/flags`. The suite has one such case,
`unsupported_operator_on_listed_key_falls_back`: a dynamic-cohort leaf uses
`not_between` on a listed key, and the harness expects exactly one `/flags`
request. If the SDK applied the table instead, `not_between` would match
locally and no `/flags` request would be sent. When an SDK implements
`not_between` locally (PostHog/posthog#138581), this case has to change.

Local results for `icontains_multi`, `not_icontains_multi`, `between` and
`not_between`, and `$feature_enrollment/<flag key>` keys are not covered. SDKs
don't evaluate those operators locally yet, and only posthog-python handles
enrollment locally.

### Unusable group keys

Adapters that also advertise `feature_flags_unusable_group_key_v1` run the
suite's `unusable_group_key` cases. They cover the sdk-specs rule that a usable
group key is a non-empty string or a number, as the flags service's
`is_usable_group_key` defines it. An omitted group, an empty string, JSON
`null`, a boolean, an array and an object all count as no group. A condition
aggregated on that group type doesn't match, and a flag whose conditions are all
skipped this way is `false` locally, with no `/flags` request.

Each case evaluates once with `only_evaluate_locally: true` and once with
`allow_remote_fallback: true`, then expects no `/flags` request. The cases cover:

- a group flag with the group omitted, and with an empty string key
- a flag with a group condition and a person condition, where the group key is
  an empty string, so the person condition decides
- a person flag that depends on a group flag, where the group key is an empty
  string, so the dependency is `false`

The cases supply the group properties a usable key would match. An SDK that
used the unusable key would return `true`, and one that treated it as missing
context would call `/flags`.

Advertise `feature_flags_json_group_keys_v1` only when `/get_feature_flag` can
pass JSON `null`, `true`, `[]` and `{}` group keys to the SDK unchanged. Those
cases repeat the group flag case for each kind. Adapters whose SDK types group
keys as strings should leave it out.

## Optional unsupported property-filter types (protocol v1)

Only adapters that advertise **both** `feature_flags_local_evaluation_v1` and
`feature_flags_unsupported_filter_types_v1` run the
`feature_flags_unsupported_filter_types` suite. It covers the sdk-specs
local-feature-flag-evaluator requirement "Unsupported property-filter types are
inconclusive": `person` and `group` filters match caller properties, and a
missing, `null` or empty-string `type` is a person filter. `cohort` and `flag`
filters keep their own matching. Any other type, such as a cohort's
`person_metadata` filter on `created_at` (first seen, which `/flags` reads from
the persons table) or a type added later, is inconclusive.
The SDK must not match it against a caller property with the same key.

The suite needs no new adapter params. It uses:

- `only_evaluate_locally: true` for definitive results, as in the local
  evaluation suite: a `person` filter on `created_at` with the type set,
  omitted, `null` or `""`; another cohort leaf deciding an `AND` group; a
  definitively false person filter deciding a condition that also has an
  unsupported filter; a later catch-all condition deciding after an
  unsupported one; and another flag resolving while one flag is deferred.
- `only_evaluate_locally: true` for inconclusive results. Return no flag value
  (`value` `null` or omitted) when the SDK's local-only getter returns nothing.
  Don't report an inconclusive result as local `false`. The harness checks
  that `value` has no value and that no `/flags` request was made. Leaf
  `negation` and an outer `not_in` must not turn the unsupported leaf into a
  match.
- `allow_remote_fallback: true`, as described in
  [the known-absent protocol](#optional-known-absent-property-keys-protocol-v1).
  Adapters with this capability must honor it even without
  `feature_flags_known_absent_v1`. The harness expects exactly one `/flags`
  request for a `person_metadata` cohort leaf, whatever `created_at` value the
  caller passes.

One case, `local_only_inconclusive_with_created_at_known_absent`, also lists
`created_at` as known absent and runs only when the adapter advertises
`feature_flags_known_absent_v1`. A known-absent listing doesn't resolve an
unsupported filter.

Adapters that also advertise `feature_flags_unresolved_reasons_v1` run three
more cases that check the reason. For an `only_evaluate_locally` request with no
value, return `unresolved_reason` in the `/get_feature_flag` response, read
from the SDK's unresolved flags API (sdk-specs evaluate-flags, "Snapshot
unresolved flag reporting"). For example, a Python adapter can call
`client.evaluate_flags(..., flag_keys=[key], only_evaluate_locally=True)` and
return `snapshot.unresolved_flags.get(key)`. The suite expects
`unsupported_definition`. Omit the field when the flag has a value. Exposing
unresolved flags is optional in the spec, so SDKs without that API skip these
cases and still run the rest of the suite. Cover the load-time definition check
in the SDK's own tests.

## Optional local evaluation of config version 2 flags

Only adapters that advertise **both** `feature_flags_local_evaluation_v1` and
`feature_flags_local_evaluation_v2` run the `feature_flags_local_evaluation_v2`
suite. It covers the sdk-specs change for local evaluation of config version 2
flags (sdk-specs #244): the SDK sends exactly one `v=2` on every definitions
request and evaluates config version 2 flags itself. Declare the capability
only in an SDK release that sends `v=2`.

The definitions mock serves one document to a request with exactly one `v`
query parameter equal to `2`, and another to every other request. In this suite
the other document holds only the config version 1 flags, as the legacy feed
does, so an SDK that leaves out `v=2` cannot read a config version 2 flag. Each
document can carry its own ETag, and a request whose `If-None-Match` matches the
ETag of the document it selects gets `304`. The definitions request query is
recorded with every value of each parameter.

The suite needs no new adapter route. It uses:

- `/reload_feature_flag_definitions`, as in the local evaluation suite. One
  test reloads twice with an ETag on the v=2 document: the second reload must
  send that ETag with `v=2` and get `304`, and the SDK must keep its config
  version 2 flags. Report `ready: true` after a `304`.
- `only_evaluate_locally: true` for local results and for results with no value,
  as in the unsupported property-filter types suite.
- `allow_remote_fallback: true`, as described in
  [the known-absent protocol](#optional-known-absent-property-keys-protocol-v1),
  for reads that send events and for fallbacks. Adapters with this capability
  must honor it even without `feature_flags_known_absent_v1`. Flush the
  `$feature_flag_called` and `$experiment_exposure` events before returning, or
  let the harness `flush` do it.
- `/get_feature_flag_payload` with `only_evaluate_locally: true` for number and
  object values, when the adapter advertises `feature_flag_payloads`.
- `unresolved_reason`, when the adapter advertises
  `feature_flags_unresolved_reasons_v1`.

Definitions and expectations come from the feature flag rules v2 contract
package. The feed tests serve `fixtures/wire/definitions_feed.json` and read it
with its context. The corpus tests serve one flag from each case of the corpora
that `rules/local_outcomes.json` maps, when a black-box SDK can run the case: UTC,
a non-empty distinct ID, no relative date, no private seam, and no key read from
a complete property map that the map lacks. A conclusive case must return its
legacy value with no `/flags` request and send one `$feature_flag_called` with
`locally_evaluated: true`, the definition's `$feature_flag_id` and
`$feature_flag_version`, and the case's config version 2 context; an experiment
split also sends one `$experiment_exposure`. An inconclusive case or an
unsupported feed entry falls back to `/flags` once in auto mode, and has no
value in local-only mode, while the other entries keep their local values. The
fallback is answered with an invented `/flags?v=4` record whose value a local
evaluator cannot produce.
