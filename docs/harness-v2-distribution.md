# Self-contained Gherkin distribution

Build locally from explicit harness and sdk-specs checkouts:

```sh
uv sync --locked --extra dev
uv run python scripts/build_v2_distribution.py \
  --specs /path/to/sdk-specs --out /tmp/harness-distribution --allow-dirty
```

Use a fresh output directory. Omit `--allow-dirty` for clean release inputs. This command never publishes. It stages the runner in a temporary directory, copies feature files with their inline scenario data, and generates one small bundle manifest containing file hashes, source commit/dirty state and protocol identity. No audit ledger, catalog generator or migration history is needed at runtime.

The output contains a wheel, sdist, hashed dependency lock and distribution identity. Runner provenance distinguishes development snapshots. Existing v1 contract assets and the feature-flag checksum updater remain included in the source distribution. A dirty worktree artifact is not current-main or release validation.

Install outside both source checkouts:

```sh
uv venv --python 3.12.12 /tmp/harness-installed
uv pip install --python /tmp/harness-installed/bin/python \
  --require-hashes -r /tmp/harness-distribution/requirements.lock
uv pip install --python /tmp/harness-installed/bin/python --no-deps \
  /tmp/harness-distribution/*.whl
cd /tmp
/tmp/harness-installed/bin/posthog-test-harness-v2 bundle-info
/tmp/harness-installed/bin/posthog-test-harness-v2 discover \
  --migration-suite --require-ready --report /tmp/discovery.json
```

`bundle-info`, discovery and execution verify the bundled file inventory and hashes. Explicit `--specs` inputs are editable local files and report their current content digests instead.

A generic installed-wheel smoke uses controlled HTTP adapters, healthy and defective:

```sh
uv run python scripts/smoke_v2_distribution.py \
  --python /tmp/harness-installed/bin/python --out /tmp/harness-smoke
```

Build the optional container using the distribution output as its build context:

```sh
docker build -f Dockerfile.v2 -t local-harness:development /tmp/harness-distribution
```

The container verifies artifact checksums, installs dependencies with hashes, and defaults to `posthog-test-harness-v2`. SDK repositories own adapter images and private-network orchestration. Record raw CLI exits and retain reports/diagnostics even on failure. Do not publish development images or replace the existing v1 image as part of local validation.
