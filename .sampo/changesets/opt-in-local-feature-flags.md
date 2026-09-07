---
pypi/posthog-sdk-test-harness: minor
---

Add opt-in `feature_flags_local_evaluation_v1` adapter controls and capability-gated tests for legacy and version-2 boolean matching, including version-only reloads, groups, and recursive cohorts. Serve isolated authenticated definitions GET mocks without affecting capture/remote accounting. Existing adapters and remote fixtures remain unchanged unless explicitly opted in.
