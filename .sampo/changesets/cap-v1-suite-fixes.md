---
"posthog-sdk-test-harness": patch
---

- Removed `error_response_has_structured_body` and `error_response_has_correct_tag` from `response_format_validation` — these tested mock fidelity, not SDK behavior
- Clarified in `test_actions.yaml` that `assert_v1_error_response_format`, `assert_v1_error_tag`, and `assert_v1_per_event_result` are infrastructure for integration tests, not used in the base SDK compliance suite
- `assert_events_in_batch_count` now accepts a `request_index` param (default `-1`, the most recent request) so partial-batch retry tests can inspect the retried batch instead of the original
