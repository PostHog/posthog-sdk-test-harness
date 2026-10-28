---
pypi/posthog-sdk-test-harness: minor
---

Bring the `capture_v1` and `capture_ai_v1` suites in line with capture v1 as it ships today.

- New retry tests: SDKs retry 404, 429 and 502 like 408 and the other 5xx statuses.
- `implements_backoff` measures the SDK's own waits (100, 200 and 400 ms, jitter down to 0.75x) on a 502 that carries no `Retry-After`.
- The mock answers a repeated uuid the way capture does: it keeps the first copy's result and adds `duplicates_dropped`. A new test checks that only the first copy is resent when capture asks for a retry.
- Partial-batch tests use capture's `warning` result instead of `limited`, which capture never sends. The mock still accepts `limited` for older feature files.
- New tests: a caller uuid is sent in lowercase, `capture_ai` keeps a non-`$ai_` event on the AI endpoint, and a failing AI lane does not delay analytics events.
- Mock responses can target one path, and request-count, batch-count and final-success assertions can filter by path.
- New adapter capabilities gate tests some SDKs cannot run: `event_uuid_override` (caller uuids, now also forwarded on `/capture`), `bounded_retries` (`max_retries_respected`), `disable_geoip` and `historical_migration`. Adapters that support these must declare them to keep running those tests.
