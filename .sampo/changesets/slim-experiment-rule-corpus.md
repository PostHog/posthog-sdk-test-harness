---
pypi/posthog-sdk-test-harness: minor
---

Slim the feature flag rules v2 experiment rule evaluation corpus (contract package 7.0.0). The corpus drops about 38% of its bytes and loses no expectation: every removed field follows from the fields that remain.

- `corpus/v2_experiment_evaluation.json` gains `defaults` with the flag and context every case shares. A case no longer carries `flag`, and its `context` holds only the fields that differ from the default context.
- Cases no longer carry `legacy`; consumers derive it from `expected.value` with the typed value corpus table.
- `hash_evidence` entries keep `use`, `prefix`, `identifier`, `salt` and `hash01_binary64_hex`. The SHA-1 intermediates move out to `corpus/hash_sha1_60_v1.json`, which already pins them, and the threshold or boundaries follow from the rule.
- Removed cases: `v2_experiment.assignment.fifty_fifty_test`, an exact copy of `v2_experiment.ordering.split`, and seven parser rows that repeat a config fixture: `parser.experiment_id`, `parser.holdout_id`, `parser.missing_paused`, `parser.one_variant`, `parser.twenty_one_variants`, `parser.variant_key_characters` and `parser.variant_value_type`. The corpus has 73 cases.
