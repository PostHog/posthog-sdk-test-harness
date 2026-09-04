# Feature Flag Rules v2 contract

This package defines the stored configuration contract for Feature Flag Rules v2.
Contract version 1.0.0 covers config version 2.
The contract version is independent of the test harness package version.

The package does not enable config writes or runtime evaluation.
It reserves number values, JSON values, and variant rollout configs for later writer support.

## Package contents

- schemas/config.schema.json contains the JSON Schema 2020-12 config contract.
- registries/literals.json owns shared protocol literals and semantic constraints.
- fixtures/config/valid contains configs that the schema must accept.
- fixtures/config/invalid contains configs that the schema must reject.
- manifest.json assigns stable fixture IDs and declares the compatibility policy.
- SHA256SUMS records the SHA-256 digest for each package file except itself.

JSON Schema enforces all constraints that the standard can express.
The registry identifies constraints that need a semantic or parser-level validator.

## Version and integrity policy

A published contract version is immutable.
Publish a new contract version to correct or extend a published contract.
Do not change a published version in place.

SHA256SUMS uses raw file bytes.
It has one lowercase SHA-256 digest, two spaces, a relative POSIX path, and one line feed per entry.
Entries use bytewise path order.

To pin this contract, record the source revision, the contract version, and the SHA-256 digest of SHA256SUMS.
Verify each file against SHA256SUMS before use.
