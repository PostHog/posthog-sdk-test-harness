# Changelog

All notable changes to the PostHog SDK Test Harness will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
