---
pypi/posthog-sdk-test-harness: patch
---

Correct the feature flag rules v2 wire schemas (contract package 2.3.1): `metadata.variant_key` in the v3 response and `$feature_flag_variant` in the event contexts accept any string, because the PostHog API never restricted version 1 variant keys.
