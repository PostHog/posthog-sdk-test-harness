---
pypi/posthog-sdk-test-harness: minor
---

Bind the capture AI v1 Gherkin steps that need no adapter protocol change.

- `the first received event property "<key>" should be absent`.
- `capture_ai is called with distinct id "<id>" and event "<name>"`, and the same for `capture`.
- `the mock delays every request to "<path>" until released` and `the delayed AI request is released and succeeds`: the v2 mock holds every request to one path until the case releases it, while other paths keep flowing.
- `the analytics batch endpoint should have received "<name>"` and the AI form.
- `the mock serves these ordered AI responses:`. Both ordered response tables now accept the two-column `status | body` form, and a body that keys results by `*` gives every event in the request that result.
