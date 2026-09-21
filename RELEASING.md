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
3. Once approved: runs `sampo release` to bump `pyproject.toml` and write `CHANGELOG.md`, syncs `src/posthog_test_harness/__init__.py` and `uv.lock`, and commits the result
4. Tags the release commit `X.Y.Z` (bare, matching the other Sampo SDKs) and creates a GitHub Release
5. Builds a clean v2 wheel/sdist with pinned specs, smoke-tests the installed wheel and local v2 image, then publishes both images for `linux/amd64` and `linux/arm64`:
   - `ghcr.io/posthog/sdk-test-harness` — existing v1 entrypoint and consumers
   - `ghcr.io/posthog/sdk-test-harness-v2` — `posthog-test-harness-v2` entrypoint with bundled specs

   Both use the same Sampo version and tags: `X.Y.Z`, `X.Y`, `latest`, and `X` once we're past v0.x. The `-v2` image name identifies the runner, not a separate version series.
6. Records both image digests and distribution/smoke artifacts for consumer handoff, then notifies Slack on success, failure, or rejection

### Manual trigger

You can manually start the workflow from the Actions tab via `workflow_dispatch`. Manual runs still hit the approval gate.

## V2 release inputs and handoff

`.github/workflows/release.yml` tracks `SDK_SPECS_COMMIT`, currently
`8583749b4a634ec5881fa6af20057f39c89b0c64` from `PostHog/sdk-specs` (PR67).
Update this immutable commit pin through review, not a moving branch reference.
The workflow checks out specs and writes distribution/smoke outputs under
`RUNNER_TEMP`, outside the harness checkout. `scripts/build_v2_distribution.py`
requires clean harness/specs commits and uses `uv export --locked`; the release
commit must therefore include the Sampo version's updated `uv.lock`.

Before either image is pushed, the installed-wheel smoke runs a controlled healthy
host and a deliberately defective host (not SDK conformance), and the local amd64
v2 image verifies its bundle and migration-suite discovery readiness. Both images
are then built/pushed for amd64 and arm64; arm64 is not runtime-smoked.

The Actions job summary and `harness-release-X.Y.Z-<attempt>` artifact contain
`release-images.json`: version, release/specs commits, each publish outcome, and
both multi-platform image digests and digest-pinned pull references. The artifact
also includes the wheel, sdist, hashed requirements, `distribution.json` (source
provenance and artifact checksums), installed-wheel smoke reports/logs, and image
bundle/discovery reports. Consumers can opt into the separate v2 image using its
recorded `ghcr.io/posthog/sdk-test-harness-v2@sha256:...` reference. Existing v1
callers are unchanged. Download the handoff before the repository's Actions
artifact retention period expires; it is not attached to the GitHub Release.

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

The release commit, tag, and GitHub Release are created before distribution checks
and Docker publishing. Their presence does not prove either image was published.
The pushes are sequential (v1, then v2), not transactional: v1 can succeed while v2
fails, and even a failed push can leave some tags moved. Nothing rolls back an
already-published tag. Inspect the job summary, handoff artifact, build logs, and
registry digests for **both** images before handing the release to consumers. A
missing recorded digest means no confirmed output, not proof the registry was
untouched.

Do not assume **Re-run failed jobs** resumes the push: it restarts the whole
release job, checks out current `main`, and invokes Sampo again after the original
changesets may already have been consumed. A fresh manual run also checks for
changesets and can skip the release entirely. This workflow does not implement a
publish-only recovery mode. Coordinate an explicitly approved recovery using the
original release commit, specs pin, and verified distribution artifacts; verify
both image/tag sets afterward. Do not add a new changeset merely to retry a push.
