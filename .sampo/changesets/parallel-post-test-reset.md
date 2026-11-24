---
pypi/posthog-sdk-test-harness: patch
---

In parallel mode, send a scoped `/reset?test_id=...` after each test finishes, including failed tests, so adapters can release per-test SDK clients instead of holding them until the container exits. The cleanup is best-effort and never changes the test result.
