---
pypi/posthog-sdk-test-harness: minor
---

Remove the feature flag rules v2 wire fixture sets that no consumer runs: `fixtures/wire/calls.json`, `call_transport.json`, `exposures.json`, `exposure_transport.json`, `definitions_feed.json`, `errors.json` and `warnings.json`, together with their `manifest.json` entries and the meta-tests that only checked them. The `definitions.json`, `responses.json` and `readers.json` fixture sets are unchanged. All schemas stay byte-identical and published as specifications, including the `$feature_flag_called` context, experiment exposure, management warning, management error and definitions feed schemas; the meta-tests still check each one as a valid JSON Schema 2020-12 document. Fixture sets are added together with the first consumer that runs them. The removal ships in contract package 3.0.0.
