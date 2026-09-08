---
pypi/posthog-sdk-test-harness: patch
---

Fix the AI capture routing test to expect the normal capture endpoint for the adapter's advertised capture protocol, instead of requiring `/batch` for capture-v1 adapters.
