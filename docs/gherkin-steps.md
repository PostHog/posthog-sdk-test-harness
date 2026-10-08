# Gherkin step catalogue

Generated from the v2 runner's complete step registry. Regenerate from the harness
checkout used for validation:

```sh
posthog-test-harness-v2 steps > docs/gherkin-steps.md
```

This reference lists **registered harness bindings**, not SDK support or conformance.
The adapter must negotiate the required public routes and fixture capabilities.
Earlier steps must establish the state used by later assertions. Legacy bindings and
fixture controls remain listed; registration alone does not make them appropriate
for new black-box acceptance tests.

## Matching and arguments

- Patterns are Python regular expressions, matched against the entire step text,
  without the `Given`, `When`, `Then`, `And`, or `But` keyword. Matching is case-sensitive.
  Supply concrete values in place of regex capture groups; do not paste a pattern
  verbatim as a parameterized Gherkin step. Exactly one binding must match.
- `none` means no doc string or table is accepted. `docString` and `dataTable` require
  that exact Gherkin argument kind. The linked handler defines the content schema,
  including JSON fields, media types, table headers, and allowed values.
- Routes and fixture capabilities are declarations for that binding. `None declared`
  does not mean a step can run independently of SDK setup or earlier observations.
- Use `discover --require-ready` to check an authored feature's bindings. Discovery
  does not negotiate adapter support or execute the SDK. Execute the selected cases
  against the real SDK to establish behavior.

## Example

This server example uses JSON arguments and a data table. It requires `/setup`,
`/capture`, and `/flush`; the adapter owns mapping JSON arguments to its public SDK API.
The table allows the listed ingestion paths without requiring a particular transport.

```gherkin
@sdk:server
Scenario: Capture delivers an event
  Given an isolated SDK instance
  And the SDK is initialized with token "test-token" and flush threshold 20
  When capture is called with JSON arguments:
    """application/json
    {"event":"catalogue-example","distinct_id":"catalogue-user","properties":{"area":"checkout"}}
    """
  And pending captures are flushed
  Then exactly 1 capture request should have been received
  And the first request should contain exactly 1 parsed events
  And the first received event field "event" should equal "catalogue-example"
  And the first received event field "distinct_id" should equal "catalogue-user"
  And the first received event property "area" should equal "checkout"
  And every capture request path should be one of:
    | path                   |
    | /batch                 |
    | /i/v0/e                |
    | /i/v1/analytics/events |
```

## Registered bindings

### ai_steps

```text
AI capture should return an admitted event UUID
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`admitted`](../src/posthog_test_harness/v2/ai_steps.py).

```text
alias is called with JSON arguments:
```
Argument: `docString`. Routes: `/alias`. Fixture capabilities: None declared.
Handler: [`alias`](../src/posthog_test_harness/v2/ai_steps.py).

```text
an isolated SDK instance
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`isolated_instance`](../src/posthog_test_harness/v2/ai_steps.py).

```text
an isolated SDK with empty persistent storage
```
Argument: `none`. Routes: None declared. Fixture capabilities: `storage.empty.v1`.
Handler: [`isolated`](../src/posthog_test_harness/v2/ai_steps.py).

```text
capture is called with JSON arguments:
```
Argument: `docString`. Routes: `/capture`. Fixture capabilities: None declared.
Handler: [`capture`](../src/posthog_test_harness/v2/ai_steps.py).

```text
capture_ai is called with JSON arguments:
```
Argument: `docString`. Routes: `/capture_ai`. Fixture capabilities: None declared.
Handler: [`capture_ai`](../src/posthog_test_harness/v2/ai_steps.py).

```text
every capture request path should be one of:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`paths`](../src/posthog_test_harness/v2/ai_steps.py).

```text
every event in the first capture request should have UTC timestamp "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`timestamp`](../src/posthog_test_harness/v2/ai_steps.py).

```text
exactly ([0-9]+) capture request should have been received
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`count`](../src/posthog_test_harness/v2/ai_steps.py).

```text
identify is called with JSON arguments:
```
Argument: `docString`. Routes: `/identify`. Fixture capabilities: None declared.
Handler: [`identify`](../src/posthog_test_harness/v2/ai_steps.py).

```text
no capture request should use "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`forbidden_path`](../src/posthog_test_harness/v2/ai_steps.py).

```text
pending captures are flushed
```
Argument: `none`. Routes: `/flush`. Fixture capabilities: None declared.
Handler: [`flush`](../src/posthog_test_harness/v2/ai_steps.py).

```text
the AI capture return should equal "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`returned_uuid`](../src/posthog_test_harness/v2/ai_steps.py).

```text
the AI capture return should equal the first received event UUID
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`uuid_equal`](../src/posthog_test_harness/v2/ai_steps.py).

```text
the SDK is initialized with token "([^"]*)" and flush threshold ([0-9]+)
```
Argument: `none`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup`](../src/posthog_test_harness/v2/ai_steps.py).

```text
the first received event UUID should be valid
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`valid_uuid`](../src/posthog_test_harness/v2/ai_steps.py).

```text
the first received event field "([^"]*)" should be a UUID
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_field_uuid`](../src/posthog_test_harness/v2/ai_steps.py).

```text
the first received event field "([^"]*)" should equal "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_field`](../src/posthog_test_harness/v2/ai_steps.py).

```text
the first received event property "([^"]*)" should equal "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_property`](../src/posthog_test_harness/v2/ai_steps.py).

```text
the first received identify event disables person-profile processing
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`identify_personless`](../src/posthog_test_harness/v2/ai_steps.py).

### analytics_outcome_steps

```text
the SDK is initialized with token "([^"]*)", flush threshold ([0-9]+), and compression disabled
```
Argument: `none`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup_uncompressed`](../src/posthog_test_harness/v2/analytics_outcome_steps.py).

```text
the SDK is initialized with token "([^"]*)", flush threshold ([0-9]+), and historical migration enabled
```
Argument: `none`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup_historical`](../src/posthog_test_harness/v2/analytics_outcome_steps.py).

```text
the first received event option "([^"]*)" should be absent
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`absent_option`](../src/posthog_test_harness/v2/analytics_outcome_steps.py).

```text
the first request body should contain historical_migration equal to true
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`historical_body`](../src/posthog_test_harness/v2/analytics_outcome_steps.py).

```text
the first request header "([^"]*)" should be absent
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`absent_header`](../src/posthog_test_harness/v2/analytics_outcome_steps.py).

```text
the last request should contain exactly ([0-9]+) parsed events
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`last_count`](../src/posthog_test_harness/v2/analytics_outcome_steps.py).

```text
the second request should retain first-response retry UUIDs and omit its terminal UUIDs
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`partial_pruning`](../src/posthog_test_harness/v2/analytics_outcome_steps.py).

### analytics_retry_steps

```text
all recorded request IDs should equal the nonempty first request ID
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`preserved_request_id`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
all recorded request attempts should be consecutive integers starting at one
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`attempts`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
at least one recorded response should have status 200
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`any_success`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
every first mock-authored response result should equal "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`results_equal`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
every received batch should have no duplicate nonempty UUIDs
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_duplicates`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
exactly one recorded request excluding paths containing /flags should have been received
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_retry`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the SDK is initialized with token "([^"]*)" and maximum retries ([0-9]+)
```
Argument: `none`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup_retries`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the first inter-request delay should be at least ([0-9]+) milliseconds
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`first_delay`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the first mock-authored response Retry-After should be (present|absent)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`response_retry_after`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the first mock-authored response should contain a results object
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`results_map`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the first mock-authored response should contain exactly ([0-9]+) results
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`results_count`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the first mock-authored response should echo the nonempty sent request ID
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`response_echo`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the first mock-authored response should have status ([0-9]+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`response_status`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the first two request headers "(posthog-request-id|posthog-request-timestamp)" should be nonempty and different
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`different_headers`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the mock serves these ordered analytics responses:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`responses`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

```text
the present event "(uuid|timestamp)" lists in requests zero and one should be identical
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`preserved_events`](../src/posthog_test_harness/v2/analytics_retry_steps.py).

### analytics_steps

```text
alias is called with previous distinct id "([^"]*)" and distinct id "([^"]*)"
```
Argument: `none`. Routes: `/alias`. Fixture capabilities: None declared.
Handler: [`alias`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
capture is called with distinct id "([^"]*)", event "([^"]*)", and properties:
```
Argument: `dataTable`. Routes: `/capture`. Fixture capabilities: None declared.
Handler: [`capture_explicit`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
capture is called with event "([^"]*)"
```
Argument: `none`. Routes: `/capture`. Fixture capabilities: None declared.
Handler: [`capture`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
capture is called with event "([^"]*)" and properties:
```
Argument: `dataTable`. Routes: `/capture`. Fixture capabilities: None declared.
Handler: [`capture_properties`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
identify is called with distinct id "([^"]*)"
```
Argument: `none`. Routes: `/identify`. Fixture capabilities: None declared.
Handler: [`identify_only`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
identify is called with distinct id "([^"]*)" and properties:
```
Argument: `dataTable`. Routes: `/identify`. Fixture capabilities: None declared.
Handler: [`identify`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
no event named "([^"]*)" should be enqueued
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`no_named_event`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
no event should be enqueued
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`no_events`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
one event named "([^"]*)" should be enqueued
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`enqueued`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
the SDK is flushed
```
Argument: `none`. Routes: `/flush`. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`sdk_flushed`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
the enqueued event distinct id should be "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_identity`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
the enqueued event properties should include:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_properties`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
the enqueued event property "([^"]*)" should equal "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_property`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
the enqueued event should include a timestamp and uuid
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_timestamp_uuid`](../src/posthog_test_harness/v2/analytics_steps.py).

```text
the enqueued event should include an event uuid
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_uuid`](../src/posthog_test_harness/v2/analytics_steps.py).

### analytics_wire_steps

```text
([0-9]+) milliseconds elapse without a public SDK call
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`elapsed`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
all present UUIDs across received requests should be unique
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`unique_uuids`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
at least ([0-9]+) capture request should have been received
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`count_at_least`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
capture is called sequentially ([0-9]+) times with zero-based top-level index substitution:
```
Argument: `docString`. Routes: `/capture`. Fixture capabilities: None declared.
Handler: [`capture_sequence`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
every event in the first capture request should contain these root fields:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_fields`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
every event in the first capture request should have a canonical UTC timestamp
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_timestamps`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the SDK is initialized with token "([^"]*)" and no additional configuration
```
Argument: `none`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup_defaults`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first received event field "([^"]*)" should be a string
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_string`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first received event property "([^"]*)" should be an object
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`property_object`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first received event property "([^"]*)" should equal JSON (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`property_json`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first received event should contain "([^"]*)" at root and not in properties
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_placement`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first received event should contain root field "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_presence`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request body should have a canonical UTC created_at and a nonempty batch array
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`body_format`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request body should omit these root fields:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`body_omissions`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request created_at should be within ([0-9]+) seconds of the current wall clock
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`created_at_recent`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request header "([^"]*)" should be a canonical UTC timestamp
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`header_timestamp`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request header "([^"]*)" should be a valid UUID
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`header_uuid`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request header "([^"]*)" should be integer ([0-9]+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`header_integer`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request header "([^"]*)" should match "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`header_matches`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request should authenticate with bearer token "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`bearer`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first request should contain exactly ([0-9]+) parsed events
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`batch_count`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

```text
the first two present UUIDs across received requests should differ
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`different_uuids`](../src/posthog_test_harness/v2/analytics_wire_steps.py).

### cached_flag_steps

```text
cached feature flag "([^"]*)" changes to "([^"]*)"
```
Argument: `none`. Routes: `/update_flags`. Fixture capabilities: None declared.
Handler: [`change_flag`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
cached feature flags are empty
```
Argument: `none`. Routes: `/update_flags`. Fixture capabilities: None declared.
Handler: [`prepare_empty_flags`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
cached feature flags are:
```
Argument: `dataTable`. Routes: `/update_flags`. Fixture capabilities: None declared.
Handler: [`prepare_flags`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
cached feature flags should be empty
```
Argument: `none`. Routes: `/get_feature_flags`. Fixture capabilities: None declared.
Handler: [`cache_empty`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
exactly one event named "\$feature_flag_called" should be enqueued for flag "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`one_exposure`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
get feature flag "([^"]*)" is called for distinct id "([^"]*)"
```
Argument: `none`. Routes: `/get_feature_flag`. Fixture capabilities: None declared.
Handler: [`value_read_identity`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
get feature flag "([^"]*)" is called with tracking disabled
```
Argument: `none`. Routes: `/get_feature_flag`. Fixture capabilities: None declared.
Handler: [`value_read_silent`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
get feature flag "([^"]*)" is called(?: again)?
```
Argument: `none`. Routes: `/get_feature_flag`. Fixture capabilities: None declared.
Handler: [`value_read`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
get feature flag payload "([^"]*)" is called
```
Argument: `none`. Routes: `/get_feature_flag_payload`. Fixture capabilities: None declared.
Handler: [`payload_read`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
get feature flag result "([^"]*)" is called
```
Argument: `none`. Routes: `/get_feature_flag_result`. Fixture capabilities: None declared.
Handler: [`result_read`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
get feature flags and payloads is called
```
Argument: `none`. Routes: `/get_feature_flags_and_payloads`. Fixture capabilities: None declared.
Handler: [`paired_read`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
get feature flags is called
```
Argument: `none`. Routes: `/get_feature_flags`. Fixture capabilities: None declared.
Handler: [`bulk_read`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
get feature flags is called for distinct id "([^"]*)"
```
Argument: `none`. Routes: `/get_feature_flags`. Fixture capabilities: None declared.
Handler: [`bulk_read_identity`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
is feature enabled "([^"]*)" is called
```
Argument: `none`. Routes: `/is_feature_enabled`. Fixture capabilities: None declared.
Handler: [`enabled_read`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
is feature enabled "([^"]*)" is called with default value (true|false)
```
Argument: `none`. Routes: `/is_feature_enabled`. Fixture capabilities: None declared.
Handler: [`enabled_read_default`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
is feature enabled "([^"]*)" is called with tracking disabled
```
Argument: `none`. Routes: `/is_feature_enabled`. Fixture capabilities: None declared.
Handler: [`enabled_read_silent`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
no exception should be thrown
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_exception`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
no feature flag network request should be sent
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_flags_network`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
no feature flag result should be returned
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_result`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
no payload should be returned
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_payload`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
reset is called
```
Argument: `none`. Routes: `/reset`. Fixture capabilities: None declared.
Handler: [`reset`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned enabled value should be (true|false)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`enabled_assertion`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned feature flag payloads should be empty
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`empty_payloads`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned feature flag payloads should be:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`paired_payloads`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned feature flag result should include:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`result_fields`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned feature flag result should not include a variant
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_variant`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned feature flag value should be (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`value_assertion`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned feature flag values should be empty
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`empty_values`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned feature flag values should be:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`paired_values`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned feature flags should be:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`bulk_values`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
the returned payload should include:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`payload_assertion`](../src/posthog_test_harness/v2/cached_flag_steps.py).

```text
two "\$feature_flag_called" events should be enqueued for flag "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`two_exposures`](../src/posthog_test_harness/v2/cached_flag_steps.py).

### capture_amendment_steps

```text
a received request header "([^"]*)" should equal "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`any_header`](../src/posthog_test_harness/v2/capture_amendment_steps.py).

```text
the SDK is initialized with token "([^"]*)" and compression "(gzip|deflate|br|zstd)"(?: and flush threshold ([0-9]+))?
```
Argument: `none`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup_compression`](../src/posthog_test_harness/v2/capture_amendment_steps.py).

```text
the SDK is initialized with token "([^"]*)", flush threshold ([0-9]+), and GeoIP disabled
```
Argument: `none`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup_geoip`](../src/posthog_test_harness/v2/capture_amendment_steps.py).

```text
the first encoded request should decompress to parseable events
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`decompressed_events`](../src/posthog_test_harness/v2/capture_amendment_steps.py).

```text
the first received event option "([^"]*)" should equal JSON (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`option_json`](../src/posthog_test_harness/v2/capture_amendment_steps.py).

### concurrent_steps

```text
evaluate flags is started concurrently for disjoint scopes containing "([^"]*)" and "([^"]*)"
```
Argument: `none`. Routes: `/evaluate_flags`. Fixture capabilities: `invocation.concurrent.v1`.
Handler: [`start`](../src/posthog_test_harness/v2/concurrent_steps.py).

```text
no local feature flag definitions are loaded for "([^"]*)" and "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: `flags.definitions.install.v1`.
Handler: [`absent`](../src/posthog_test_harness/v2/concurrent_steps.py).

```text
remote feature flag evaluation responses for "([^"]*)" and "([^"]*)" are delayed
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`delayed`](../src/posthog_test_harness/v2/concurrent_steps.py).

```text
two remote feature flag evaluation requests should be in flight before either response is released
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`overlapping`](../src/posthog_test_harness/v2/concurrent_steps.py).

### flag_steps

```text
(?:a|only one deduped) "\$feature_flag_called" event should be enqueued for flag "([^"]*)" with value "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`exposure`](../src/posthog_test_harness/v2/flag_steps.py).

```text
an event named "([^"]*)" is captured for distinct id "([^"]*)" with the evaluation snapshot
```
Argument: `none`. Routes: `/capture`. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`capture_snapshot`](../src/posthog_test_harness/v2/flag_steps.py).

```text
evaluate flags is called for distinct id "([^"]*)"
```
Argument: `none`. Routes: `/evaluate_flags`. Fixture capabilities: None declared.
Handler: [`evaluate_identity`](../src/posthog_test_harness/v2/flag_steps.py).

```text
evaluate flags is called for distinct id "([^"]*)" with flag keys:
```
Argument: `dataTable`. Routes: `/evaluate_flags`. Fixture capabilities: None declared.
Handler: [`evaluate_keys`](../src/posthog_test_harness/v2/flag_steps.py).

```text
exactly one remote feature flag evaluation request should have been sent
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`one_evaluation`](../src/posthog_test_harness/v2/flag_steps.py).

```text
no additional remote feature flag evaluation request should have been sent
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_additional_evaluation`](../src/posthog_test_harness/v2/flag_steps.py).

```text
remote feature flag evaluation for distinct id "([^"]*)" (?:returns|can return):
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`remote_flags`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot enablement for "([^"]*)" should be (true|false)
```
Argument: `none`. Routes: `/snapshot/is_enabled`. Fixture capabilities: None declared.
Handler: [`enabled_assertion`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot enablement is read(?: again)? for "([^"]*)"
```
Argument: `none`. Routes: `/snapshot/is_enabled`. Fixture capabilities: None declared.
Handler: [`read_enablement`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot keys are enumerated
```
Argument: `none`. Routes: `/snapshot/keys`. Fixture capabilities: None declared.
Handler: [`read_keys`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot only accessed is called(?: before a value or enablement read)?
```
Argument: `none`. Routes: `/snapshot/only_accessed`. Fixture capabilities: None declared.
Handler: [`only_accessed`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot only accessed should return no flags
```
Argument: `none`. Routes: `/snapshot/keys`, `/snapshot/only_accessed`. Fixture capabilities: None declared.
Handler: [`only_accessed_empty`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot only is called with flag keys "([^"]*)" and "([^"]*)"
```
Argument: `none`. Routes: `/snapshot/only`. Fixture capabilities: None declared.
Handler: [`only`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot payload is read for "([^"]*)"
```
Argument: `none`. Routes: `/snapshot/get_flag_payload`. Fixture capabilities: None declared.
Handler: [`read_payload`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot value for "([^"]*)" should be (.+)
```
Argument: `none`. Routes: `/snapshot/get_flag`. Fixture capabilities: None declared.
Handler: [`value_assertion`](../src/posthog_test_harness/v2/flag_steps.py).

```text
snapshot value is read(?: again)? for "([^"]*)"
```
Argument: `none`. Routes: `/snapshot/get_flag`. Fixture capabilities: None declared.
Handler: [`read_value`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the (filtered snapshot|snapshot) should not contain "([^"]*)"
```
Argument: `none`. Routes: `/snapshot/keys`. Fixture capabilities: None declared.
Handler: [`absent_key`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the captured event property "([^"]*)" should (not )?contain "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`capture_membership`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the captured event should have property "([^"]*)" equal to (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`capture_value`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the filtered snapshot should contain flags:
```
Argument: `dataTable`. Routes: `/snapshot/get_flag`, `/snapshot/keys`. Fixture capabilities: None declared.
Handler: [`filtered_values`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the filtered snapshot should contain no flags
```
Argument: `none`. Routes: `/snapshot/keys`. Fixture capabilities: None declared.
Handler: [`filtered_empty`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the filtered snapshot should contain only "([^"]*)"
```
Argument: `none`. Routes: `/snapshot/keys`. Fixture capabilities: None declared.
Handler: [`only_key`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the remote feature flag evaluation request should include only flag keys "([^"]*)" and "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`request_keys`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the returned enabled value for "([^"]*)" should be (true|false)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`enabled_result`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the returned snapshot payload for "([^"]*)" should be `(.+)`
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`payload_result`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the returned snapshot value for "([^"]*)" should be (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`value_result`](../src/posthog_test_harness/v2/flag_steps.py).

```text
the snapshot should expose the boolean, value, payload, and both keys from the same evaluation
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`consistent_reads`](../src/posthog_test_harness/v2/flag_steps.py).

### legacy_capture_steps

```text
an event in the first request should resolve token "([^"]*)" from event then property token or api_key
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_token`](../src/posthog_test_harness/v2/legacy_capture_steps.py).

```text
the first received event should contain property "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`property_presence`](../src/posthog_test_harness/v2/legacy_capture_steps.py).

```text
the first request body should contain a batch array( and an api_key field)?
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`batch_format`](../src/posthog_test_harness/v2/legacy_capture_steps.py).

```text
the first request should contain token "([^"]*)" at event token or body api_key or token
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`request_token`](../src/posthog_test_harness/v2/legacy_capture_steps.py).

### local_flag_steps

```text
cached feature flag evaluation for distinct id "([^\"]*)" contains:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: `flags.evaluation_cache.put.v1`.
Handler: [`evaluation_cache`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
evaluate flags is called for distinct id "([^\"]*)" with an empty flag key list
```
Argument: `none`. Routes: `/evaluate_flags`. Fixture capabilities: `flags.evaluation_activity.v1`.
Handler: [`empty_evaluation`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
evaluate flags is called for distinct id "([^\"]*)" with local-only evaluation enabled
```
Argument: `none`. Routes: `/evaluate_flags`. Fixture capabilities: None declared.
Handler: [`local_only`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
local feature flag definitions cannot resolve "([^\"]*)" for distinct id "([^\"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: `flags.definitions.install.v1`.
Handler: [`inconclusive_definition`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
local feature flag definitions include a flag "([^\"]*)" rolled out to distinct id "([^\"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: `flags.definitions.install.v1`.
Handler: [`definition_rollout`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
local feature flag definitions include flags:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: `flags.definitions.install.v1`.
Handler: [`definition_bulk`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
local feature flag definitions resolve "([^\"]*)" for distinct id "([^\"]*)" as (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: `flags.definitions.install.v1`.
Handler: [`definition_value`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
local feature flag definitions resolve for distinct id "([^\"]*)":
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: `flags.definitions.install.v1`.
Handler: [`definition_values`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
no cached feature flag evaluation result should have been consulted
```
Argument: `none`. Routes: None declared. Fixture capabilities: `flags.evaluation_activity.v1`.
Handler: [`no_cache_lookup`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
no local feature flag definition is loaded for "([^\"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: `flags.definitions.install.v1`.
Handler: [`absent_definition`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
no local feature flag evaluation should have been attempted
```
Argument: `none`. Routes: None declared. Fixture capabilities: `flags.evaluation_activity.v1`.
Handler: [`no_local_evaluation`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
no remote feature flag evaluation request should have been sent
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_remote`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
the next remote feature flag evaluation request fails
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`fail_evaluation`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
the returned evaluation snapshot should be empty
```
Argument: `none`. Routes: `/snapshot/keys`. Fixture capabilities: None declared.
Handler: [`empty_snapshot`](../src/posthog_test_harness/v2/local_flag_steps.py).

```text
the snapshot should contain "([^\"]*)" with value (.+)
```
Argument: `none`. Routes: `/snapshot/get_flag`. Fixture capabilities: None declared.
Handler: [`snapshot_value`](../src/posthog_test_harness/v2/local_flag_steps.py).

### local_parity_steps

```text
local definitions are publicly reloaded within 5000 milliseconds
```
Argument: `none`. Routes: `/reload_feature_flags`. Fixture capabilities: None declared.
Handler: [`reload`](../src/posthog_test_harness/v2/local_parity_steps.py).

```text
no remote flag evaluation path should have been requested
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_remote_paths`](../src/posthog_test_harness/v2/local_parity_steps.py).

```text
the SDK is initialized for native local evaluation with JSON arguments:
```
Argument: `docString`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup`](../src/posthog_test_harness/v2/local_parity_steps.py).

```text
the definitions service serves this typed document:
```
Argument: `docString`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`definitions`](../src/posthog_test_harness/v2/local_parity_steps.py).

```text
the local flag getter is called with JSON arguments:
```
Argument: `docString`. Routes: `/get_feature_flag`. Fixture capabilities: None declared.
Handler: [`getter`](../src/posthog_test_harness/v2/local_parity_steps.py).

```text
the local flag getter should return JSON (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`result`](../src/posthog_test_harness/v2/local_parity_steps.py).

### probe_steps

```text
both evaluation snapshots should not contain "([^"]*)"
```
Argument: `none`. Routes: `/snapshot/keys`. Fixture capabilities: None declared.
Handler: [`both_absent`](../src/posthog_test_harness/v2/probe_steps.py).

```text
evaluate flags is started concurrently for distinct ids "([^"]*)" and "([^"]*)" with flag key "([^"]*)"
```
Argument: `none`. Routes: `/evaluate_flags`. Fixture capabilities: `invocation.concurrent.v1`.
Handler: [`concurrent`](../src/posthog_test_harness/v2/probe_steps.py).

```text
exactly one remote feature flag evaluation request should be in flight
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`held`](../src/posthog_test_harness/v2/probe_steps.py).

```text
exactly two remote feature flag evaluation requests should have been sent
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`two_requests`](../src/posthog_test_harness/v2/probe_steps.py).

```text
the (first|second) evaluation snapshot should contain "([^"]*)" with value (.+)
```
Argument: `none`. Routes: `/snapshot/get_flag`. Fixture capabilities: None declared.
Handler: [`value`](../src/posthog_test_harness/v2/probe_steps.py).

```text
the clean remote response omitting "([^"]*)" is delayed
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`omission`](../src/posthog_test_harness/v2/probe_steps.py).

```text
the delayed remote feature flag evaluation response for distinct id "([^"]*)" returns:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`first_response`](../src/posthog_test_harness/v2/probe_steps.py).

```text
the delayed remote feature flag evaluation response is released
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`release`](../src/posthog_test_harness/v2/probe_steps.py).

```text
the following remote feature flag evaluation response for distinct id "([^"]*)" returns:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`following_response`](../src/posthog_test_harness/v2/probe_steps.py).

### remote_flag_steps

```text
a feature flag listener is registered
```
Argument: `none`. Routes: `/on_feature_flags`. Fixture capabilities: `callbacks.continuation`.
Handler: [`register`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
a received event named "([^"]*)" should have property "([^"]*)" equal to JSON (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_property`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
exactly ([0-9]+) received events should be named "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`event_count`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
exactly ([0-9]+) requests containing /flags should have been received
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`count`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
feature flags are (?:already )?loaded with values:
```
Argument: `dataTable`. Routes: `/update_flags`. Fixture capabilities: None declared.
Handler: [`loaded`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
get feature flag is called with JSON arguments:
```
Argument: `docString`. Routes: `/get_feature_flag`. Fixture capabilities: None declared.
Handler: [`getter`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
the feature flag listener is unsubscribed
```
Argument: `none`. Routes: `/subscription/unsubscribe`. Fixture capabilities: None declared.
Handler: [`unsubscribe`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
the feature flag listener should be invoked with flags:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`callback_values`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
the feature flag listener should not be invoked again
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`removed`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
the first flags request field "([^"]*)" should equal JSON (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`field`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
the first flags request query parameter "([^"]*)" should equal "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`query`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
the mock serves these ordered flag responses:
```
Argument: `docString`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`responses`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
the public flag getter should return JSON (.+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`result`](../src/posthog_test_harness/v2/remote_flag_steps.py).

```text
the server uses its native flag startup and getter behavior with no installed local definitions or results
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`native_server`](../src/posthog_test_harness/v2/remote_flag_steps.py).

### steps

```text
a fresh SDK acceptance test harness
```
Argument: `none`. Routes: None declared. Fixture capabilities: `scheduler.manual.v1`.
Handler: [`fresh`](../src/posthog_test_harness/v2/steps.py).

```text
flush is called
```
Argument: `none`. Routes: `/flush`. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`flush`](../src/posthog_test_harness/v2/steps.py).

```text
no network request should be sent
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_network`](../src/posthog_test_harness/v2/steps.py).

```text
persistent storage is empty
```
Argument: `none`. Routes: None declared. Fixture capabilities: `storage.empty.v1`.
Handler: [`storage`](../src/posthog_test_harness/v2/steps.py).

```text
the SDK clock is fixed at "([^"]*)"
```
Argument: `none`. Routes: None declared. Fixture capabilities: `clock.fixed.v1`.
Handler: [`clock`](../src/posthog_test_harness/v2/steps.py).

```text
the SDK is initialized with token "([^"]*)"
```
Argument: `none`. Routes: `/setup`. Fixture capabilities: None declared.
Handler: [`setup`](../src/posthog_test_harness/v2/steps.py).

```text
the call should not throw
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`no_throw`](../src/posthog_test_harness/v2/steps.py).

```text
the event named "([^"]*)" should remain queued for retry
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`retryable`](../src/posthog_test_harness/v2/steps.py).

```text
the event queue contains events:
```
Argument: `dataTable`. Routes: `/capture`. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`queued`](../src/posthog_test_harness/v2/steps.py).

```text
the event queue is empty
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`empty_queue`](../src/posthog_test_harness/v2/steps.py).

```text
the event queue should be empty after a successful flush
```
Argument: `none`. Routes: None declared. Fixture capabilities: `queue.snapshot.v1`.
Handler: [`drained`](../src/posthog_test_harness/v2/steps.py).

```text
the mock PostHog server is reset
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`reset_server`](../src/posthog_test_harness/v2/steps.py).

```text
the mock server should receive a batch containing events:
```
Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`received`](../src/posthog_test_harness/v2/steps.py).

```text
the mock server will fail the next ingestion request with status ([0-9]+)
```
Argument: `none`. Routes: None declared. Fixture capabilities: None declared.
Handler: [`fail_ingestion`](../src/posthog_test_harness/v2/steps.py).
