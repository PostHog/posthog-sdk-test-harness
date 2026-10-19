---
pypi/posthog-sdk-test-harness: minor
---

Use one version for the feature flag rules v2 contract (contract package 3.0.0). `manifest.json` now holds only `contract` (`name`, `version`, `config_version`) and `artifacts` entries with `path` and `kind`, plus `fixture_id`, `expected` and `expected_failure` on config fixtures. `SHA256SUMS` and its checksum script are removed, as are the per-component versions: `case_ids`, per-artifact `version`, `schema` and `component` in the manifest, `fixture_version` in the wire fixture files, `registry_version` in the literal registry and `version` in the presence matrix. Consumers re-vendoring the contract must stop reading these, take case IDs from the corpus and fixture files, and pin the harness release or commit instead of the checksum index. Schema bytes, schema `$id`s and all fixture and corpus expectations are unchanged.
