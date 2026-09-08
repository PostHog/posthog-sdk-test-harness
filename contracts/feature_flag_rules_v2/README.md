# Feature Flag Rules v2 contract

This package defines the stored configuration contract for Feature Flag Rules v2 and the canonical evaluation corpus that consumers pin.
Contract version 1.1.0 covers config version 2 and corpus version 1.0.0.
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

rollout_miss means a terminal miss under on_rollout_miss: return_default.
A continuing miss contributes no context to the final result.
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
- corpus/hash_sha1_60_v1.json contains exact sha1_60_v1 vectors, white-box threshold and variant boundary vectors, and v1-to-v2 seed parity inputs.
- corpus/v1_evaluation.json contains lock-down fixtures for the frozen version 1 evaluation arm.
- corpus/legacy_projection.json records how a version 1 outcome projects into each response protocol version.
- schemas/hash_sha1_60_v1.schema.json, schemas/v1_evaluation.schema.json, and schemas/legacy_projection.schema.json are the companion schemas for the corpus files.
- manifest.json assigns stable fixture and case IDs and declares the compatibility policy.
- SHA256SUMS records the SHA-256 digest for each package file except itself.

JSON Schema enforces all constraints that the standard can express.
The registry identifies constraints that need a semantic or parser-level validator.

## Component versions

Each artifact carries its own component version in manifest.json.
The config schema and literal registry stay at 1.0.0 because contract 1.1.0 does not change accepted configs or frozen literals.
The corpus files and their companion schemas are corpus version 1.0.0.

## Corpus rules

Every corpus case has a stable ID that manifest.json declares under its file.
Adding, removing, or renaming a case requires a manifest change.

Expected values are hand-derived from the algorithm definition and the frozen version 1 evaluator.
Hash expectations were calculated with an independent SHA-1 implementation, never generated from a production evaluator.
The meta-tests recompute every hash vector, so an expectation cannot change silently.

A published corpus version is immutable.
Changing any expected value, including a hash vector, a version 1 outcome, or a projection cell, requires a new corpus version and a review explanation of why the previous expectation was wrong or superseded.
Additive cases may join a new minor corpus version; a changed expectation is a major corpus change.

Hash arithmetic is defined in corpus/hash_sha1_60_v1.json.
The contract value of hash01 converts both the 60-bit integer and the scale to binary64 before one division.
Thresholds are rollout_percentage / 100 computed in binary64, and variant boundaries accumulate left to right in binary64 in stored order.

## Version and integrity policy

A published contract version is immutable.
Publish a new contract version to correct or extend a published contract.
Do not change a published version in place.

SHA256SUMS uses raw file bytes.
It has one lowercase SHA-256 digest, two spaces, a relative POSIX path, and one line feed per entry.
Entries use bytewise path order.

To pin this contract, record the source revision, the contract version, the corpus version, and the SHA-256 digest of SHA256SUMS.
Verify each file against SHA256SUMS before use.
