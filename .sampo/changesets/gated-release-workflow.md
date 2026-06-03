---
pypi/posthog-sdk-test-harness: minor
---

Switch the release flow to Sampo with a gated `Release` GitHub environment. Docker image publishes now require explicit human approval via Slack before they go out, addressing the supply-chain concern that any merge to `main` could push a new image consumed by every PostHog SDK. Contributors now write `sampo add` changesets instead of editing `pyproject.toml` and `CHANGELOG.md` by hand.
