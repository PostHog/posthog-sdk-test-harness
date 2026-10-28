---
pypi/posthog-sdk-test-harness: minor
---

Add the `flags_response_v4` adapter capability. An SDK that declares it must send `?v=4` on remote `/flags` requests, which asks the server for the v4 flag record. An SDK that does not declare it is still held to `?v=2`. Contract tests can now list `excludes` to skip a test when the adapter declares any of the named capabilities.
