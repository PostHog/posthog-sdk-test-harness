---
pypi/posthog-sdk-test-harness: minor
---

`event_size.batches_close_before_byte_target_and_keep_order` is now `event_size.batches_close_before_byte_target` and no longer checks arrival order across requests: an SDK may send batches in parallel. `assert_events_property_values` takes `any_order: true` for this.

The capture AI v1 suite now runs the `reported_drops`, `property_layers` and `fail_open` tests the analytics suite already had, on the AI lane: an AI event capture drops (or a dropped duplicate copy) is reported, a capture_ai property beats a registered one and `before_send` runs last, and an empty or personal API key sends no AI request.
