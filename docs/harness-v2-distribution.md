# V2 distribution foundation

## Status and boundary

The v2 runner can now use a self-contained, verified specs bundle from an installed
wheel, a wheel rebuilt from the sdist, or the opt-in Docker image. It does not fetch
specifications at runtime. `sdk-specs` remains the authoring source; distribution
copies are generated in an isolated build directory, not edited in this repository.

This completes the packaging foundation, not CI cutover. Existing Dockerfile,
release workflow, shared v1 workflow, version and `latest` channel are unchanged.
The validated artifacts are **development snapshots** built from dirty source
checkouts. Their package version is 1.4.0, not a published v2 release. No artifacts
have been published.

## Build

From the harness checkout with its locked development environment:

```sh
uv run --locked python scripts/build_v2_distribution.py \
  --specs ../specs --out /tmp/harness-v2-artifacts --allow-dirty
```

Choose a fresh output directory. Omit `--allow-dirty` for a clean-source build;
otherwise dirty inputs are refused. The build:

1. Stages the runner separately and checks copied source bytes.
2. Copies 106 selected specification resources: Gherkin, fixture data, contract
   inputs/generated schemas, migration and coverage ledgers.
3. Verifies the effective catalog identity and frozen feature/ledger checksums;
   checks 157 migration cases are harness-ready and preserves 728 canonical cases
   for discovery. Canonical discovery is not a claim that all cases are implemented.
4. Records source commits and dirty flags, a runner snapshot hash, individual bundle
   file digests and the bundle identity. A dirty checkout's commit is its base, not
   a claim that the copied files exist at that commit.
5. Builds wheel/sdist, exports hash-locked runtime dependencies from `uv.lock`, and
   writes `distribution.json` with artifact checksums and development status.

The generated `_bundle` and `build-provenance.json` live inside the distribution.
Missing, changed or extra bundle files fail verification before discovery or RPC.
The hashes detect mismatched contents; consumers still need a trusted artifact pin.

## Installed use

Install dependencies from `requirements.lock`, then install the wheel without
resolving another dependency set. Use Python 3.12.12, matching the project contract.

```sh
uv venv --python 3.12.12 /tmp/harness-v2-consumer
uv pip sync --python /tmp/harness-v2-consumer/bin/python --require-hashes \
  /tmp/harness-v2-artifacts/requirements.lock
uv pip install --python /tmp/harness-v2-consumer/bin/python --no-deps \
  /tmp/harness-v2-artifacts/*.whl

/tmp/harness-v2-consumer/bin/posthog-test-harness-v2 bundle-info
/tmp/harness-v2-consumer/bin/posthog-test-harness-v2 discover \
  --migration-suite --require-ready --report /tmp/migration-discovery.json
```

`run` likewise defaults to the verified bundle. Select `--migration-suite` for the
migrated scope; its default feature remains the original flush calibration scope.
Run diagnostics and discovery output record the runner version and input identity.

`--specs PATH` explicitly selects local development inputs. They retain existing
frozen-source and catalog checks and are labeled separately from packaged inputs.
An external `--contracts` override requires explicit `--specs` too.

## Docker

The build context is the artifact directory, not a source checkout:

```sh
docker build -f Dockerfile.v2 -t posthog-harness-v2:development /tmp/harness-v2-artifacts
docker run --rm --network none posthog-harness-v2:development bundle-info
```

`Dockerfile.v2` uses a digest-pinned Python 3.12.12 base, checks wheel/dependency-lock
hashes, installs locked dependencies and that exact wheel, and validates its bundle.
Its entrypoint is `posthog-test-harness-v2`. This image does not move any registry tag.

## Repeatable package smoke

```sh
uv run --locked python scripts/smoke_v2_distribution.py \
  --python /tmp/harness-v2-consumer/bin/python \
  --out /tmp/harness-v2-wheel-smoke
```

The smoke requires a fresh directory outside the checkout, rejects editable
installations, copies only its controlled host fixtures, and executes five AI cases
through the installed CLI. A healthy controlled host must exit 0; a deliberately
misrouting host must exit 1. Reports, diagnostics and exit logs are retained.
These are harness/distribution tests, **not SDK conformance results**.

## Validation for this increment

- **167 focused tests pass**, including 10 new bundle tests and existing discovery,
  Gherkin, boundary and AI migration coverage.
- Clean wheel installation outside the checkout: both discovery inventories match
  source; healthy/defective controlled execution returns 0/1 respectively.
- Sdist rebuilt into a wheel offline, using cached build dependencies; fresh locked
  environment produces the same bundle and controlled execution results.
- Linux/arm64 Docker runs with `--network none` discover both scopes and execute the
  same healthy/defective controlled runs. No source/specs checkout is mounted.
- Separate real Node packaged-consumer smoke using the installed harness: five AI
  cases pass and the GeoIP case fails in each native mode; both real CLI exits are 1.
  The copied Node adapter loads contracts from the installed bundle. This increment
  does not rerun the entire 151-case SDK scope or repair its three known failures.
- Fresh read-only review found no issues. The final pinned-base/checksum Docker
  refinement was rebuilt and rerun successfully after review.

The initial distribution smoke used bundle
`714d0a35e52e3bf8dc4a91419e82b30a480119d7695435d69122e4c645587bb8`.
The later [network deployment](harness-v2-network-evidence.md) changed that
identity. These are historical development-snapshot checks, not a build of a
current-main merge. Raw build/runtime logs and artifacts are retained locally,
not distributed with this repository. See the [current result ledger](harness-v2-native-results.json)
for the subsequent native replay's input identities and failing outcomes.

## Remaining gates

1. **Completed in the next increment:** explicit adapter listen and mock
   bind/advertised addresses, validated between separate containers. See
   [network deployment evidence](harness-v2-network-evidence.md) for the approved
   opt-in transport amendment and newer bundle identity.
2. **Source-build gate completed:** [selected-checkout package integration](harness-v2-source-build-evidence.md)
   replaces the calibration-only SDK version pin and retains honest instrumentation
   gaps. The shared workflow below uses this path.
3. **Implemented and locally validated:** [opt-in v2 Node shared workflow](harness-v2-node-ci.md)
   validates immutable inputs, preserves failing exits and diagnostics, and leaves
   the v1 caller intact. Real published pins, caller enablement and GitHub/amd64
   execution remain pending; local replay is not a GitHub CI run.
4. Clean committed inputs, approved versioning/publication and a release channel
   policy that does not move v1 consumers onto v2 through `latest`.

The six unsupported SDK API/codec origins and three known SDK assertion failures
remain separate from these distribution gates. No SDK migration or enforcement
change is authorized by a successful packaging smoke alone.
