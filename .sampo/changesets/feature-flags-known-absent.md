---
pypi/posthog-sdk-test-harness: minor
---

Add the opt-in `feature_flags_known_absent` suite for adapters that declare both `feature_flags_local_evaluation_v1` and `feature_flags_known_absent_v1`. `/get_feature_flag` gains optional `person_property_keys_known_absent` and `group_property_keys_known_absent` params, sent only when a test sets them. The suite checks local results for listed keys (`is_not_set`, `is_set`, a listed key overriding a supplied value, and an operator table) with no `/flags` request, falls back to `/flags` for a group key listed under another group type and for an unlisted omitted key, and checks the `/flags` fallback body carries the listed keys, or neither field when none are listed. Adds the `assert_flags_request_field_absent` assertion.
