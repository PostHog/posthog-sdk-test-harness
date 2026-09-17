# Historical remote flags and native callback semantics

This controlled-host increment added 17 remote YAML origins to the prior 136,
reaching **153/157 controlled-executed origins**. Three unchanged canonical
callback cases were bound separately: canonical discovery became 728 cases,
57 ready and 671 missing. Four local cases followed in the
[local parity increment](harness-v2-local-parity-evidence.md).

No real SDK or existing adapter was executed in this increment. “Native” here
refers to the controlled engine's owning operations and callback stack, not a
production SDK. Subsequent real Node results, including retained SDK failures,
are in the [native result ledger](harness-v2-native-results.json). All 518 original
assertion-action crosswalk rows remain unresolved.

## Contract and execution boundary

The historical `flag-semantics-v1` effective catalog identity was
`3a0a24cdfa3a1bfd677c677b1be7f15400dbb543fcb2d29a0f84f5d6fdc4bc26`;
the amendment input hash was
`5831088033c377faaee005bfcb761a4be18b9f0d54dc8a9cd70cdaaf5384a892`.
Transport remained `2.0.0` / `http-json-v2`. All 180 argument and 180 result
operation-schema graphs remained unchanged. The callback signature and optional
profile SDK type were protocol additions. The later local amendment changes the
effective identity; consumers verify the current ordered inputs rather than using
this historical digest.

Sixteen forcing inputs across 15 origins were removed as an intentional test-setup
amendment, not byte-identical invocation parity. Other typed arguments, omissions
and ordered assertions remained. Per-call mappings live in the companion specs'
`migration/yaml-parity-v1/cases.json` and `approved-amendments.json`; original YAML
and blocker ledgers remain source provenance.

All 17 origins retain explicit server context and the independent `flags_v2`
wire declaration. Runtime and identity do not infer SDK type. Fresh storage/no
local definitions are fixture preconditions, not hidden preload/cache overrides.
Only the repeated-getter case requires `flags_getter_remote_uncached`. Startup
and capture silence remain assertions, not opt-out capabilities. Missing required
operations/fixtures are gaps, not adapter-emulated successes.

The controlled engine owns real HTTP fetching, retry attempts, parsing, cache
choice, capture and subscriptions. `on_feature_flags` preserves actual enabled
keys, values/variants and optional loading context, including immediate two-argument
registration. Undefined remains distinct from false/null. No implicit getter fills
callback data. Synchronous continuations execute on the owning callback stack;
unsubscribe removes the real listener and teardown disposes fixtures.

Client comparisons cover one HTTP load followed by two cached reads, prepared
zero-network reads, changed values, errors retaining cached data and idempotent
unsubscribe. Controlled reload is awaitable; this does not establish browser SDK
reload timing. Cached/error callbacks alone do not prove fresh successful HTTP.

Preserved source limits include first matching `/flags` request checks, ordinary
request-zero Authorization absence, all accumulated matching-path counts,
token-before-api_key precedence and Python/JSON equality behavior. The 502/504
then 200 cases require two HTTP requests and a true getter result, not an added
backoff floor. `$feature_flag_called` must reach HTTP after public flush. Source
IDs use `yaml:029a94a:feature_flags:<category>:<name>` and executable IDs use
`migration:yaml-parity-v1:feature_flags:<name>`; the companion ledger retains all
17 exact names and source lines.

## Historical validation

- **52 new Python tests passed**, including all 17 origins, 22 real-HTTP defect
  vectors, the three canonical callbacks, context/error/unsubscribe checks,
  source reconciliation, applicability and missing-operation/fixture checks.
- **37 shared-contract tests passed**, with exact regeneration checked.
- Final affected outside-checkout slice: **344 passed**.
- One full run: **924 passed, 1 failed, 1 skipped**, two existing warnings. The
  failure was a stale discovery-set assertion after callback bindings were added;
  its corrected exact test passed once. There was no corrected full-suite rerun.
  Final SDK-type and JSON-scope refinements had focused/outside coverage instead.
- The known pre-existing L280 uncoordinated-probe flake did not recur in that run;
  it remained unresolved and was not skipped or weakened.
- Controlled receipts: 17 remote cases passed with **36 public calls and 20 HTTP
  requests**; three canonical callback cases passed with **10 calls and zero HTTP**.
  Discovery and eight inventory tests were separate routing/provenance checks.

Source-named tests are `tests/test_v2_remote_flags.py` and
`tests/test_v2_remote_flag_blockers.py`; use the companion specs and locked
environment described in the [overview](harness-v2.md). Raw reports, diagnostics,
parsed-response receipts and verified before-state snapshots remain local and
are not included in this repository. Results describe development snapshots
based on harness `029a94a` and specs `9cb330e`, not those commits alone or current
main. They establish controlled harness behavior, not production SDK conformance.
