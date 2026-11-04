---
pypi/posthog-sdk-test-harness: minor
---

The v2 Gherkin runner reads `@excludes:<capability>` tags. It does not select a case when the adapter profile declares any capability that the case excludes. An excluded case that is selected explicitly reports `sdk_capability_excluded`. This lets `flags_response_v4` replace the `?v=2` query check on `/flags` with a `?v=4` check in the Gherkin suite, as `excludes` already does in the YAML suite.
