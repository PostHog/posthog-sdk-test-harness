---
pypi/posthog-sdk-test-harness: minor
---

The v2 Gherkin runner can run the `/flags?v=4` read cases of the YAML `feature_flags_v4` suite. The specs features `remote-flags-v4-legacy-getter.feature` (requires `flags_response_v4`) and `remote-flags-v4-legacy-payload.feature` (also requires `feature_flag_payloads`) show each mock response and expected result inline.

- New steps: `the mock serves this /flags response body:` (sent byte for byte, with no default fields merged in), `get feature flag payload is called with JSON arguments:` (route `/get_feature_flag_payload`), strict `the public flag getter should return exactly JSON <value>`, payload comparisons as parsed JSON values, and "no value"/"no payload" checks that accept native undefined or JSON `null`. Every result check also requires that at least one `/flags` request received the served body, all of them with `v=4`, so a read that never reached the mock cannot pass. The body serves up to 10 requests.
- `bin/update-flags-v4-features.py --specs <checkout>` generates the two features from the feature flag rules v2 contract with `posthog_test_harness.flags_v4`; `--check` fails on drift. The release workflow runs the check on the pinned specs.
- The migrated scope grows from 158 to 308 cases. SDKs without `flags_response_v4` select none of the new cases.
