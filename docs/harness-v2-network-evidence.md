# V2 network deployment evidence

## Gate

Explicit cross-container networking is implemented and validated. Loopback remains
the default. This is a deployment increment, not full SDK conformance or CI cutover.

- Runner: `--mock-bind-host`, `--mock-advertised-host`, and
  `--allow-private-network` (for non-loopback adapter URLs).
- Node host: `--listen-host` and `--listen-port` (default `127.0.0.1`, port `0`).
- Hosts are validated independently from URLs; each mock service keeps its own
  OS-allocated port. Retired case URLs remain bound until run teardown.
- Adapter URLs retain HTTP/explicit-port restrictions, no credentials or non-root
  paths/query/fragment, no redirects, and no environment proxy use.
- The opt-in permits configured DNS/IP hosts; it does not establish network privacy.
  The smoke supplies an internal Docker network with no published host ports.

The companion `sdk-specs` contract (`contracts/v2/README.md` §2) permits explicitly opted-in
network deployment. Wire envelopes and catalog negotiation are unchanged.
Bundle identity changed because the normative documentation changed:

- Previous bundle: `714d0a35e52e3bf8dc4a91419e82b30a480119d7695435d69122e4c645587bb8`
- New bundle: `2ec6041e382bc83e86a615c93d6c2da8119917b24541aa1b91b218f795b0f6e2`
- Unchanged catalog: `c52ae7fac46f0395a78276bbc6c3b97ea83538005bcee11b60b2dc33879adfaa`

## Evidence

`scripts/smoke_v2_network.py` runs genuine SDK operations between containers in
separate network namespaces. It checks private DNS resolution, distinct namespaces,
absence of published ports, advertised case URLs, real ingestion, and CLI exits.
Readiness probes only open control-plane TCP connections; they do not invoke SDK
operations. The script cleans up its own containers and network on completion.

Runtime: Linux/arm64, Node **24.21.0**, Python **3.12.12**, installed `posthog-node`
**5.52.4** and `@posthog/core` **1.54.2**. This Linux Node version differs from the
earlier macOS calibration's 24.13.0; no performance comparison is claimed.

| Run | Result | Actual CLI exit |
| --- | --- | --- |
| v0 AI | 5 passed | 0 |
| v1 AI | 5 passed | 0 |
| v0 GeoIP default | Known `flag_request_field` assertion failure retained | 1 |
| v1 GeoIP default | Same known assertion failure retained | 1 |
| v1 deliberately wrong advertised loopback address | Invocation timeout / harness error; zero mock traffic | 1 |

The last row is a topology-negative control, not an additional SDK defect. The two
known local flag boolean-matching failures were not rerun or repaired in this slice.
All 151 native cases and the full harness pytest suite were not rerun.

- Focused validation: **300 harness tests** and **41 host tests** pass. Of the host
  tests, 35 are controlled and six use the real installed consumer.
- Independent validation: **140 focused tests** pass. The initial
  command omitted the required contracts environment variable, causing two setup
  failures; the configured rerun passed.
- Fresh read-only review found no issues. Independent inspection covered the
  implementation and receipts, but did not repeat the Docker smoke.
- Owned containers and network were removed. Existing workflow and SDK behavior
  were unchanged by this network increment.

Raw native reports, diagnostics, commands, topology and cleanup receipts are
retained locally, not distributed with this repository. These historical checks
used development snapshots; they do not validate a current-main merge. The later
[native result ledger](harness-v2-native-results.json) records source-built full
profile results using this bundle/catalog identity.

## Subsequent gates

[Source-built SDK integration](harness-v2-source-build-evidence.md) now exercises
the selected SDK checkout rather than the pinned calibration consumer. The
[opt-in shared workflow](harness-v2-node-ci.md) uses that build path and preserves
actual failing exits. Published immutable inputs, caller enablement and GitHub
execution remain pending; the development images are not published artifacts.
