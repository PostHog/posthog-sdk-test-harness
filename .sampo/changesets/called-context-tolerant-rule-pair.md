---
pypi/posthog-sdk-test-harness: minor
---

Accept a config version 2 `$feature_flag_called` that drops a broken rule pair, following PostHog/sdk-specs#162 (contract package 9.0.0).

- `$feature_flag_rule_id` and `$feature_flag_rule_type` are sent together or not at all, and only with a rule type the reason code allows. An SDK that reads a record with a missing rule ID, a missing rule type or a disallowed rule type sends neither field, and keeps the config version and reason code.
- `schemas/feature_flag_called_context.schema.json` now accepts a config version 2 call with no rule, variant or experiment attribution for `targeting_match`, `rollout_miss`, `experiment_paused` and `holdout`, as it already did for a malformed `experiment_split`. A half pair and a disallowed rule type, such as `targeting_match` with `experiment`, are still rejected, and complete pairs are unchanged.
- `bin/update-feature-flag-rules-v2-presence-schemas.py` generates these branches, one per reason that names a rule in `rules/response_presence.json`. The malformed-split branch is now generated the same way, with the same shape.
- The meta-tests check a call without attribution for each of those reasons, the complete call for every matrix row, and each half pair and disallowed rule type.
- A failed record reads as disabled, so the legacy payload accessor yields no payload for it, even when the record carries `metadata.payload`. Reader cases gain an optional `legacy_payload` expectation, and `readers.v1_failed`, a failed config version 1 record that still carries a payload, pins it as `null`. A meta-test checks every declared `legacy_payload` and requires one on any failed reader case that carries a payload.
- The set of accepted calls grows, so this is a major contract version. Consumers that vendor the called-context schema need to copy this release.
