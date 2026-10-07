---
pypi/posthog-sdk-test-harness: minor
---

Publish feature flag rules v2 contract 3.0.0: experiment rules may set `experiment_id` to null to split variants without an experiment, with a rule-local holdout, an evaluation corpus for these rules, and response presence rows that carry no experiment identity for them. Retire the fixtures this relaxation makes valid; keep every other published component.
