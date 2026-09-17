# Opt-in Gherkin v2 harness

The `posthog-test-harness-v2` command runs Gherkin through the negotiated
`http-json-v2` transport. It is separate from the existing YAML runner and adapter
interface. Existing v1 Docker and shared-workflow entry points remain available.

## Scope and inputs

The migration suite covers 157 YAML-origin cases: 33 legacy capture, 98 analytics
v1, five AI capture, 17 remote flags and four local flags. All 157 have controlled
host execution coverage; that is not 157 SDK passes. The separate canonical
inventory has 728 cases, with 57 harness-ready and 671 missing bindings. Discovery
is not execution. The original 518 assertion-action crosswalk rows remain
unresolved; case-level migration does not close that assertion-level audit.

Specifications, generated transport schemas, fixture contracts and checksummed
migration/coverage ledgers are authored in the companion `sdk-specs` checkout.
Source tests expect it beside this repository as `../specs`; see
`tests/test_v2_gherkin.py` and `tests/test_v2_boundary.py`. It must contain the v2
contracts and `migration/yaml-parity-v1` work, not only the older base revision.
The Node binding and source-package builder live separately in
`PostHog/posthog-js`, under `compliance/node/v2/`.

```sh
uv sync --locked --extra dev
uv run --locked posthog-test-harness-v2 discover \
  --specs ../specs --migration-suite --require-ready \
  --report /tmp/migration-discovery.json
uv run --locked posthog-test-harness-v2 run \
  --specs ../specs --migration-suite \
  --adapter-url "$ADAPTER_URL" --profile "$PROFILE_ID" \
  --report /tmp/migration-report.json
```

`--case-id` selects exact cases while retaining others as `not_selected`.
`discover` without `--migration-suite` inventories canonical cases; `run
--all-features` selects that full inventory. The default run feature is flush.
Installed artifacts instead default to a verified bundle; see
[distribution](harness-v2-distribution.md). Local specs are explicit overrides,
not runtime downloads. Controlled Brotli/Zstd tests require `brotli` and `zstd`
executables; gzip/deflate use the Python standard library.

## Execution contract

Public-operation declarations select candidate cases. Independent feature/API
capabilities and explicit SDK type refine applicability; runtime does not imply
SDK type. Missing required operations and unavailable fixture observations remain
gaps, not passing exclusions. Reports preserve native void, undefined, values and
errors, individual calls, fixture ownership and actual failing exits.

Effective catalog identity includes the frozen base and ordered amendments:

- `capture-amendment-v1`: optional setup GeoIP control and capture-v1 event-root
  options; six compression inputs explicitly select the algorithm being tested.
- `flag-semantics-v1`: ordinary native getters replace forcing remote behavior;
  callback values, loading context and unsubscribe behavior remain observable.
- `local-evaluation-v1`: declared local evaluation uses genuine definitions
  loading, a fresh authenticated HTTP 200 and public readiness after each reload
  within one five-second deadline. Per-call local provenance requires actual
  evaluator instrumentation, not inference from input or HTTP silence.

These are intentional input/contract amendments, not byte-identical migration.
The executable fixtures and source-parity tests reconcile them against pinned
YAML. The v2 runner does not execute YAML at runtime. Historical evidence pointers
in the frozen migration ledgers resolve to the [capture/YAML summary](harness-v2-yaml-parity-evidence.md),
[remote flags summary](harness-v2-remote-flags-evidence.md) and
[local evaluation summary](harness-v2-local-parity-evidence.md). These retain
controlled-test scope without replacing the checksummed source audit; raw
per-run receipts remain local.

## Real Node results and validation boundary

The [native result ledger](harness-v2-native-results.json) summarizes the final
local helper replay against source-built public packages: Node 5.52.4, core 1.54.2
and types 1.412.1, on Linux/arm64 with Node 24.21.0 and Python 3.12.12.

| Profile | Pass | SDK assertion failures | Not selected | CLI exit |
| --- | ---: | ---: | ---: | ---: |
| `node-legacy` / v0 | 53 | 3 | 101 | 1 |
| `node-analytics-v1` / v1 | 118 | 3 | 36 | 1 |

Across profiles: **151 unique origins executed, 148 passed, three failed, six
unsupported**. Failures are GeoIP defaulting and two version-2 local boolean
matching cases. Client-style capture and deflate/Brotli/Zstd origins account for
the six unsupported cases. Neither unsupported cases nor unit/build checks are
SDK conformance passes. The helper aggregate exit is also **1**.

These historical native results describe dirty development snapshots based on
harness `029a94a3861c79f5e99d656b03648ba903eb6e7e`, specs
`9cb330e3bac8868f39cc7dd665e42817285c9493` and Node
`e96852dbe48690a18e40afe4bb423afb260a28d4`, not those commits alone. The ledger
records snapshot/package/report identities. Raw logs and machine-specific
receipts remain local and are not included; their hashes identify evidence but
do not make it retrievable from this repository. These results do not validate
a rebased revision or current main.

Further bounded evidence and reproduction commands:

- [Wheel, sdist and Docker distribution](harness-v2-distribution.md)
- [Separate-container network smoke](harness-v2-network-evidence.md)
- [Source-built public SDK integration](harness-v2-source-build-evidence.md)
- [Strict reusable Node CI pilot](harness-v2-node-ci.md)

A historical full harness run at the local-parity milestone passed 963 tests with
one optional skip; later increments used focused regressions, not another full
suite. A pre-existing canonical L280 uncoordinated-probe defect-detection flake
remains unresolved. Published immutable images, explicit caller enablement,
Ubuntu/amd64 GitHub execution, current-base integration, release/channel policy
and YAML retirement remain separate gates. The Node pilot is not yet enabled.
