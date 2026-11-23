---
pypi/posthog-sdk-test-harness: minor
---

Accept a `$feature_flag_called` whose `$feature_flag_response` is absent or `null` when the read has no value, following the sdk-specs `feature-flag-called-tracker` requirements "A missing flag reports no value" and "Failed direct getter evaluations report evaluation_failed" (contract package 11.0.0).

- `schemas/feature_flag_called_context.schema.json` no longer requires `$feature_flag_response` on every call. A call without config version 2 may omit it or send `null`, because a missing flag carries no config version.
- A config version 2 call may omit it or send `null` only when `$feature_flag_error` lists `evaluation_failed`: a failed record with no successful fallback. A successful config version 2 call must still carry its value. Config version 2 attribution rules do not change.
- The closed projection now declares `$feature_flag_error` as a string, so the schema can check for `evaluation_failed`. Consumers that build the projection from a fixed list of property names need to add it.
- A response value is still a boolean or a non-empty string. `schemas/experiment_exposure_properties.schema.json` does not change and never accepts `null`.
- The meta-tests accept a missing flag and a failed config version 2 read with an absent or a null response. For config version 2 calls that do not report `evaluation_failed`, they reject both: every presence-matrix row, every call without attribution, and a failed record that reports another error.
- The set of accepted calls grows, so this is a major contract version. Consumers that vendor the called-context schema need to copy this release, and can then drop any local relaxation of `$feature_flag_response`.
