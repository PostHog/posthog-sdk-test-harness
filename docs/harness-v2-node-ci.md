# Opt-in Node v2 CI pilot

`.github/workflows/test-sdk-v2-node.yml` is a reusable, strict Node pilot. It is
not selected by existing callers. **GitHub execution is pending clean commits,
artifact publication and an explicitly enabled caller.** Local development images
and dirty snapshots are not published artifacts or commit-only evidence.

## Inputs and trust

Supply all five inputs; there are no floating defaults:

- `sdk-repository`: exact GitHub `owner/repository` whose SDK source is built.
- `sdk-revision`: full lowercase 40-character commit SHA, including for a PR head.
- `harness-revision`: full lowercase commit SHA in
  `PostHog/posthog-sdk-test-harness` containing the helper. Normally use the same
  reviewed commit as the reusable workflow pin.
- `runner-image`: packaged v2 runner reference ending in `@sha256:` plus 64 hex digits.
- `node-image`: compatible Node build image with the same digest-pin requirement.

A tag without a digest, branch, PR ref, short commit or local image ID is rejected
by CI validation. Image digests must resolve for the selected platform. The SDK
checkout HEAD and helper checkout HEAD are checked against their requested pins.
The source builder records all staged working-tree hashes, dirty status and public
package identities; a dirty snapshot is not described as commit-only source.

The helper checkout names its repository explicitly: reusable workflows inherit
caller context, so a default checkout would incorrectly fetch the SDK repository.
Both checkouts disable persisted credentials. The job requests only `contents: read`;
images and sources must be readable without registry login or supplied secrets.
Pins identify inputs, not whether their contents are trustworthy. Review the SDK
build tooling, helper and images before execution on an ephemeral runner.

## Execution and failure evidence

The Python-standard-library helper `scripts/run_v2_node_ci.py build-run` invokes
that SDK checkout's `compliance/node/v2/build-packages.py`. The builder stages source,
rebuilds the current types/core/Node graph, packs fresh public tarballs, installs an
isolated consumer using those tarballs and builds a local adapter image. The pilot
does not select a calibration SDK package version or substitute a registry SDK.

Both complete migrated profiles run sequentially: `node-legacy` with capture mode
`v0`, and `node-analytics-v1` with `v1`. Profile selection remains the runner's job;
unsupported origins are not execution passes. Each adapter and runner has its own
network namespace on an owned internal Docker network, with no published ports.
Private transport and mock bind/advertised addresses are explicit.

Each actual CLI exit is retained in `profiles.json` and `commands.json`. The helper
returns 1 if either profile fails, a report/diagnostic is missing or malformed,
preflight fails, a command times out, or cleanup fails. A timeout before CLI
completion has `cli_exit: null`, not a fabricated CLI outcome. `summary.json` has
`tests_passed: false` on failure. There is no expected-failure whitelist or
nonblocking mode. A report with zero passing executions cannot produce a pass.

The workflow uploads reports, diagnostics, command stdout/stderr, source-build logs
and provenance with `always()`, retaining them for 14 days. It excludes staged
source and installed dependency trees. Cleanup runs in the helper's `finally` and
is retried by an `always()` workflow step using invocation-specific ownership
labels. Partial reports and container logs are collected before removal. The
workflow output is true only if pilot, cleanup and artifact upload all succeed;
continuing to upload artifacts is never an SDK pass. Abrupt runner/daemon loss can
prevent cleanup or artifact upload; it is not successful validation.

## Enabling a caller (not yet active)

After publication, add a separate opted-in job in the SDK repository using a
**literal full commit SHA** in `jobs.<job>.uses`. GitHub does not permit an input
expression to choose that reusable-workflow ref. The following is an inactive
shape illustration, not a deployable caller; replace the angle-bracket values with
reviewed, published identities before installing it under `.github/workflows`:

```yaml
permissions:
  contents: read
jobs:
  node-v2-pilot:
    uses: PostHog/posthog-sdk-test-harness/.github/workflows/test-sdk-v2-node.yml@<published-full-workflow-commit>
    with:
      sdk-repository: PostHog/posthog-js
      sdk-revision: <selected-full-sdk-commit>
      harness-revision: <published-full-helper-commit>
      runner-image: ghcr.io/posthog/<published-v2-image>@sha256:<published-digest>
      node-image: node@sha256:<approved-node-image-digest>
```

Do not point this job at a historical commit that lacks the workflow. Keep v1
selected until the v2 caller and publication gate are explicitly approved. The
existing Node caller uses `test-sdk-action.yml` at
`03d972e49be84402c491324320b0a0f38c2ddc53` with image version `0.10.0`; this pilot
does not alter that selection. Disabling/removing the separate pilot job leaves
that v1 path intact. This is rollback compatibility, not a live rollback deployment.

## Local checks

```sh
.venv/bin/python -m pytest -q tests/test_v2_node_ci.py
python3 scripts/run_v2_node_ci.py replay \
  --runner-image "$(docker image inspect --format '{{.Id}}' "$LOCAL_RUNNER")" \
  --adapter-image "$(docker image inspect --format '{{.Id}}' "$SOURCE_BUILT_ADAPTER")" \
  --out /tmp/fresh-node-v2-replay
```

`replay` is a local evidence entry point using exact local image IDs, not a CI
input bypass exposed by the workflow. It runs the same full-profile orchestration
against an already source-built adapter. Preserve its source-build provenance
alongside the output. No local tag or unpublished image ID is a registry digest.
Local Linux/arm64 validation does not prove the GitHub Ubuntu/amd64 environment;
that pilot remains a separate post-publication gate.

## Validated local implementation

The exact final helper replay retains the established results: legacy 53 passes
and three SDK assertion failures; analytics v1 118 passes and three failures.
Both raw CLI exits and the aggregate helper exit are **1**. Across profiles,
151 unique origins execute: 148 pass and three fail. No infrastructure errors
occurred in that replay. The broken-adapter control also exits 1, retains missing
reports/readiness errors, and records null CLI exits because execution never began.
Owned containers and networks were removed in both runs.

Validation: **123 tests pass** (46 new helper tests and 77 legacy regressions),
plus actionlint and Ruff. An independent rerun passed the 46 new tests and actionlint.
A fresh read-only reviewer found no issues; the independent checks did not repeat
the full Docker replay. Existing Node CI, legacy workflow and release files were
byte-identical to the verified before snapshot; no live rollback was performed.

The [native result ledger](harness-v2-native-results.json) records the final helper
hash, historical source/runtime identities, actual profile exits, report hashes
and known failing/unsupported origins. Raw reports, logs, source-build provenance,
action-pin resolutions and preservation receipts are retained locally, not
included in this repository. These results describe the original dirty snapshots,
not a current-main merge or a GitHub run. No v2 images have been published and the
pilot has not been enabled in a caller.
