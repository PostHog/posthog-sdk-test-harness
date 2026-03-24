# Changelog

All notable changes to the PostHog SDK Test Harness will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
