---
pypi/posthog-sdk-test-harness: minor
---

Add the feature flag rules v2 experiment rule evaluation corpus and drop `experiment_id` from v2 experiment rules (contract package 6.0.0). An experiment links to its rule from the Experiment row, so the rule carries no experiment reference, and every variant assignment by an experiment rule reports `experiment_split`, whether or not an experiment links to the rule.

- Config schema: `experiment_id` is no longer a rule field, so a rule that carries one is rejected. `holdout.id` is an integer or null. `config.invalid.null_experiment_id` is removed; `config.valid.experiment_rule_local_holdout` and `config.invalid.experiment_rule_with_experiment_id` are added; the other experiment rule fixtures drop `experiment_id` and keep their expectations.
- Corpus: `corpus/v2_experiment_evaluation.json` (80 cases in the `ordering`, `assignment`, `values`, `white_box` and `parser` families) and its companion schema.
- Wire: the producer schema forbids `experiment_id` on v2 records and no longer requires it on a split, the presence rows for experiment rules forbid it, and `$experiment_exposure` has no `$feature_flag_experiment_id`. Version 1 records keep `experiment_id`. The wire templates drop it from v2 records. Removed cases: `responses.split_wrong_type_experiment_id`, `responses.experiment_split_contradicted_has_experiment`, `responses.rollout_miss_percentage_rollout_wrong_rule_type`, `responses.rollout_miss_experiment_wrong_rule_type` and `readers.partial_split_missing_experiment_id`. Added cases: `responses.experiment_split_flag_without_experiment`, `readers.split_extra_experiment_id` and `definitions.v2_rule_local_holdout`. `responses.json` now has 26 valid and 45 invalid cases.
- Registry: `experiment_id` leaves `fields` and `openfeature_mapping.flag_metadata_keys`.
