---
pypi/posthog-sdk-test-harness: minor
---

Rename `$feature_flag_variant` to `$feature_flag_variant_key` in the feature flag rules v2 event schemas, following PostHog/sdk-specs#203 (contract package 10.0.0).

- `schemas/experiment_exposure_properties.schema.json` requires exactly `$feature_flag`, `$feature_flag_response`, `$feature_flag_variant_key`, `$feature_flag_rule_id` and `locally_evaluated`. `schemas/feature_flag_called_context.schema.json` uses `$feature_flag_variant_key` in every config version 2 branch. Both closed schemas reject the old name.
- For config version 2 the variant key and the value are separate: a split can assign the variant `control` with the value `false`. `$feature_flag_variant` read like the value, which `$feature_flag_response` carries. The new name matches the source field `metadata.variant_key` and `$feature_flag_rule_id`. The value, the presence rules and `$feature_flag_response` do not change.
- `bin/update-feature-flag-rules-v2-presence-schemas.py` now names every event property after its metadata field, with no exception for the variant key.
- The meta-tests check that both schemas reject `$feature_flag_variant`.
- A renamed field is a major contract version. Consumers that vendor either event schema need to copy this release. No released SDK sends the old name.
