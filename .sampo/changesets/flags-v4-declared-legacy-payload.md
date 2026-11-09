---
pypi/posthog-sdk-test-harness: patch
---

The `feature_flags_v4` suite and the Gherkin `/flags?v=4` payload feature read a reader case's `legacy_payload` expectation from contract 9.0.0. `readers.v1_failed`, a failed record that still carries a `metadata.payload`, now runs in `legacy_payload_reads_reader_fixture` and in `remote-flags-v4-legacy-payload.feature`, and expects no payload. The payload feature has 75 scenarios.
