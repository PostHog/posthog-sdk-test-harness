# Gherkin compliance harness

The opt-in `posthog-test-harness-v2` entry point runs SDK-independent Gherkin over HTTP. The SDK repository owns its native adapter, package build, and compliance caller. The harness owns mock services, input selection, assertions, deadlines, and reports. Existing YAML/v1 commands remain available.

## Run

```sh
posthog-test-harness-v2 discover --specs /path/to/sdk-specs \
  --migration-suite --require-ready --report discovery.json
posthog-test-harness-v2 discover --specs /path/to/sdk-specs \
  --acceptance-suite --require-ready --report acceptance-discovery.json
posthog-test-harness-v2 run --specs /path/to/sdk-specs \
  --migration-suite --adapter-url http://127.0.0.1:8080 \
  --profile PROFILE --timeout-ms 60000 --report report.json
posthog-test-harness-v2 run --specs /path/to/sdk-specs \
  --acceptance-suite --adapter-url http://127.0.0.1:8080 \
  --profile PROFILE --timeout-ms 60000 --report acceptance-report.json
```

Omit `--specs` to use the verified [packaged features](harness-v2-distribution.md). `--migration-suite` runs the YAML-parity capabilities. `discover --acceptance-suite` checks step bindings for all cases opted in with `@sdk:client` or `@sdk:server`, without contacting an adapter. `run --acceptance-suite` selects only the opted-in cases for the adapter profile; unrelated cases remain visible but unselected. `--feature` selects relative paths for explicit inspection, and `--all-features` includes unresolved cases. `--case-id` remains an explicit debugging selector outside the tag-selected acceptance suite.

The same official Cucumber parser expands backgrounds, rules and outlines across both suites. Ordinary scenarios use feature path and scenario name as their report identity; Scenario Outlines may use `@case:<case_id>` with a unique `case_id` Examples column to label each row. Unmigrated outlines without labels use their source-row locations. The source-content revision remains in the report separately. `@requires:<capability>` declares SDK features; `@sdk:server` and `@sdk:client` restrict applicability in the migration suite and opt scenarios into tag-based acceptance selection. Both tags together opt the case in for either SDK type. Required routes come from step bindings; a missing required route does not silently exclude a selected case.

The migrated scope contains 157 cases. Canonical features are discoverable but many require operations or fixtures not yet implemented. Discovery readiness means steps are bound, not SDK conformance. Missing public operations are `unsupported_binding`; unavailable fixture controls are `blocked_fixture`; undefined steps are harness errors. Public getters such as `pending_events()` are legitimate future bindings when shared event/queued/in-flight/retry semantics are defined. Queue snapshots, evaluator hooks and fabricated counters are not substitutes. Retention and delivery may instead be proved by public flush/retry behavior and observed traffic where that preserves the scenario's assertion.

## Draft HTTP adapter contract

Protocol identity: **`sdk-compliance-v2-draft2`**. All routes below use POST with uncompressed JSON, maximum body 1 MiB, no redirects. These unauthenticated services belong on loopback or an isolated private network.

- `/v2/negotiate`: `{ "protocol": "sdk-compliance-v2-draft2" }` → `{ "protocol": "sdk-compliance-v2-draft2", "supported_routes": ["/setup", "..."], "profiles": [{ "id": "PROFILE", "sdk_type": "server", "sdk_capabilities": [], "fixture_capabilities": ["storage.empty.v1"] }], "max_timeout_ms": 60000 }`. SDK type is `server` or `client`. Capability and profile IDs must be unique. Declare only genuine support.
- `/v2/fixtures/allocate`: `{fixture_id, case_id, profile_id, timeout_ms}` → `{fixture_id}`. IDs are strings; timeout is a positive integer within the negotiated bound. Each allocation creates a fresh isolated receiver. `storage.empty.v1` means the receiver starts with empty per-case persistent storage; allocation establishes this, not a private reset call.
- `/v2/invoke`: `{fixture_id, call_id, route, args, timeout_ms}` → `{fixture_id, call_id, completion}`. Invocation IDs cannot be reused. `args` is a JSON object. Route support is negotiated, not inferred from an SDK name.
- `/v2/fixtures/close`: `{fixture_id, timeout_ms}` → `{fixture_id}` after bounded public cleanup. Failed shutdown is an infrastructure failure, not successful cancellation. An isolated worker may be terminated after its deadline, without mutating SDK internals.

Completion is exactly one of:

```json
{"kind":"sdk","outcome":{"kind":"void"}}
{"kind":"sdk","outcome":{"kind":"undefined"}}
{"kind":"sdk","outcome":{"kind":"value","value":null}}
{"kind":"sdk","outcome":{"kind":"thrown","error":{"name":"Error","message":"native error"}}}
{"kind":"harness","failure":{"kind":"timeout","code":"deadline","message":"Operation timed out"}}
```

`value` accepts JSON data including false, zero and null. Omitted arguments remain omitted. Native SDK throws are not transport errors; public-operation throws fail acceptance cases and remain visible in the report. Adapters must pass representable invalid inputs to the SDK, faithfully translate native argument names, and avoid injected defaults, hidden flushes or corrective retries. Malformed requests, duplicate IDs and nonexistent fixtures produce non-200 JSON errors. The client retains bounded error detail. Request transport permits one second beyond the native deadline for a failure response; negotiation defaults to five seconds. No SDK result catalog is enforced globally.

The migrated bindings use `/setup`, `/capture`, `/capture_ai`, `/flush`, `/get_feature_flag` and `/reload_feature_flags`. Their argument objects appear directly in feature doc strings or named step bindings. Additional shared public operations can be added with concrete scenarios; object references, callback continuations and private fixture-control endpoints are not part of this draft.

## Server identify and alias delivery

Run `--acceptance-suite` with a server adapter profile to select the opted-in
server identify and alias cases from the acceptance features. This includes the
identify call without an explicit id; it checks a received personless `$identify`
with a UUID-shaped root distinct id. The receiver assertion requires
`properties.$process_person_profile: false` on legacy batch delivery or
`options.process_person_profile: false` (without the legacy property) on Capture v1.
Unmigrated client and validation cases remain
unselected. The 157-case YAML-parity suite uses the same Gherkin runner but retains
its independent capability-based selection.
The steps `identify is called with JSON arguments:` and
`alias is called with JSON arguments:` forward JSON doc strings unchanged to the
negotiated `/identify` and `/alias` routes:

- `/identify`: `{ "distinct_id": "user-123", "set": { "active": false, "score": 0, "note": null } }`
- `/alias`: `{ "distinct_id": "anon-123", "alias": "user-123" }`

Adapters translate these arguments to their public SDK methods. Unexpected operation
throws fail the scenario. After public setup, operation and explicit flush, received
events must have the exact event name (`$identify` or `$create_alias`), root
`distinct_id`, and `$set` or `alias` property. For alias, root `distinct_id` is the
previous identity and `properties.alias` is the target. JSON property assertions
preserve boolean, numeric and null types and require the property to be present.
These delivery cases require no private queue observations or identity persistence
controls and do not establish broader client-side identity behavior.

## Black-box local evaluation

A fresh receiver loads controlled definitions through the mock definitions service using public SDK configuration. Scenarios call the local-only getter directly after initialization and compare conclusive results with exact expectations, recording authenticated HTTP 200 definitions fetched during setup or initial evaluation. When definitions change, scenarios explicitly reload through the public SDK method, require fresh authenticated HTTP 200 definitions within five seconds, and evaluate again. Different properties and changed definitions detect constants, defaults and stale reloads. Definitions downloads are allowed; `/flags` and `/decide` requests are forbidden throughout setup, loading, evaluation and public cleanup. No private evaluator observation is required.

## Isolation, networks and results

Every case receives a distinct ephemeral mock URL. Retired listeners remain bound until run teardown and reject late requests, so traffic cannot enter a later case. Configure `--mock-bind-host` and `--mock-advertised-host` independently for separate containers. `--allow-private-network` permits an operator-selected adapter DNS/IP address; it does not prove privacy. Use separate network namespaces, an internal network, and no published host ports. Bare IPv6 addresses are accepted and bracketed in constructed URLs.

The CLI writes a report and `<report>.diagnostics.json` with actual calls, traffic, source identities, selection and failures. Each failed case in the diagnostics includes `failure` with its code, message, failing step source and step text. Local and remote flag getter comparisons also include `details` with the public operation, scenario arguments, expected value and actual value or non-value outcome. Flags request field comparisons include the request path, field path, expected value and actual value; present values use `{"kind":"value","value":...}`, while absent fields use `{"kind":"missing"}` and non-value getters retain their SDK outcome (for example `{"kind":"undefined"}`). The outer tag distinguishes JSON null and objects shaped like these markers from missing fields or non-value outcomes. These diagnostic details supplement the strict report without changing its schema or assertion decisions. Consumers can link the failing step to the specs checkout using the clean `distribution.source.commit` and the step's source path/line; `source.revision` is a file-content hash, not a Git commit. Keep CI comments bounded and show only relevant diagnostic fields rather than entire request bodies. Exit 0 requires at least one passed case and no selected failures or infrastructure errors; exit 1 means failed/empty scope; exit 2 means malformed report or CLI/input failure. SDK callers should independently run `posthog-test-harness-v2 check-report --report report.json --profile PROFILE` after execution. It applies the same strict result gate and verifies matching run, profile and case identities in the diagnostics. Missing/malformed artifacts or startup errors cannot pass; callers must also preserve the original process exit and bound their own subprocess/cleanup commands. Keep artifacts outside the repository. Controlled test adapters validate the harness, not SDK conformance. Publication, production caller enablement and v1 retirement are separate release decisions.
