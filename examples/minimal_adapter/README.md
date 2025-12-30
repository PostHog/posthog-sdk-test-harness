# Minimal Adapter Example

This is a more complete example SDK adapter that implements:
- Event queueing and batching
- Retry logic with exponential backoff
- Proper state tracking
- Auto-flush when queue is full

## Features

### ✓ Implemented
- Event queueing (events are batched before sending)
- Auto-flush when `flush_at` events are queued
- Retry logic for 500/502/503/429 errors
- Exponential backoff (2^retry_attempt seconds)
- No retry on 400/401 errors
- UUID generation and preservation on retry
- Full state tracking

### ✗ Not Implemented
- Background flush thread (events only flush on `/flush` or when queue is full)
- Respect for Retry-After headers
- Compression support

## Running the Adapter

### Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run the adapter
python adapter.py
```

The adapter will start on port 8080.

## Testing with the Harness

```bash
# Terminal 1: Start the adapter
python adapter.py

# Terminal 2: Run the test harness
posthog-test-harness run --adapter-url http://localhost:8080
```

## Expected Test Results

This adapter should **pass most tests**:

### ✓ Will Pass
- All format validation tests
- Most retry tests (retries on 503, doesn't retry on 400/401, implements backoff)
- All deduplication tests (UUIDs are unique and preserved on retry)

### ⚠️ May Fail
- `respects_retry_after_header` - Not implemented in this minimal example

## Implementation Highlights

### Event Queueing

```python
# Events are queued, not sent immediately
state.queue.put(event)

# Auto-flush when queue reaches flush_at size
if state.queue.qsize() >= state.flush_at:
    state.flush()
```

### Retry with Backoff

```python
if response.status_code in [500, 502, 503, 429]:
    if retry_attempt < self.max_retries:
        # Exponential backoff: 1s, 2s, 4s, ...
        delay = 2 ** retry_attempt
        time.sleep(delay)
        return self.send_batch(events, retry_attempt + 1)
```

### UUID Preservation

```python
# UUID is generated once when event is created
self.uuid = str(uuid.uuid4())

# Same UUID is used on all retry attempts
```

## Using This as a Template

This adapter is a good starting point for implementing your own SDK adapter:

1. Copy this file
2. Replace the `send_batch` method with your SDK's API
3. Add any SDK-specific configuration
4. Implement additional features (background flushing, Retry-After headers, etc.)

For a production adapter, see the `posthog-python` SDK adapter implementation.
