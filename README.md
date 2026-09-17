# PostHog SDK Test Harness

Language-agnostic test harness for validating PostHog SDK compliance.

## What is This?

This test harness validates that PostHog SDKs correctly implement the PostHog API by:
1. Running a mock PostHog server
2. Exercising your SDK through a simple HTTP adapter
3. Verifying behavior matches the contract defined in [CONTRACT.yaml](CONTRACT.yaml)

## Quick Start

### Using Docker (Recommended)

```bash
# Run tests against your SDK adapter
docker run --rm \
  --network host \
  ghcr.io/posthog/sdk-test-harness:latest \
  run --adapter-url http://localhost:8080
```

### Using in CI/CD

Add to your SDK's `.github/workflows/`:

```yaml
jobs:
  sdk-compliance:
    uses: PostHog/posthog-sdk-test-harness/.github/workflows/test-sdk-action.yml@v1
    with:
      adapter-dockerfile: "tests/adapter/Dockerfile"
      adapter-context: "."
      sdk-type: "server"        # or "client"
      suite: "capture"          # optional; comma/space/newline separated values are supported
      continue-on-error: true    # set false when compliance should block CI
```

The action will run tests, generate reports, and comment on PRs with results.

### Choosing `sdk-type`

`sdk-type` describes the SDK's capture wire format, not where the SDK runs. Do **not** choose it based on frontend/backend, mobile/server, or browser/native labels alone.

- Use `client` for SDKs that send client-style capture payloads to `/e/`, with event data such as `token` and `distinct_id` carried in the event/properties payload.
- Use `server` for SDKs that send server-style capture payloads to `/batch`, with a body shaped like `{ "api_key": "...", "batch": [...] }`.

For example, a mobile SDK that posts `{ "api_key": "...", "batch": [...] }` to `/batch` should use `sdk-type: "server"` for harness filtering.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Test Harness                            │
│  Reads CONTRACT.yaml and executes tests                         │
└─────────────────────────────────────────────────────────────────┘
              │                              │
              │ HTTP                         │ HTTP
              ▼                              ▼
┌─────────────────────────┐    ┌─────────────────────────────────┐
│   Mock PostHog Server   │    │       SDK Adapter               │
│  Simulates API          │◄───│  Wraps your SDK                 │
│  Records requests       │    │  Exposes REST API               │
└─────────────────────────┘    └─────────────────────────────────┘
```

## Creating an SDK Adapter

Your adapter is a simple HTTP service that wraps your SDK. It needs these endpoints:

### Required Endpoints

```
GET  /health    - Return SDK name/version and capabilities
POST /init      - Initialize SDK with config
POST /capture   - Capture an event
POST /get_feature_flag - Evaluate a feature flag
POST /flush     - Flush pending events
GET  /state     - Return internal state
POST /reset     - Reset SDK state
```

If your SDK can evaluate flags locally, make `/get_feature_flag` honor `force_remote=true` so the harness can verify remote `/flags` payloads without depending on adapter defaults. `distinct_id` is always a top-level adapter parameter.

### Example (Python)

```python
from flask import Flask, request, jsonify
import posthog

app = Flask(__name__)
client = None

@app.route("/init", methods=["POST"])
def init():
    global client
    data = request.json
    client = posthog.Client(
        api_key=data["api_key"],
        host=data["host"]
    )
    return jsonify({"success": True})

@app.route("/capture", methods=["POST"])
def capture():
    data = request.json
    uuid = client.capture(
        distinct_id=data["distinct_id"],
        event=data["event"]
    )
    return jsonify({"success": True, "uuid": uuid})

# ... implement other endpoints
```

See [ADAPTER_GUIDE.md](ADAPTER_GUIDE.md) for complete implementation details and [examples/minimal_adapter/](examples/minimal_adapter/) for a full example.

## Tests

Tests are defined in [CONTRACT.yaml](CONTRACT.yaml) and organized into suites. Which suites run depends on the `capabilities` your adapter declares in `/health`:

| Suite | Requires | Protocol |
|-------|----------|----------|
| `capture` | `capture_v0` | `POST /batch` |
| `capture_v1` | `capture_v1` | `POST /i/v1/analytics/events` |
| `capture_ai` | `capture_ai_v0` | `POST /i/v0/ai/batch/` |

Some individual tests have additional requirements (e.g., `encoding_gzip`, `encoding_zstd`).

### Capabilities

Your adapter declares capabilities in its `/health` response:

```json
{
  "sdk_name": "posthog-python",
  "sdk_version": "3.0.0",
  "adapter_version": "1.0.0",
  "capabilities": ["capture_v0", "capture_v1", "capture_ai_v0", "encoding_gzip"]
}
```

The harness skips any suite or test whose `requires` field isn't satisfied by the adapter's capabilities. If `capabilities` is omitted, only tests with no `requires` field will run.

See [CONTRACT.yaml](CONTRACT.yaml) for the complete test specification.

## CLI Usage

```bash
# Run all tests
uv run posthog-test-harness run --adapter-url http://localhost:8080

# Run specific suite
uv run posthog-test-harness run --adapter-url http://localhost:8080 --suite capture

# Generate report
uv run posthog-test-harness run --adapter-url http://localhost:8080 --report report.md

# Run tests in parallel (requires adapter support)
uv run posthog-test-harness run --adapter-url http://localhost:8080 --concurrency 4

# JSON output
uv run posthog-test-harness run --adapter-url http://localhost:8080 --output json

# Run mock server standalone
uv run posthog-test-harness mock-server --port 8081

# Check adapter health
uv run posthog-test-harness health --adapter-url http://localhost:8080
```

## Adding New Tests

Tests are defined in [CONTRACT.yaml](CONTRACT.yaml). To add a test:

```yaml
test_suites:
  capture:
    categories:
      my_new_category:
        tests:
          - name: my_new_test
            steps:
              - action: init
              - action: capture
                params:
                  distinct_id: user1
                  event: test_event
              - action: assert_event_has_field
                params:
                  field: uuid
```

No Python code needed! See [EXTENDING.md](EXTENDING.md) for details on adding custom actions.

## Local Development

```bash
# Clone and install
git clone https://github.com/PostHog/posthog-sdk-test-harness.git
cd posthog-sdk-test-harness
bin/install

# Test the example adapter
bin/test

# Format code
bin/fmt

# Run tests
uv run pytest
```

### Contributing

When making changes to the test harness, add a [Sampo](https://github.com/bruits/sampo) changeset describing the release before opening your PR:

```bash
sampo add
```

This prompts for the bump type and a release note, and writes a file under `.sampo/changesets/`. Commit that file with your PR.

- **patch** — bug fixes, documentation, internal refactors
- **minor** — new tests, new actions (backwards compatible)
- **major** — breaking changes to `CONTRACT.yaml` or the adapter interface

CI will fail (`Changeset hygiene` check) if you change releasable code without including a changeset.

The actual version bump, changelog entry, tag, and Docker image publish happen in a single gated workflow after a maintainer approves the release in Slack. See [RELEASING.md](RELEASING.md) for the full flow.

## Versioning

Docker images are published with semantic versioning:
- `latest` — most recently approved release
- `1` — latest `v1.x.x` release (only published once the major is non-zero)
- `1.0` — latest `v1.0.x` release
- `1.0.0` — specific version

All tags only move when a maintainer approves a release through the gated workflow — there is no automatic publish on every push to `main`.

Pin to a specific version in your CI for stability:
```yaml
test-harness-version: "1.0"  # Recommended: pin to major.minor
```

## Harness v2 development

The opt-in `posthog-test-harness-v2 run` command executes Gherkin through a
negotiated v2 transport. The [v2 overview](docs/harness-v2.md) describes the
157-case migration suite, canonical discovery, explicit local specs inputs and
known SDK failures. Controlled-host results are not SDK conformance results.
Built artifacts default to a verified bundled snapshot; see
[v2 distribution](docs/harness-v2-distribution.md) for wheel, sdist and opt-in
Docker instructions, and the [Node CI pilot](docs/harness-v2-node-ci.md) for the
separate reusable workflow. Existing v1 entry points remain available.

### V2 network addresses

V2 listeners remain local by default. `run --mock-bind-host HOST` selects the mock
listener interface; `--mock-advertised-host HOST` selects the hostname sent to the
SDK in setup. Both default independently to `127.0.0.1`. Each case still receives
a fresh OS-allocated port. Retired URLs stay bound and reject late requests until
run teardown, so traffic cannot leak into a later case. Diagnostics record both
configured hosts and every case URL.

These options accept a DNS name, IPv4 address, or **bare IPv6** address such as
`::1`; IPv6 is bracketed when constructing URLs. Do not supply a scheme, port,
credentials, path, query, fragment, brackets, or IPv6 zone identifier. Ports are
always allocated by the harness, not supplied in the advertised host.

For separate containers on a private Docker network, give the runner the network
alias `runner` and the adapter the alias `adapter`, then run:

```sh
posthog-test-harness-v2 run --migration-suite \
  --adapter-url http://adapter:8080 --allow-private-network --profile node-legacy \
  --mock-bind-host 0.0.0.0 --mock-advertised-host runner \
  --report /tmp/report.json
```

`--allow-private-network` permits operator-selected adapter DNS/IP hosts; it does
**not** verify that a hostname or IP belongs to a private network. Without it, the
adapter host must remain `127.0.0.1`. Adapter URLs require HTTP and an explicit port,
without credentials, non-root paths, query, or fragment. Redirects and environment
proxy settings are not used. Docker network isolation supplies the privacy boundary.

The Node v2 host must explicitly opt in with `--listen-host 0.0.0.0 --listen-port 8080`.
Use separate network namespaces, no published host ports, and a private network
(e.g. `docker network create --internal NAME`). Wildcard binding is an explicit
opt-in for that topology; it never changes the advertised host automatically.
These unauthenticated mock services are not intended for public exposure.

A bounded real-Node distribution smoke is available for prebuilt runner and
calibration-host images. The adapter image must contain compatible native Linux
Node, Python/harness, the pinned installed public SDK dependency closure, and a
host entrypoint accepting the listen/capture-mode options (see the script docstring).
It is not SDK-source production packaging:

```sh
uv run --locked python scripts/smoke_v2_network.py \
  --runner-image posthog-harness-v2:network-dev \
  --adapter-image posthog-node-v2:network-smoke \
  --out /tmp/harness-v2-network-smoke
```

Choose a fresh output directory. The script creates and cleans up its own internal
network and containers, retains topology/commands/reports, expects five real AI
passes and the known GeoIP assertion failure in each capture mode, and checks that
an intentionally loopback-advertised cross-container run cannot pass. That last
run is a topology-negative control, not an additional SDK defect. This slice does
not rerun or clear the known local flag boolean-matching failures.

## Documentation

- [ADAPTER_GUIDE.md](ADAPTER_GUIDE.md) - Complete guide to implementing adapters
- [EXTENDING.md](EXTENDING.md) - How to add new tests and actions
- [Harness v2](docs/harness-v2.md) - Migration scope, execution contract, Node results and remaining gates
- [CONTRACT.yaml](CONTRACT.yaml) - Main contract (references modular contracts)
- [Feature Flag Rules v2](contracts/feature_flag_rules_v2/README.md) - Versioned config, definitions, response and event schemas, fixtures, and evaluation corpus
- [contracts/](contracts/) - Modular contract definitions:
  - `adapter_actions.yaml` - Actions that call the adapter
  - `test_actions.yaml` - Test harness actions (assertions, etc.)
  - `capture_tests.yaml` - Capture V0 test suite
  - `capture_analytics_v1_tests.yaml` - Capture V1 test suite
  - `capture_ai_tests.yaml` - Dedicated AI capture endpoint test suite
- [examples/minimal_adapter/](examples/minimal_adapter/) - Working example

## License

MIT - see [LICENSE](LICENSE)

### Opt-in local feature flag evaluation

Adapters may explicitly advertise `feature_flags_local_evaluation_v1` to enable
local-rule compliance tests for **both** property matching versions 1 and 2.
No existing adapter or default health response opts in automatically; remote
fixtures and requests remain unchanged. See [the optional adapter protocol](ADAPTER_GUIDE.md#optional-local-feature-flag-evaluation-protocol-v1)
for privileged definitions loading, bounded readiness/reload and local-only
result requirements.
