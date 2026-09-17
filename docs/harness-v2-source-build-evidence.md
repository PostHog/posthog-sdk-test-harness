# Node source-built package gate

The Node v2 binding now consumes fresh public packages built from a selected SDK
checkout rather than requiring the calibration version. This local gate is
complete; the [CI pilot](harness-v2-node-ci.md) integrates it, while publication
and actual GitHub execution remain separate gates.

## Implementation

SDK-owned tooling in `PostHog/posthog-js`, under `compliance/node/v2/`, stages the
selected checkout outside its source directory, builds types/core/Node in Linux with the locked workspace
package manager, packs public tarballs and installs an isolated consumer. Core and
types resolve from those same fresh tarballs, not the registry. Provenance records
HEAD, dirty status, source hashes, package/tarball identities and build commands.
A dirty snapshot is not represented as the contents of its base commit.

Worker metadata and advertised SDK version come from installed packages. Fixture
workers must agree with the initial package/public-entry identity. The local
observer reports the actual package version and preserves native completion when
its private seam is missing or incompatible. Such a gap cannot manufacture local
provenance or introduce an instrumentation exception into SDK work.

See `compliance/node/v2/README.md` in the companion SDK checkout for build and
adapter image commands.
That source-build increment changed no shared workflow or SDK behavior. One harness test double now forwards
network constructor options: the preceding networking change had missed it. Its
HTTP-304/fresh-reload assertions remain unchanged.

## Validation

Selected source: dirty snapshot based on
`e96852dbe48690a18e40afe4bb423afb260a28d4`, not latest main or an independently
selected PR. Fresh builds produced Node **5.52.4**, core **1.54.2**, types **1.412.1**.
The same actual versions remain appropriate for this checkout; alternate-version
metadata tests are controlled unit tests, not evidence for another SDK release.

Linux/arm64 used Node **24.21.0**, Python **3.12.12**, pnpm **11.7.0**.

- 22 binding/observer/metadata tests pass.
- 72 host tests pass, with no skips.
- 301 focused harness regressions pass.
- Public CJS and ESM smoke checks pass in both native capture modes.
- Separate-container smoke: five AI cases pass in each mode; GeoIP retains exit 1;
  wrong network topology retains exit 1 and zero mock traffic.
- An independent run passed 22 Node tests using the exported fresh consumer on
  macOS/Node 24.13.0. The initial command omitted the required consumer variable;
  the correctly configured rerun passed.
- Fresh read-only review found no issues. Independent inspection covered source
  and receipts, but did not repeat the full Docker replays.

Full migrated-profile replay through the new packages:

| Profile | Passed | SDK assertion failures | Not selected / unexecuted | CLI exit |
| --- | ---: | ---: | ---: | ---: |
| Legacy | 53 | 3 | 101 | 1 |
| Analytics v1 | 118 | 3 | 36 | 1 |

Together: **151 unique executed origins, 148 passed and three failed**, across 177
profile executions. The same six client-API/codec origins remain unsupported across
both profiles. Discovery or selection does not give them execution credit.

Both focused local-evaluation replays retain two passes and two SDK assertion
failures, with 79 genuine native observations per mode. The three known failures
remain GeoIP defaulting and the two version-2 local boolean-matching cases. They
are not masked or accepted as conformance passes. Full harness pytest was not rerun.

## Artifacts and remaining gates

The [native result ledger](harness-v2-native-results.json) retains the final helper
replay's input identities, counts, actual exits and known failing/unsupported
origins. Raw source-build provenance, reports, diagnostics and logs are retained
locally, not distributed with this repository. Validation describes the original
dirty source snapshots, not their base commits alone or a current-main merge.

The catalog and specs bundle remain unchanged from the networking increment.
The build requires Docker BuildKit/buildx and a compatible packaged runner runtime.
Native compatibility evidence covers this selected snapshot, not every SDK version.

The [opt-in shared workflow](harness-v2-node-ci.md) now integrates immutable input
validation, source builds, actual exit propagation and failure diagnostics.
Publication, changing `latest`, explicit caller enablement, GitHub/amd64 execution,
broader SDK cutover and YAML retirement remain separate gates.
