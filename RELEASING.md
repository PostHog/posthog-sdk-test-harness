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
3. Once approved: checks that the generated `/flags?v=4` features in the pinned specs match the contract (before anything is committed), then runs `sampo release` to bump `pyproject.toml` and write `CHANGELOG.md`, syncs `src/posthog_test_harness/__init__.py` and `uv.lock`, and commits the result
4. Builds a clean v2 wheel/sdist with pinned specs and smoke-tests the installed wheel plus local amd64 and arm64 v2 images. All checks must pass before creating the version tag or GitHub Release.
5. Tags the release commit `X.Y.Z` (bare, matching the other Sampo SDKs), creates a GitHub Release, then publishes both images for `linux/amd64` and `linux/arm64`:
   - `ghcr.io/posthog/sdk-test-harness` — existing v1 entrypoint and consumers
   - `ghcr.io/posthog/sdk-test-harness-v2` — `posthog-test-harness-v2` entrypoint with bundled specs

   Both use the same Sampo version and tags: `X.Y.Z`, `X.Y`, `latest`, and `X` once we're past v0.x. The `-v2` image name identifies the runner, not a separate version series.
6. Records both image digests and distribution/smoke artifacts for consumer handoff, then notifies Slack on success or rejection (the failure notification workflow below covers failures)

### Manual trigger

You can manually start the workflow from the Actions tab via `workflow_dispatch`. Manual runs still hit the approval gate.

## V2 release inputs and handoff

`.github/workflows/release.yml` tracks `SDK_SPECS_COMMIT`, currently
`dd11a6b8f8c7be3013cd26d97782452ce0f8a76d` from `PostHog/sdk-specs` (PR174),
including the merged group-identify, capture-exception, evaluate-flags and generated `/flags?v=4` read coverage.
Update this immutable commit pin through review, not a moving branch reference.
Pull request CI runs the generated `/flags?v=4` features check against the same pin,
so a contract change that needs regenerated features fails before merge
(see [Remote `/flags?v=4` reads](./docs/harness-v2.md#remote-flagsv4-reads)).
The workflow checks out specs and writes distribution/smoke outputs under
`RUNNER_TEMP`, outside the harness checkout. `scripts/build_v2_distribution.py`
requires clean harness/specs commits and uses `uv export --locked`; the release
commit must therefore include the Sampo version's updated `uv.lock`.

Before release metadata or either image is published, the installed-wheel smoke
runs a controlled healthy host and a deliberately defective host (not SDK
conformance). Separate amd64 and arm64 v2 images verify their bundles and
discovery readiness for unified SDK acceptance and the migrated-case compatibility filter; arm64
runs under QEMU on the amd64 release runner. Each architecture retains its own smoke reports. Both images are then
built/pushed for amd64 and arm64.

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

The Sampo release commit consumes changesets before validation. A validation
failure prevents tag, GitHub Release and image publication, but does not undo that
commit. The tag and GitHub Release are created only after the validation checks
pass; their presence still does not prove either image was published.
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

### Release failed after the version commit, before any tag

If validation fails after `chore: Release vX.Y.Z` lands but before the tag, nothing
was published. Re-running cannot finish it: Sampo finds no changesets. To release
the same version, fix the cause in a PR that also reverts the release commit. The
revert restores the consumed changesets and the previous version. When that PR
merges, the workflow runs as usual and Sampo releases `X.Y.Z` again.

### Failed or cancelled release

The independent [release failure notification workflow](.github/workflows/release-failure-notification.yml) checks every completed main-branch release attempt and alerts if a job failed, timed out, or was cancelled. This includes runner startup failures and cancellations with job records. It also alerts when the run failed but the `Release and publish` job never appears in the job list, for example when GitHub Actions stops mid-run. Once both Docker image pushes have succeeded, alerts describe follow-up problems rather than claiming the release failed; a failed image push is always reported as a release failure, and the workflow still alerts if the released Slack notification was skipped.

Cancelled runs with no jobs are ignored, including superseded queued runs and manual cancellations before any job is created. A `Release and publish` failure before any steps ran is also ignored when the rejection-reply step confirms an explicit rejection in the same attempt and no other job failed.

Job inspection retries transient GitHub API failures up to three times. If inspection still fails, it logs the HTTP status or error type and message, then posts a fallback alert with the overall run status and logs link so the release can be checked manually. Error diagnostics stay in the run log, not in the Slack message.

Alerts reply to the approval thread in the release approvals Slack channel. The release workflow preserves the thread timestamp in a run artifact for 30 days, independently of release approval. Failed-job reruns reuse the latest available thread from the same or an earlier attempt. If the timestamp is missing, invalid, or unavailable, the alert falls back to a new channel message, so it does not depend on the approval notification succeeding. The completion workflow must be present on the default branch to run. It only acts on runs that a push or a manual dispatch started in this repository.
