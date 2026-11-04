---
pypi/posthog-sdk-test-harness: minor
---

Add the `feature_flags_v4` suite, which runs the feature flag rules v2 reader fixtures and value corpus through an SDK by serving mock `/flags?v=4` responses built from the contract package.

- Requires `flags_response_v4`. Every case in `fixtures/wire/readers.json` and every successful case of `corpus/v2_value_evaluation.json` runs as its own test, named `<test>[<case id>]`: `get_feature_flag` returns the legacy rendering of the record value, `false` for a failed or unreadable record, no value for a missing flag, and `variant ?? enabled` for an older server's record. Each test also requires a successful adapter result and a `/flags?v=4` request that read the case's response.
- With `feature_flag_payloads`, the payload accessor returns a config version 2 number or object value as the payload, keeps a config version 1 record's `metadata.payload`, and returns no payload otherwise. A failed record that still carries a payload is not checked, because the contract leaves it open. Payloads compare as parsed JSON values, so key order and number spelling may differ.
- New optional adapter route `POST /get_feature_flag_payload`; see ADAPTER_GUIDE.md.
- Contract tests can set `cases: readers`, `cases: values` or `cases: reader_payloads` to run once per fixture case with `{case}` substituted.
- Mock responses can set `raw_body` to send a body as is, without the endpoint's default fields, so a v4 envelope carries no `featureFlags`.

Adapters without `flags_response_v4` run no new tests, and no existing test changes.
