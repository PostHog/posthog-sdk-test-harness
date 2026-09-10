# Feature Flag Rules v2 contract

This package defines the stored configuration contract for Feature Flag Rules v2.
Contract version 1.0.0 covers config version 2.
The contract version is independent of the test harness package version.

The package does not enable config writes or runtime evaluation.
It reserves number and object values for later writer support.

## Terminology and OpenFeature translation

These are PostHog configuration names compatible with OpenFeature's evaluation and provider interfaces.
OpenFeature does not prescribe this configuration schema; person assignment, experiments, rule types, and variant weights are PostHog vocabulary choices.

default_value is the flag default; the caller default is supplied to the SDK accessor.
On a normal no-match result, a configured flag default of false wins over a caller default of true.
A null flag default delegates to the caller default.
On abnormal execution, OpenFeature returns the caller default regardless of the configured flag default.

Each variants[].weight is a percentage of subjects enrolled by the rule, and the weights sum to exactly 100.
The separate rollout_percentage controls enrollment into the rule.

Experiment rules require an integer experiment_id linking an Experiment row.
An experiment_id of null is invalid in this contract.
Non-experiment variant splits are reserved for a possible future extension; their evaluation reason and exposure semantics are not defined here.

rollout_miss means a terminal miss under on_rollout_miss: return_default.
A continuing miss moves evaluation to the next rule and contributes neither a reason code nor metadata from the missed rule (including rule_id and condition_index) to the final result. The final result uses the terminating rule's reason and metadata, or no_rule_match if no rule terminates.
no_rule_match means no rule produced a terminal result, including when targeting matched but rollout missed with continue.
dependency_error covers unknown, cyclic, and otherwise unresolvable dependencies.

The OpenFeature mapping also depends on the provider's evaluation-context binding.
missing_group_key maps to INVALID_CONTEXT when required group membership is missing from additional context fields, even if a person targetingKey is present.
Only a provider that binds the group key to OpenFeature targetingKey uses TARGETING_KEY_MISSING when that field is missing.
Percentage rollout inclusion maps to TARGETING_MATCH by contract choice; OpenFeature also describes pseudorandom assignment with SPLIT and permits custom reasons.

## Package contents

- schemas/config.schema.json contains the JSON Schema 2020-12 config contract.
- registries/literals.json owns shared protocol literals, semantic constraints, and the OpenFeature reason mapping.
- fixtures/config/valid contains configs that the schema must accept.
- fixtures/config/invalid contains configs that the schema must reject.
- manifest.json assigns stable fixture IDs and declares the compatibility policy.
- SHA256SUMS records the SHA-256 digest for each package file except itself.

Objects marked with x-posthog-open-object accept arbitrary keys by design; every other object is closed.
The marker documents this choice for the contract tests; additionalProperties defines how those keys are validated.

JSON Schema enforces all constraints that the standard can express, including the 20-level object-value depth limit.
Depth counts object and array containers, with the returned object at level 1; scalar leaves add no level.
The registry also identifies constraints that need a semantic or parser-level validator, such as unique rule IDs and variant keys, and exact variant weight totals.

Docker releases include contracts/ directly. MANIFEST.in also includes this contract in Python source distributions for source-artifact verification.
It does not install the contract as wheel package data; consumers should pin the repository files or use the source distribution or Docker image.

## Version and integrity policy

A published contract version is immutable.
Publish a new contract version to correct or extend a published contract.
Do not change a published version in place.

SHA256SUMS uses raw file bytes.
It has one lowercase SHA-256 digest, two spaces, a relative POSIX path, and one line feed per entry.
Entries use bytewise path order.

To pin this contract, record the source revision, the contract version, and the SHA-256 digest of SHA256SUMS.
Verify each file against SHA256SUMS before use.

After editing contract files and updating manifest.json, regenerate the checksum index from any working directory:

```sh
python3 <repo>/bin/update-feature-flag-rules-v2-checksums.py
```

Run `python -m pytest tests/test_feature_flag_rules_v2_contract.py` from the repository root to verify the manifest, fixtures, and checksums.
