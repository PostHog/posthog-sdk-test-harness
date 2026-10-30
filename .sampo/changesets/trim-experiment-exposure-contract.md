---
pypi/posthog-sdk-test-harness: minor
---

Trim the feature flag rules v2 SDK experiment exposure contract and drop the reason description from config version 2 flag calls, following PostHog/sdk-specs#162 (contract package 8.0.0).

- `schemas/experiment_exposure_properties.schema.json` requires exactly `$feature_flag`, `$feature_flag_response`, `$feature_flag_variant`, `$feature_flag_rule_id` and `locally_evaluated`. `$feature_flag_config_version`, `$feature_flag_rule_type`, `$feature_flag_reason_code` and `$experiment_exposure_source` are removed, so the closed schema rejects an exposure that carries them. An SDK sends the exposure only for a config version 2 experiment split, so their values were constants; the rule ID identifies the experiment rule and marks the exposure as SDK-sent. `$feature_flag_forced_variant` stays rejected.
- `schemas/feature_flag_called_context.schema.json` declares `$feature_flag_reason` and rejects it on a config version 2 call, which carries `$feature_flag_reason_code` instead. Config version 1 calls keep `$feature_flag_reason` with any value.
- The meta-tests check both schemas against projections they build: an exposure without each required property, an exposure with each forbidden property, and a config version 2 call with `$feature_flag_reason` for every presence-matrix row. No fixture set is added.
- Consumers that vendor the exposure schema need to copy this release.
