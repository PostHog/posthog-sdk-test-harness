# Parallel Adapter Example

This adapter extends the minimal adapter with support for **parallel test execution**. It manages a separate SDK instance per `test_id`, allowing the test harness to run multiple tests concurrently with full isolation.

## What's Different from the Minimal Adapter

| Feature | Minimal Adapter | Parallel Adapter |
|---------|----------------|-----------------|
| `supports_parallel` | Not declared | `true` |
| Instance management | Single global | Per-test-id dict |
| `X-Test-Id` header | Not sent | Injected on all outbound requests |
| `?test_id=` query param | Ignored | Routes to correct instance |

## How Parallel Isolation Works

1. The harness sends `?test_id=t-<uuid>` on all adapter requests
2. The adapter creates a fresh `SDKState` per `test_id`
3. Each instance sends `X-Test-Id: <id>` with every request to the mock server
4. The mock server partitions recorded requests by this header
5. On `/reset?test_id=...`, only that instance is cleaned up

## Running

```bash
pip install -r requirements.txt
python adapter.py
```

## Testing with the Harness

```bash
# Terminal 1: Start the adapter
python adapter.py

# Terminal 2: Run the test harness (from the harness repo root)
uv run posthog-test-harness run --adapter-url http://localhost:8080 --concurrency 4
```

## Using as a Template

To add parallel support to your own adapter:

1. Return `"supports_parallel": true` in `/health`
2. Use `request.args.get("test_id")` to route requests to per-test instances
3. Inject `X-Test-Id: <test_id>` into all outbound SDK requests
4. Clean up instances on `/reset`

See the [Adapter Guide](../../ADAPTER_GUIDE.md#supporting-parallel-test-execution) for the full specification.
