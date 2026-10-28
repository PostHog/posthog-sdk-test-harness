---
pypi/posthog-sdk-test-harness: patch
---

`event_size.batches_close_before_byte_target_and_keep_order` is now `event_size.batches_close_before_byte_target` and no longer checks arrival order across requests: an SDK may send batches in parallel. `assert_events_property_values` takes `any_order: true` for this.
