# Changelog

All notable changes to the PostHog SDK Test Harness will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
