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
```

The action will run tests, generate reports, and comment on PRs with results.

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

If your SDK can evaluate flags locally, make `/get_feature_flag` honor `force_remote=true` so the harness can verify remote `/flags` payloads without depending on adapter defaults. `distinct_id` is always a top-level adapter parameter, even for SDKs that only mirror it into `person_properties` in the final `/flags` request.

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
| `capture_v1` | `capture_v1` | `POST /i/v1/e` |

Some individual tests have additional requirements (e.g., `encoding_gzip`, `encoding_zstd`).

### Capabilities

Your adapter declares capabilities in its `/health` response:

```json
{
  "sdk_name": "posthog-python",
  "sdk_version": "3.0.0",
  "adapter_version": "1.0.0",
  "capabilities": ["capture_v0", "capture_v1", "encoding_gzip"]
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

## Documentation

- [ADAPTER_GUIDE.md](ADAPTER_GUIDE.md) - Complete guide to implementing adapters
- [EXTENDING.md](EXTENDING.md) - How to add new tests and actions
- [CONTRACT.yaml](CONTRACT.yaml) - Main contract (references modular contracts)
- [contracts/](contracts/) - Modular contract definitions:
  - `adapter_actions.yaml` - Actions that call the adapter
  - `test_actions.yaml` - Test harness actions (assertions, etc.)
  - `capture_tests.yaml` - Capture V0 test suite
  - `capture_analytics_v1_tests.yaml` - Capture V1 test suite
- [examples/minimal_adapter/](examples/minimal_adapter/) - Working example

## License

MIT - see [LICENSE](LICENSE)
