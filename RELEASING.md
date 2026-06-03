# Releasing

This repository uses [Sampo](https://github.com/bruits/sampo) for versioning and changelog generation, with a GitHub Actions workflow that gates Docker image publishes behind human approval in Slack.

## How to release

### 1. Add a changeset

When making a change that should be released, add a changeset before opening your PR:

```bash
sampo add
```

This prompts you to pick a version bump (`patch`, `minor`, or `major`) and write a short release note. Commit the generated file in `.sampo/changesets/` with your PR.

If you skip this step, the release workflow simply won't fire after merge — there's nothing to release.

### 2. Open and merge the PR

After review, merge the PR to `main`. The release workflow triggers automatically when a push to `main` includes a `.sampo/changesets/*.md` file (matching how `posthog-python` and `posthog-elixir` work — the other Sampo-based SDKs).

The workflow then:

1. Notifies `#approvals-client-libraries` in Slack and pings the client-libraries approvers
2. Waits for explicit approval in the GitHub `Release` environment
3. Once approved: runs `sampo release` to bump `pyproject.toml`, sync `src/posthog_test_harness/__init__.py`, write `CHANGELOG.md`, and commit the result
4. Tags the release commit `X.Y.Z` (bare, matching the other Sampo SDKs) and creates a GitHub Release
5. Builds and pushes the Docker image to `ghcr.io/posthog/sdk-test-harness` with tags `X.Y.Z`, `X.Y`, `latest` (and `X` once we're past v0.x)
6. Notifies Slack on success, failure, or rejection

### Manual trigger

You can manually start the workflow from the Actions tab via `workflow_dispatch`. Manual runs still hit the approval gate.

## Version bumping

Sampo derives the next version from the committed changeset files:

- **patch** — bug fixes, documentation, internal refactors
- **minor** — new tests, new actions, backwards-compatible additions
- **major** — breaking changes to `CONTRACT.yaml` or the adapter interface

## Why Sampo, not Changesets?

Other PostHog SDKs (`posthog-android`, `posthog-go`, `posthog-js`) use [Changesets](https://github.com/changesets/changesets). We picked [Sampo](https://github.com/bruits/sampo) instead because:

- **Native Python support.** Sampo reads and writes `pyproject.toml` directly. Changesets is an npm tool that would require us to keep a stub `package.json` + `pnpm` in this repo purely to host the CLI — the same workaround `posthog-go` uses today.
- **PostHog is migrating to Sampo.** `posthog-python` already runs on Sampo, and the SDK release handbook notes a gradual migration away from Changesets.
- **Single source of truth.** With Sampo, `pyproject.toml` is the only version file the tool touches; the workflow syncs `src/posthog_test_harness/__init__.py` from it. With Changesets we'd be juggling `package.json` (for the Changeset CLI's own bookkeeping) and `pyproject.toml` (for the actual package version).

If you're used to Changesets, the day-to-day mechanics are nearly identical: `sampo add` is the analog of `pnpm changeset`, the resulting files live in `.sampo/changesets/`, and the release workflow runs `sampo release` instead of `pnpm changeset version`.

## Security: why the approval gate exists

The Docker image this repo publishes (`ghcr.io/posthog/sdk-test-harness:latest`) is consumed by every PostHog SDK's compliance test workflow. Without a gate, a single approving review on a `main` merge could push a tampered image that every SDK would pull on its next CI run — a supply-chain attack vector flagged by the security team.

The `Release` GitHub environment requires explicit approval from a maintainer before any Docker tag (`latest`, `X.Y.Z`, etc.) is moved. The previous flow also pushed `latest` and `main-<sha>` on every merge to `main`; both have been removed. Docker tags only move via this gated release path.

## Troubleshooting

### Workflow didn't fire after merge

The push-to-main trigger is path-filtered on `.sampo/changesets/*.md`. If your merged PR didn't include a new changeset, the workflow never fires. Add a changeset in a follow-up PR.

### Approval timed out

GitHub's environment approval has a 30-day deadline. If it expires, re-run the workflow from the Actions tab.

### Release approved but Docker push failed

The release commit and tag still landed — the workflow's later steps just fell over. Inspect the run, fix the issue (often a transient registry error), and re-run the failed jobs from the Actions UI. Don't add a new changeset for a retry.
