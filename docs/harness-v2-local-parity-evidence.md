# Historical local evaluation parity: final four YAML origins

This increment completed **157/157 translated and controlled-executed origins**.
It added four local cases to the prior 153; it did not establish real SDK or
existing-adapter conformance. Canonical discovery remained 728 cases, 57 ready
and 671 missing; all 518 original assertion-action crosswalk rows remained
unresolved. Subsequent real Node results are separate and retain two local SDK
assertion failures: see the [native result ledger](harness-v2-native-results.json).

## Source cases and public execution

Source prefix:
`yaml:029a94a:feature_flags_local_evaluation:versioned_boolean_matching:`.
Migration prefix: `migration:yaml-parity-v1:feature_flags_local_evaluation:`.

| Suffix | YAML line | Public getters | Fresh reload barriers |
| --- | ---: | ---: | ---: |
| `matching_version_missing` | 8 | 38 | 1 |
| `matching_version_1` | 995 | 38 | 1 |
| `matching_version_2` | 1433 | 38 | 1 |
| `version_only_reload_1_2_1_2_missing` | 1871 | 5 | 5 |

The four cases make **139 public RPCs**: four setup calls, 119 getters, eight
reloads and eight readiness calls. Typed JSON DocStrings retain definitions,
arrays, booleans/numbers/strings/null, group mappings and recursive AND/OR cohorts.
The same-instance five-reload sequence expects **true,false,true,false,true**,
returning to legacy matching when the version is omitted. The runner executes
Gherkin, not YAML; source tests reconcile ordered inputs and assertions against
pinned YAML and its immutable inventory.

Source helper semantics were read at harness `029a94a3861c79f5e99d656b03648ba903eb6e7e`.
Each getter must complete with a conclusive boolean/string, genuine per-call local
provenance and no remote flags traffic across initialization/reloads/getters.
Every reload requires a NEW successful authenticated definitions HTTP 200 after
its barrier starts, plus public readiness, within one 5000ms deadline. Old
readiness or native void alone is insufficient. Project-token query and Bearer
personal-key authentication apply to both modern and legacy definitions paths.

## Scoped local-evaluation-v1 amendment

The amendment input SHA-256 is
`0fda649d942134af6d6c80a9dcc44a0609f063e241b7113338be506f2abf3f53`;
the effective base+capture+flag+local catalog identity is
`c52ae7fac46f0395a78276bbc6c3b97ea83538005bcee11b60b2dc33879adfaa`.
The companion specs generator and consumer verify ordered input bytes and
identity; pre-local peers reject before allocation.

For explicitly declared local evaluation, `/reload_feature_flags` may invoke
native definitions refresh. Public void completion is not readiness; the separate
public readiness call shares the deadline. Client evaluated reload/callback
behavior is unchanged. Setup maps the personal API key to `config.secret_key`
without hidden preload, TTL or polling overrides. The source had no SDK-type
filter, so none is inferred from runtime.

Optional `evaluation_provenance(call_id)` observes actual evaluator instrumentation
correlated to fixture/call/key/value and classification. It is not inferred from
local-only input, HTTP silence or counters. Records are fixture-scoped, immutable
after the owning call settles, and cleared on close. Missing instrumentation is
`blocked_fixture`, not a fabricated local result. Reading a settled record does
not require pausing unrelated work; cumulative-activity scheduler rules remain.

## Controlled engine and defects

`tests/v2_local_parity_host.py` owns real HTTP loading, accepted document state,
version selection and narrow exact/is_not person/group/cohort evaluation. It
reads response bytes rather than expected answers. Mutated-rule/property/cohort
checks demonstrate evaluation rather than source-key answer lookup; this is not
the full 358-case evaluator.

All 119 values and eight barriers passed on both `/flags/definitions` and
`/api/feature_flag/local_evaluation/`. Four native initializations also loaded
definitions; no remote flags/decide traffic occurred. Negative coverage includes
cached/no-new fetch, stale readiness with failed authentication or HTTP 304,
ignored reload/version, omission retaining v2, remote escapes, missing/wrong
provenance attribution, inconclusive results and wrong values. Timeout tests
preserve one five-second budget, cancel work and verify fixture disposal.

## Historical validation

- Final focused module: **33 tests**, including a genuine HTTP-304 regression.
- Final affected outside-checkout run: **399 passed**. Earlier runs corrected
  two stale not-selected tail counts without changing runtime assertions.
- **39 shared-contract tests** and exact regeneration passed; **8 inventory
  tests** passed.
- One serial full suite: **963 passed, 1 optional integration skipped**, two
  existing collection warnings, in 670.08 seconds. Only import ordering and
  documentation changed afterward; the final affected run covered that ordering.
- The known canonical L280 uncoordinated-probe defect-detection flake did not recur;
  it remained unresolved, not skipped or weakened.
- Outside-checkout CLI receipts executed all four local cases. Per-call reports
  retained results, provenance, authentication verdicts and barrier timing.

Run `uv run --locked pytest -q tests/test_v2_local_parity.py` with the companion
specs checkout described in the [overview](harness-v2.md). Raw logs, reports,
per-call receipts and verified preservation snapshots remain local, not included
in this repository. These checks describe development snapshots based on harness
`029a94a` and specs `9cb330e`, not current-main integration or SDK conformance.
