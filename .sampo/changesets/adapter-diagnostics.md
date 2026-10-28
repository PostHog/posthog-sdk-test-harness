---
pypi/posthog-sdk-test-harness: minor
---

Add capability-gated capture v1 tests that need new adapter features.

- `reported_drops`: `/state` lists the drops the SDK reported. Tests: a `drop` result and a dropped duplicate copy are reported with capture's details. Local drops (such as an over-ceiling AI event) are not asserted, because each SDK keeps its own surface for them.
- `generated_payloads`: `/capture_ai` pads properties to an exact byte size. Tests: an AI event under the 8 MiB ceiling is sent and one at 9 MiB is not; three 3 MiB AI events go out in at least two requests under 5 MiB each, in order.
- `log_capture`: `/state` lists the SDK's log lines. Test: an over-ceiling AI event is reported as dropped or named in a log line, and no log line includes its content.
- `property_layers`: `/init` takes `super_properties` and a declarative `before_send`. Tests: caller properties beat registered ones, and `before_send` runs last.
- `fail_open`: `/init` accepts an empty or personal (`phx_`) API key. Tests: such a client sends nothing.
