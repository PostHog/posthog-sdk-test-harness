---
pypi/posthog-sdk-test-harness: minor
---

Add capture v1 cases for both lanes: a same-origin 307 is followed with the same events, a cross-origin 307 and a same-origin 302 are terminal with no retry, and at most 5 redirects are followed for one request. Each event carries `$lib` and `$lib_version`. On the analytics lane, three 2 MiB events go out in at least two requests under 5 MiB each. `/capture` now documents `pad_properties_to_bytes` and `marker` under `generated_payloads`, as `/capture_ai` does.
