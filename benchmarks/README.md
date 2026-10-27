# SDK benchmark reporting

SDK repositories own workload execution and instrumentation. This component reads
bounded JSON results from a paired baseline/head run; it never builds an SDK,
executes artifact code, or interprets runtime-specific profiles.

## Render locally

Use the repository's locked Python 3.12.12 / uv 0.13.4 toolchain:

```sh
uv sync --frozen --no-dev --no-install-project
uv run --no-sync python -I benchmarks/report/cli.py \
  --base-results results/base.json --head-results results/head.json \
  --output-directory benchmark-report
```

This writes `report.md` and `comparison.json`. If set, `GITHUB_STEP_SUMMARY` receives
the Markdown and `GITHUB_OUTPUT` receives absolute `report-path` and
`comparison-path` values. Render mode uses no token and makes no API calls.

Exit status is zero for valid performance comparisons, including slower results,
incompatible configurations, unsupported metrics and missing baselines. Invalid
JSON/schema/identities and workload/metric/raw-run `error`, `timeout` or `oom`
produce a nonzero status. Execution failures still produce a report; partial
samples cannot appear as an improvement. Input and API failures produce an
explicit failure summary, without echoing raw input or logs.

Representative sanitized Node and Python pairs live in
`tests/fixtures/benchmarks/`. They retain two repetitions of single-flag and
refresh workloads, including allocation measurements and memory checkpoints.
SDK paths, paired-run identity and commit SHAs are synthetic. A separate Node
headline fixture retains all workload families with two metric samples and no
raw runs, to test summary selection. Full producer artifacts should remain
attached to the producing workflow run.

## Result contract v1

[`schema/result.schema.json`](schema/result.schema.json) is the machine-readable
schema. `report/validate.py` adds semantic and resource bounds. Both must pass:

- Maximum 8 MiB per document, depth 20, 250,000 JSON nodes, 1,024 fields per
  object, 2,000 items per generic array, 4,096 characters per generic string.
  Raw stdout/stderr are capped at 65,536 characters and never rendered.
- At most 128 workloads, 64 metrics/workload, 100 repetitions and samples/metric,
  400 raw runs/workload and 1,000 checkpoints/run. IDs are at most 256 characters.
- Full lowercase 40-character commit SHAs and 64-character SHA-256 digests.
  Duplicate JSON keys, workload IDs, metric IDs, sample repetition IDs and raw
  `(pass, repetition)` IDs are invalid. Repetitions must be in the configured
  range; checkpoint operation positions must be unique and increasing.
- Finite, nonnegative measurements, bounded at `1e100`, and positive integer
  completed operation counts. Signed exit codes are termination evidence, not
  measurements. Checkpoints cannot exceed the run's completed operations.
- Statuses: `ok`, `error`, `timeout`, `oom`, `unsupported`. Successful metrics
  require samples, method, direction (`lower`/`higher`) and quality
  (`measured`/`estimated`); unavailable metrics require an explanation.
- Units: `ns/op`, `bytes/op`, `count/op`, `bytes`, `ns`, `count`, `ops/s`.
  Allocation estimates remain labeled as estimates. Memory deltas are not
  substitutes for allocation volume.

Suite and execution objects allow bounded extra configuration so producers can
record instrumentation policy without runtime-specific reporter branches. Raw
run objects also allow runtime-native measurement and correctness evidence.
Additional suite/execution fields participate in exact compatibility matching.

### Comparison semantics

Both sides must match SDK identity, the entire suite (version, benchmark source
commit and digest, profile, repetitions and measurement policy), and the entire
execution environment, including pair ID, runtime versions, flags, OS/kernel,
architecture, CPU, runner/image, limits and measurement-tool versions. SDK build
fields `dependency_lock_digest`, `build_digest`, `core_build`, `entry`,
`sdk_version` and `packages` may differ: those describe the code under test.
Profiler versions belong in `measurement_tools` (or exact execution metadata),
not solely in the dependency package inventory.

Each workload additionally requires identical parameters, fixture digest, raw
run identities, operations, warmup operations and checkpoint positions. Each
metric requires identical unit, direction, quality, method, scope/configuration,
paired repetition IDs and operation counts. Missing or incompatible metrics are
explicit rows, never zeros. Zero baselines have no percentage change.

The report shows medians, percentage change in the median, and descriptive
paired absolute-delta median/min/max. JSON retains individual paired percentage
changes, original metric samples, parameters, checkpoints and both contexts.
All numeric comparisons are **inconclusive**: these ranges are not calibrated
confidence intervals. There is no universal threshold or aggregate score.
Incomplete repetitions are labeled. Execution failures sort first. The headline
is capped at 12 rows, preferring representative single-flag timing, all-flags
scaling timing/allocation bytes, load/refresh memory, and capture/drain cases.
Selection uses shared workload parameters and metric semantics, not runtime
profiler logic. Unsupported measurements remain in the collapsible details
rather than crowding out numerical headline rows. Markdown is bounded below 60,000 bytes;
`comparison.json` retains omitted rows. Artifact text is escaped, URLs/mentions
are neutralized, and raw stderr is not included in reports.

## Composite action

Action: `.github/actions/benchmark-report/action.yml`. Pin consumers to a reviewed,
published immutable harness commit. Inputs:

| Input | Default | Use |
| --- | --- | --- |
| `mode` | `render` | `render` or `publish` |
| `base-results`, `head-results` | empty | Required local JSON paths in render mode |
| `output-directory` | `benchmark-report` | Markdown and comparison destination |
| `workflow-path` | empty | Trusted producer workflow literal, e.g. `.github/workflows/node-benchmarks.yml` |
| `artifact-name` | empty | Trusted exact producer artifact name |
| `report-key` | empty | Trusted stable comment namespace, e.g. `node-performance` |
| `sdk` | empty | Trusted SDK identity matching both documents, e.g. `posthog-node` |

The last four inputs are required for publishing and must come from trusted
workflow configuration, not artifact data or PR-controlled expressions. Report
keys are independent of SDK identities. Outputs are `report-path` and
`comparison-path`. Upload them with an `always()` step to retain execution-failure
reports. The action uses SHA-pinned setup-uv with caching disabled and locked
harness dependencies; it does not install the harness project or use actions/cache.

## Trusted workflow_run publisher

Publish mode requires `GH_TOKEN` in the environment (there is no token input),
`GITHUB_REPOSITORY`, `GITHUB_EVENT_NAME=workflow_run`, and `GITHUB_EVENT_PATH`.
Give the trusted job only `contents: read`, `actions: read`, and
`pull-requests: write`. Scope `GH_TOKEN` to this action. Never check out or execute
PR code in the publishing job; use only trusted, SHA-pinned reporting code.

The caller must serialize publishers **per repository/report-key**, with
`cancel-in-progress: false`. The publisher also rejects equal or older source
run numbers/IDs/attempts already recorded in the bot comment, including older
runs for the same head SHA. This is a stale-result guard, not a substitute for
workflow concurrency serialization.

The publisher:

1. Verifies a completed `workflow_run` event against the GitHub API: repository
   identity, run ID/attempt/head SHA, `pull_request` source event and exact API
   workflow path.
2. Resolves exactly one currently open PR from run associations, checking exact
   head SHA, head-repository identity and base repository. Empty fork associations
   use the commit-associated PR API with the same checks. No association or an
   ambiguous association produces an explicit skip, not a success claim.
3. Uses the original run association's base SHA as target; for empty fork
   associations, conservatively uses the current PR base SHA. Calls the GitHub
   compare API for `target...head` to obtain the expected merge base.
4. Selects exactly one nonexpired artifact of the configured name from the
   verified run attempt. Its creation time must be at or after the attempt's
   start time and its run/head/repository metadata must match. Missing/expired
   artifacts are explicit skips. Download URLs come only from GitHub, not data.
5. Reads exactly `base.json`, `head.json`, and `revisions.json` from ZIP, without
   extracting. Both compressed and total uncompressed sizes are capped at
   20 MiB, individual result files at 8 MiB, revisions at 8 KiB and compression
   ratio at 200:1. Rejects duplicate/unexpected paths, directories, symlinks,
   nonregular files, encryption and unsupported compression.
6. Requires `revisions.json` to contain exactly
   `{"target":"<target SHA>","head":"<head SHA>","base":"<merge-base SHA>"}`.
   Binds both result revision roles/commits, SDK identities and benchmark source
   commits to verified metadata. Both suite source commits must equal the PR
   head. Dirty SDK revisions cannot be published. An unverifiable/changed target
   is an explicit skip; conflicting revision identities fail validation.
7. Updates only a `github-actions[bot]` account of type `Bot` whose comment starts
   with the exact `<!-- sdk-performance:REPORT_KEY -->` marker and a valid
   monotonic source stamp. Other authors/markers are untouched; ambiguous or
   malformed bot markers fail closed. Rechecks current PR head and source run
   attempt immediately before the write.

Authenticated requests are restricted to `https://api.github.com`. Signed
artifact downloads use HTTPS without forwarding authorization, with at most
three redirects. API requests have 20-second socket timeouts, a 100-request /
180-second request-start budget, ten-page pagination limits and bounded response
bodies. API errors fail explicitly; raw responses and credentials are not logged.
GitHub Enterprise API origins are not supported.

New `workflow_run` consumers generally must land on the **default branch** before
GitHub will deliver events to them. Roll out trusted shared code first, then the
default-branch consumer, then SDK producer wiring with real immutable pins. Fork
runs can publish only when GitHub establishes the association safely; never work
around missing association by trusting a PR number from an artifact or granting
PR code write credentials.

## Validation

```sh
uv run --frozen --extra dev pytest tests/test_benchmark_report.py \
  tests/test_benchmark_github.py tests/test_benchmark_publish.py
```

The existing root pytest CI job discovers these tests. GitHub interactions are
mocked, including signed-download credential isolation, fork association, stale
runs and writes. Local tests never create live comments. Linux runner noise and
cost calibration belong to the SDK pilots; the reporter does not infer confidence
from uncalibrated local measurements.
