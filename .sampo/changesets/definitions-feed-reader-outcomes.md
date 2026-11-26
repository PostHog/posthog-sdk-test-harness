---
pypi/posthog-sdk-test-harness: minor
---

Pin two more local reader outcomes in the feature flag rules v2 definitions feed fixture (contract package 10.2.0). `fixtures/wire/definitions_feed.json` gains two entries. One is an inactive version 2 flag whose document has an unknown rule type: a reader disables it without reading the document, with no value and the reason `flag_disabled`, in auto and local-only mode. The other is an active version 2 flag with an entry field outside `filters` that the reader does not know, which the reader ignores. Both entries fail the producer schema, because the flags service never sends them. No existing entry, case, expectation or schema changes.
