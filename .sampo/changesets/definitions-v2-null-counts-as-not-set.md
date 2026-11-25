---
pypi/posthog-sdk-test-harness: minor
---

Allow the team setting `null_counts_as_not_set` in the feature flag rules v2 definitions feed (contract package 10.1.0).

- PostHog/posthog#141619 added the boolean `null_counts_as_not_set` to the legacy definitions response. The `?v=2` feed is that response plus the config version 2 flags, so it carries the field too.
- `schemas/definitions_v2.schema.json` accepts an optional boolean `null_counts_as_not_set`. The top level stays closed, so any other field is still rejected.
- An added optional field is a minor contract version. Consumers that vendor the feed schema need to copy this release before they validate a feed from a PostHog build with #141619.
