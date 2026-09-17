# YAML parity: controlled execution of all five source suites

**157 of 157 YAML origins were translated and controlled-executed**: five AI,
98 analytics-v1, 33 legacy capture, 17 remote flags and four local cases. These
historical results are controlled-host evidence, not real SDK conformance.
For subsequent real Node outcomes and remaining gates, see the
[v2 overview](harness-v2.md) and [native result ledger](harness-v2-native-results.json).

## Source and traceability

Source YAML and helper semantics were read at harness
`029a94a3861c79f5e99d656b03648ba903eb6e7e`, not from modified working files.
The companion specs work was based on
`9cb330e3bac8868f39cc7dd665e42817285c9493`. Results describe development snapshots,
not those base commits alone or a current-main merge.

The companion `sdk-specs` directory `migration/yaml-parity-v1/` owns the checksummed
source manifest, executable Gherkin, `cases.json`, historical blocker ledgers and
`approved-amendments.json`. Source IDs retain `yaml:029a94a:<suite>:<category>:<name>`;
executable IDs use `migration:yaml-parity-v1:<suite>:<name>`. Analytics source IDs
use `capture_v1`; their migration suite name is `capture_analytics_v1`.

The canonical inventory remains separate: 728 identities, 57 harness-ready after
callback bindings. Readiness is not execution. All 518 original assertion-action
crosswalk rows remain unresolved; case-level evidence does not close that audit.

## Capture migration milestones

| Increment | Newly controlled-executed origins | Cumulative migration origins |
| --- | ---: | ---: |
| AI public capture/UUID/timestamp | 5 | 5 |
| Analytics wire/header/body | 18 | 23 |
| Analytics properties/batching | 22 | 45 |
| Analytics retries | 29 | 74 |
| Analytics partial outcomes and representable options | 18 | 92 |
| Legacy capture/batch/event wire | 32 | 124 |
| Capture amendment resolving former catalog blockers | 12 | 136 |
| [Remote flags and callbacks](harness-v2-remote-flags-evidence.md) | 17 migration origins | 153 |
| [Local evaluation](harness-v2-local-parity-evidence.md) | 4 | 157 |

Bindings invoke public operations. Controlled engines own buffering, HTTP,
retries, parsing and delivery; they are not SDK implementations. Real source
observation windows remain, including 109 seconds of post-call waits across the
29 analytics retry cases, 54 seconds across the 18 outcome cases and 88 seconds
across the 32 initial legacy cases. Native completion does not replace those waits.

Preserved assertion limits matter: first-request/first-event scopes are not
all-request/all-event guarantees; UUID parseability does not require UUIDv7;
field presence is not non-null validation; some equality retains Python
boolean/numeric semantics. Backoff checks only the first delay floor, and any
recorded 200 satisfies the original success helper. Eight retry/response cases
inspect mock-authored responses, not native SDK interpretation. Source-parity
and deliberate-defect tests preserve these distinctions rather than strengthening
assertions based on scenario titles.

Legacy batch and event contracts are independently declared as `capture_v0_batch`
and `capture_v0_event`, each also requiring `capture_v0`. Runtime does not infer
wire shape. Required missing operations or fixture observations remain gaps.

`capture-amendment-v1` deliberately adds optional setup GeoIP control and
capture-v1 event-root options. Six enabled-compression inputs now select an
explicit algorithm; this is an approved input amendment, not byte-identical
invocation parity. Historical blocker bytes remain provenance; the amendment
ledger records their resolution. The 12 amended cases made 38 public calls and
12 HTTP requests. Controlled Brotli/Zstd roundtrips require installed executables;
those do not establish native SDK codec support.

## Historical validation

| Increment | Focused/outside checks | Full-suite receipt |
| --- | --- | --- |
| AI foundation | 48 passed | 521 passed, 1 skipped |
| Analytics wire | 151 passed | 624 passed, 1 skipped |
| Batching after review corrections | 226 combined; 75 batching outside | 699 passed, 1 skipped |
| Analytics retries | 259 combined outside | 732 passed, 1 skipped |
| Analytics outcomes | 293 combined outside | 766 passed, 1 skipped |
| Legacy capture | 46 focused; 337 outside plus 2 corrected inventory tests | 812 passed, 1 skipped |
| Capture amendment | 275 final affected outside | 878 passed, 1 skipped |

These are successive historical runs, not one final combined validation. Earlier
batching full runs failed the pre-existing canonical L280 `uncoordinated_probes`
defect detector: an intentionally defective host sometimes passed. The same
failure reproduced on the unchanged pre-edit snapshot on repetition nine.
Subsequent green runs do not resolve that flake. Two existing collection warnings
and an optional integration skip were retained. Stale inventory expectations
caught during expansion were corrected without weakening runtime assertions.

Source-named tests are `tests/test_v2_{yaml_parity,analytics_wire,analytics_batching,
analytics_retry,analytics_outcomes,legacy_capture,capture_amendments}.py`.
Use the locked harness environment and companion specs inputs described in the
[overview](harness-v2.md). Raw logs, reports, per-call diagnostics and preservation
snapshots remain local; they are not included in this repository. Remote and local
validation histories are recorded in their linked summaries above. No capture
milestone by itself establishes SDK conformance, packaging readiness or YAML
retirement.
