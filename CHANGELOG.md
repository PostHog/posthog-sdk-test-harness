# Changelog

All notable changes to the PostHog SDK Test Harness will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Removed `error_response_has_structured_body` and `error_response_has_correct_tag` tests from `response_format_validation` -- these tested mock fidelity, not SDK behavior
- Clarified in `test_actions.yaml` that `assert_v1_error_response_format`, `assert_v1_error_tag`, and `assert_v1_per_event_result` are infrastructure for integration tests, not used in the base SDK compliance suite

### Added

- `unknown_result_treated_as_terminal` test: SDK does not retry events with unrecognized per-event result strings (forward compatibility)
- `respects_retry_after_on_retryable_error` test: SDK honors `Retry-After` header on 503 error responses

## [0.6.0] - 2026-05-26

### Changed

- **Breaking**: V1 capture endpoint path updated from `/i/v1/e` to `/i/v1/events/analytics` throughout mock server and test suite
- **Breaking**: V1 response format changed from array-based `{"results": [...]}` to UUID-keyed map `{"results": {"<uuid>": {"result": "...", "details": "..."}}}` matching the Rust capture service
- `v1_event_results` in `MockResponse` now accepts dicts with explicit `result`/`details` in addition to string shorthands
- Renamed `assert_attempt_timestamp_changes_on_retry` to `assert_request_timestamp_changes_on_retry` (header was renamed from `PostHog-Attempt-Timestamp` to `PostHog-Request-Timestamp`)
- Replaced `assert_authorization_and_token_match` (which referenced phantom `PostHog-Api-Token` header) with `assert_authorization_bearer_token` matching V1 auth spec

### Added

- Mock server now auto-generates structured V1 error bodies `{"error", "error_description", "error_uri"}` for non-200 responses on the V1 capture path
- Mock server now echoes `PostHog-Request-Id` and adds `Date` header on V1 capture responses
- Mock server adds `Retry-After: 1` on retryable status codes (408, 500, 503, 504) and on 200 responses with `result: retry` events
- 11 new assertion actions for V1 response validation: `assert_v1_error_response_format`, `assert_v1_error_tag`, `assert_v1_per_event_result`, `assert_v1_all_events_result`, `assert_v1_retry_after_present`, `assert_v1_retry_after_absent`, `assert_v1_response_echoes_request_id`, `assert_sdk_did_not_retry`, `assert_v1_response_has_results_map`, `assert_v1_response_results_count`, `assert_v1_response_status`
- V1 test suite covering the full `/i/v1/events/analytics` contract:
  - Response format validation: UUID-keyed results, Retry-After semantics, request-id echo
  - Retryable vs non-retryable status codes: 408/500/503/504 trigger retry; 400/401/402/413/415 do not
  - Partial batch handling with per-event `ok`/`drop`/`limited`/`retry` result pruning

### Fixed

- `assert_partial_batch_retry_pruning` now supports UUID-keyed results (V1) in addition to legacy array results
- Removed tests asserting phantom `PostHog-Api-Token` header that does not exist in V1 spec
- Fixed header name `PostHog-Attempt-Timestamp` to `PostHog-Request-Timestamp` in tests and assertions
- Removed 502/429 retry tests that do not correspond to V1 capture service error types

## [0.5.2] - 2026-05-22

### Fixed

- `/flags` request field assertions now accept `api_key` as an alias for `token`, matching the endpoint's accepted authentication fields.

## [0.5.1] - 2026-05-04

### Changed

- `ContractExecutor` now records `ctx.last_action_result` only for adapter actions that opt in via `Action.records_result = True` (currently just `get_feature_flag`). Previously every non-`assert_*` action overwrote the value, which let incidental actions (`init`, `flush`, `configure_mock_responses`, ...) silently clobber the value the next `assert_action_result` was about to read.

### Refactored

- Extracted `_capture_events(ctx)` helper used by `assert_event_count_with_name` and `assert_event_property_in_named_event` so the "skip /flags requests" filter lives in one place.

## [0.5.0] - 2026-05-04

### Added

- Feature flag contract suite expanded with 16 tests across 3 categories (`request_payload`, `request_lifecycle`, `side_effect_events`) locking down externally observable `/flags` request payload, request lifecycle, and the documented `$feature_flag_called` capture side-effect for server SDKs
- 4 new assertion actions: `assert_flags_request_query_param`, `assert_event_count_with_name`, `assert_event_property_in_named_event`, `assert_action_result`
- `ContractExecutor` now records the return value of every non-assertion action on `ctx.last_action_result` so `assert_action_result` can verify what the adapter returned

### Changed

- Existing `/flags` request payload contract test now also asserts top-level `distinct_id` on the request body (both server SDKs send it)
- `minimal_adapter` and `parallel_adapter` examples now send `?v=2` on `/flags` requests and capture the `$feature_flag_called` side-effect event so they pass the expanded feature-flag suite

## [0.4.1] - 2026-04-30

### Fixed

- Mock `/flags` endpoint now overlays a queue-configured `MockResponse` body on top of its default success body, so tests setting only `featureFlags` / `featureFlagPayloads` no longer have to repeat constants like `errorsWhileComputingFlags: False`. Implemented as a generic `EndpointHandler.default_success_body` hook so other endpoints can opt in.
- `/flags` request payload contract test asserts `token` (the field PostHog SDKs send) instead of `api_key`
- `parallel_adapter` example exposes `/get_feature_flag` so the feature flag suite can run against it

## [0.4.0] - 2026-04-13

### Added

- Feature flag contract test suite for verifying `/flags` request payload structure
  - Validates `token`, `person_properties` (with `$device_id` and auto-injected `distinct_id`), `groups`, `group_properties`, `geoip_disable`, and scoped `flag_keys_to_evaluate`
- `/get_feature_flag` adapter endpoint in CONTRACT.yaml with `force_remote` support
- Mock server `/flags` endpoint for intercepting and asserting feature flag requests
- `assert_flags_request_count` and `assert_flags_request_field` assertion actions

## [0.3.0] - 2026-03-24

### Added

- Capture Analytics V1 contract test suite (69 tests across 11 categories)
  - Endpoint and method validation (2 tests)
  - Required V1 headers: Authorization, PostHog-Api-Token, PostHog-Sdk-Info, PostHog-Attempt, PostHog-Request-Id, PostHog-Client-Timestamp (9 tests)
  - V1 body format: `{created_at, batch}` with no `api_key` or `sent_at` (4 tests)
  - V1 event format: root-level `uuid`, `distinct_id`, `timestamp`, `$set`/`$set_once`/`$groups` support (10 tests)
  - Batch behavior and `created_at` freshness (4 tests)
  - Deduplication and UUID preservation across retries (6 tests)
  - V1 header semantics on retry: attempt increment, request-id preservation, client-timestamp update (5 tests)
  - Retry behavior for V1 status codes (429, 500, 502, 503, 504) with backoff and Retry-After (12 tests)
  - Partial batch handling: 200 response with per-event statuses, pruning persisted/malformed events (10 tests)
  - Compression: per-encoding capability filtering (gzip, deflate, br, zstd) (6 tests)
  - Error handling for non-retryable 4xx (1 test)
- Capability-based test filtering via adapter `/health` `capabilities` field
  - Suite-level: `requires: capture_v1`, `requires: capture_v0`
  - Test-level: `requires: encoding_gzip`, `requires: encoding_zstd`, etc.
- V1 mock server endpoint (`POST /i/v1/e`) returning 204 on success
- Partial batch response templates via `v1_event_statuses` on `MockResponse`
- 23 new assertion action classes (51 total)

### Fixed

- Mock server handler dispatch now uses route-specific handler functions instead of always calling the default handler
- Response routing correctly handles 204 No Content and distinguishes configured responses from handler defaults

## [0.2.0] - 2026-03-23

### Added

- Parallel test execution support with `--concurrency N` flag (default 10)
  - Tests run concurrently when adapter declares `supports_parallel: true` in `/health`
  - Each test gets a unique `test_id` for state isolation via `X-Test-Id` header
  - Mock server partitions recorded requests by `test_id`
  - `ScopedMockServerState` and `ScopedSDKAdapterClient` wrappers for transparent scoping
- `examples/parallel_adapter/` demonstrating per-test-id instance management
- CI job testing the parallel adapter with concurrency=4
- `CONCURRENCY` environment variable support for CLI and docker-compose
- 32 unit tests for partitioned state, scoped wrappers, and parallel runner
- Feature flag `/flags` request payload contract tests (new `feature_flags` test suite)
  - `request_with_person_properties_device_id` - Verifies /flags request includes correct token, distinct_id, person_properties (with auto-added distinct_id), groups, group_properties, geoip_disable, and flag_keys_to_evaluate
- `/flags` endpoint handler in mock server
- `FeatureFlagRequest` type for feature flag evaluation
- `get_feature_flag` adapter interface method and client implementation
- New actions: `get_feature_flag`, `assert_flags_request_count`, `assert_flags_request_field` (with dot-notation for nested fields)
- `force_remote` option for `get_feature_flag` so feature-flag request assertions no longer depend on SDK local-evaluation defaults
- Clarified that `distinct_id` remains a top-level `get_feature_flag` adapter parameter; relaxed `/flags` payload contract test to avoid requiring a top-level `distinct_id`

### Changed

- `bin/test` uses `docker compose up` instead of `docker compose run` (fixes inter-container DNS resolution)
- `docker-compose.yml` supports `ADAPTER_DIR` and `CONCURRENCY` env vars for adapter selection and parallel execution
- `bin/test` and `bin/test-adapter` support `--adapter` and `--concurrency` flags

## [0.1.5] - 2026-01-27

### Added

- 3 new deduplication contract tests (total: 32 tests across 6 categories)
  - `preserves_uuid_and_timestamp_on_retry` - UUID and timestamp unchanged across single-event retries
  - `preserves_uuid_and_timestamp_on_batch_retry` - UUID and timestamp unchanged across batch retries
  - `no_duplicate_events_in_batch` - No duplicate events within a single batch request
- New actions: `assert_timestamp_preserved_on_retry`, `assert_no_duplicate_events_in_batch`

## [0.1.4] - 2026-01-26

### Added

- 9 new capture compliance tests (total: 29 tests across 6 categories)
  - Format validation: `custom_properties_preserved`, `event_has_timestamp`
  - Retry behavior: `retries_on_500`, `retries_on_502`, `retries_on_504`, `max_retries_respected`
  - Batch format: `flush_with_no_events_sends_nothing`, `multiple_events_batched_together`
  - Error handling: `does_not_retry_on_403`

### Fixed

- Removed duplicate `compression` category definition in capture_tests.yaml

## [0.1.3] - 2025-01-21

### Fixed
- Report header now includes SDK name (e.g., `# posthog-js Compliance Report`) to match the extraction pattern in the GitHub Action, fixing SDK-specific PR comment markers

## [0.1.2] - 2025-12-30

### Fixed
- Mock server now normalizes HTTP headers to lowercase for case-insensitive matching
- Fixed gzip decompression for SDKs that send `Content-Encoding: gzip` (capital letters)
- Mock server array parsing for client SDKs (posthog-js, mobile SDKs)
- Test filtering to skip server-only tests when running client SDKs
- PR comments now support multiple SDK adapters without conflicts

### Added
- `--debug` flag for verbose logging
- Proper logging using Python's logging module instead of print statements
- SDK type classification system for client vs server SDKs
  - `--sdk-type` CLI parameter (client/server)
  - Tests can be tagged with `sdk_types: [client]`, `[server]`, or `[client, server]`
  - Test runner automatically filters tests based on SDK type
- Client-specific test assertions
  - `assert_token_present_client` - Checks token in event properties
- Client-specific format validation tests
  - `event_has_required_fields_client` - Checks properties.distinct_id
  - `distinct_id_is_string_client` - Validates distinct_id in properties
  - `token_is_present_client` - Validates token in properties
- Debug logging in mock server state.py showing parsed event structure
- SDK-specific PR comment markers in GitHub Action
  - Each SDK gets its own comment thread (e.g., `<!-- posthog-sdk-compliance-report-posthog-js -->`)
  - Multiple SDKs can post results on same PR without overwriting each other

### Changed
- Mock server now parses array format FIRST (matches Rust capture service)
  - Handles `[{event1}, {event2}]` (client SDKs)
  - Handles `{batch: [...]}` (server SDKs)
  - Handles `{data: [...]}` (fallback gformat)
- `assert_event_property` action now includes debug output showing available properties

## [0.1.1] - 2025-12-30

### Added
- Modular contract structure (split into adapter_actions, test_actions, and test suites)
- 4 new tests: compression, batch format, error handling (413, 408)
- New actions: assert_request_has_header, assert_batch_format
- Total: 17 tests across 6 categories

### Changed
- Split CONTRACT.yaml into modular files in contracts/ directory
- Separated adapter_actions from test_actions for clarity
- Updated documentation to reflect modular structure

## [0.1.0] - 2025-12-30

### Added
- Contract-based SDK test harness
- Mock PostHog server with pluggable endpoints
- SDK adapter interface specification (see CONTRACT.yaml)
- 13 compliance tests for event capture:
  - Format validation (5 tests)
  - Retry behavior (5 tests)
  - Deduplication (3 tests)
- Pluggable action system for test extension
- Docker images published to ghcr.io/posthog/sdk-test-harness
- GitHub Action for CI/CD integration
- Markdown and JSON report generation
- Example minimal adapter implementation
- Documentation (README, ADAPTER_GUIDE, EXTENDING, CONTRACT.yaml)
