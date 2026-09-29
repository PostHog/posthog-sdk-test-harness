---
pypi/posthog-sdk-test-harness: minor
---

Bring the `capture_ai_v1` suite to parity with `capture_v1`: it now runs the same header, body, event, batching, retry, partial-batch, response, compression, options and geoip tests on `POST /i/v1/ai/events`. Adds a `capture_ai_multiple` test action.
