# Gherkin compliance harness

The opt-in `posthog-test-harness-v2` entry point runs SDK-independent Gherkin over HTTP. The SDK repository owns its native adapter, package build, and compliance caller. The harness owns mock services, input selection, assertions, deadlines, and reports. Existing YAML/v1 commands remain available.

## Run

```sh
posthog-test-harness-v2 discover --specs /path/to/sdk-specs \
  --migration-suite --require-ready --report discovery.json
posthog-test-harness-v2 run --specs /path/to/sdk-specs \
  --migration-suite --adapter-url http://127.0.0.1:8080 \
  --profile PROFILE --timeout-ms 60000 --report report.json
```

Omit `--specs` to use the verified [packaged features](harness-v2-distribution.md). `--feature` selects relative feature paths; `--all-features` includes unresolved canonical cases. `--case-id` explicitly selects stable identities, retaining missing prerequisites as failures rather than exclusions.

The official Cucumber parser expands backgrounds, rules and outlines. Scenario data uses inline tables and JSON doc strings; no file-data binding or generated inventory is needed. Migrated scenarios use `@case:<complete-id>`, or `@case:<case_id>` with a `case_id` Examples column. `@requires:<capability>` declares SDK features; `@sdk:server` and `@sdk:client` restrict applicability. Capabilities select candidates; a missing required route does not exclude a declared capability. Required routes come from step bindings. Non-migrated cases without explicit identities use content digest, path, declaration line and example line.

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

`value` accepts JSON data including false, zero and null. Omitted arguments remain omitted. Native SDK throws are not transport errors, and scenario assertions determine whether a throw is acceptable. Adapters must pass representable invalid inputs to the SDK, faithfully translate native argument names, and avoid injected defaults, hidden flushes or corrective retries. Malformed requests, duplicate IDs and nonexistent fixtures produce non-200 JSON errors. The client retains bounded error detail. Request transport permits one second beyond the native deadline for a failure response; negotiation defaults to five seconds. No SDK result catalog is enforced globally.

The migrated bindings use `/setup`, `/capture`, `/capture_ai`, `/flush`, `/get_feature_flag`, `/reload_feature_flags` and `/wait_for_local_evaluation_ready`. Their argument objects appear directly in feature doc strings or named step bindings. Additional shared public operations can be added with concrete scenarios; object references, callback continuations and private fixture-control endpoints are not part of this draft.

## Black-box local evaluation

A fresh receiver loads controlled definitions through the mock definitions service using public SDK configuration. Scenarios explicitly reload and await readiness, require fresh authenticated HTTP 200 definitions within five seconds, then compare conclusive public getter results with exact expectations. Different properties and changed definitions detect constants, defaults and stale reloads. Definitions downloads are allowed; `/flags` and `/decide` requests are forbidden throughout setup, loading, evaluation and public cleanup. No private evaluator observation is required.

## Isolation, networks and results

Every case receives a distinct ephemeral mock URL. Retired listeners remain bound until run teardown and reject late requests, so traffic cannot enter a later case. Configure `--mock-bind-host` and `--mock-advertised-host` independently for separate containers. `--allow-private-network` permits an operator-selected adapter DNS/IP address; it does not prove privacy. Use separate network namespaces, an internal network, and no published host ports. Bare IPv6 addresses are accepted and bracketed in constructed URLs.

The CLI writes a report and `<report>.diagnostics.json` with actual calls, traffic, source identities, selection and failures. Exit 0 requires at least one passed case and no selected failures or infrastructure errors; exit 1 means failed/empty scope; exit 2 means malformed report or CLI/input failure. SDK callers should independently run `posthog-test-harness-v2 check-report --report report.json --profile PROFILE` after execution. It applies the same strict result gate and verifies matching run, profile and case identities in the diagnostics. Missing/malformed artifacts or startup errors cannot pass; callers must also preserve the original process exit and bound their own subprocess/cleanup commands. Keep artifacts outside the repository. Controlled test adapters validate the harness, not SDK conformance. Publication, production caller enablement and v1 retirement are separate release decisions.
