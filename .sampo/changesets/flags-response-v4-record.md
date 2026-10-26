---
pypi/posthog-sdk-test-harness: minor
---

Rename the feature flag rules v2 `/flags` response record from v3 to v4 (contract package 7.0.0). Clients already send `v=3` and arbitrary `v` values to `/flags`, so the service selects the typed record only for an exact `v=4`, and the contract now names the record after that request version. The record's shape and every expectation are unchanged.

- Schemas: `schemas/flags_response_v3.schema.json` is renamed to `schemas/flags_response_v4.schema.json`, with `$id` `https://posthog.com/contracts/feature_flag_rules_v2/flags_response_v4.schema.json` and title `POST /flags?v=4 response (producer contract)`. `schemas/flags_response_v3_presence.schema.json` is renamed to `schemas/flags_response_v4_presence.schema.json`, with the matching `$id`, title and `$ref`. These `$id` changes are a deliberate exception to the stable-`$id` rule, made in this major version.
- Fixtures: `fixtures/wire/responses.json` and `fixtures/wire/readers.json` point at the renamed producer schema. The reader cases `readers.v3_value_takes_precedence` and `readers.v1_at_v3` are renamed to `readers.v4_value_takes_precedence` and `readers.v1_at_v4`, with the same expectations.
- Manifest and README follow the renames.
