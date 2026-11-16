---
pypi/posthog-sdk-test-harness: minor
---

The Gherkin `/flags?v=4` legacy getter feature accepts `false` or no value for a record with `failed: true`, as the YAML `feature_flags_v4` suite does since 1.31.1 (PostHog/sdk-specs#211).

- New step `the public flag getter should return false or no value`. It accepts JSON `false` (strict: not `0` or `"false"`), the native undefined outcome or JSON `null`. It fails with `invalid_step_data` when the served record of the requested flag does not have a boolean `failed: true`.
- `bin/update-flags-v4-features.py` uses the step for the four reader cases with `failed: true` (`readers.dependency_error`, `readers.error`, `readers.error_with_value`, `readers.v1_failed`) and says so in the feature description. The generator and the YAML `assert_legacy_flag_value_for_case` action use one predicate, `posthog_test_harness.flags_v4.failed_record`. Other reader errors keep `exactly JSON false`. The payload feature does not change, because a failed record already has no payload.
- `SDK_SPECS_COMMIT` moves to the sdk-specs commit with the regenerated feature, so the v2 image bundles it. An SDK that returns `false` for a failed record still passes.
