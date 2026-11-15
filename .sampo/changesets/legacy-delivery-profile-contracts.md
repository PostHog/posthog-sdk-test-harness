---
pypi/posthog-sdk-test-harness: minor
---

Keep legacy bounded-retry and queued-batching coverage by default while adding positive unbounded-retry recovery and synchronous-delivery profiles. Supply compatible legacy and rich-v2 feature flag fixtures, also readable by v4 old-server compatibility readers.

Require complete event identity across every recovery attempt and successful final delivery, and reject contradictory bounded/unbounded retry capabilities before acceptance. Existing first-two-request assertions remain compatible.
