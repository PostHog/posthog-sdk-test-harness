---
"posthog-sdk-test-harness": minor
---

- New tests: `unknown_result_treated_as_terminal` (forward compatibility), `respects_retry_after_on_retryable_error`
- `capture` action now accepts an `options` object (`cookieless_mode`, `disable_skew_correction`, `process_person_profile`, `product_tour_id`)
- `init` action now accepts `disable_geoip` and `historical_migration`
- New assertion actions: `assert_event_option`, `assert_body_field`
- New V1 `event_options` category: per-option override tests plus `unset_options_omitted`
- New V1 `geoip_and_historical_migration` category
- Multi-event batch variants for every `event_format` test, plus `batch_envelope_smoke`
