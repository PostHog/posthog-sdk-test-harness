#!/usr/bin/env python3
"""
Minimal SDK adapter example.

This demonstrates a more complete adapter implementation with:
- Event queueing
- Background flushing
- Basic retry logic
- Proper state tracking
"""

import os
import queue
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)


class Event:
    """Represents a single event."""

    def __init__(
        self,
        event: str,
        distinct_id: str,
        properties: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ):
        self.event = event
        self.distinct_id = distinct_id
        self.properties = properties or {}
        self.timestamp = timestamp
        self.uuid = str(uuid.uuid4())


class SDKState:
    """Tracks SDK state."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.api_key: Optional[str] = None
        self.host: Optional[str] = None
        self.flush_at = 100
        self.max_retries = 3

        self.queue: queue.Queue[Event] = queue.Queue()
        self.total_events_captured = 0
        self.total_events_sent = 0
        self.total_retries = 0
        self.last_error: Optional[str] = None
        self.requests_made: List[Dict[str, Any]] = []

        self.flush_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

    def reset(self) -> None:
        """Reset all state."""
        # Stop flush thread if running
        if self.flush_thread and self.flush_thread.is_alive():
            self.stop_event.set()
            self.flush_thread.join(timeout=2)

        with self.lock:
            self.api_key = None
            self.host = None
            self.flush_at = 100
            self.max_retries = 3
            self.queue = queue.Queue()
            self.total_events_captured = 0
            self.total_events_sent = 0
            self.total_retries = 0
            self.last_error = None
            self.requests_made = []
            self.stop_event.clear()

    def send_batch(self, events: List[Event], retry_attempt: int = 0) -> bool:
        """
        Send a batch of events.

        Returns True if successful, False otherwise.
        """
        if not self.api_key or not self.host:
            return False

        # Build batch payload
        batch = {
            "api_key": self.api_key,
            "batch": [
                {
                    "event": e.event,
                    "distinct_id": e.distinct_id,
                    "properties": {
                        **e.properties,
                        "$lib": "minimal-adapter",
                        "$lib_version": "1.0.0",
                    },
                    "timestamp": e.timestamp,
                    "uuid": e.uuid,
                }
                for e in events
            ],
        }

        # Send request
        try:
            response = requests.post(
                f"{self.host}/batch",
                json=batch,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            # Record request
            with self.lock:
                self.requests_made.append(
                    {
                        "timestamp_ms": int(time.time() * 1000),
                        "status_code": response.status_code,
                        "retry_attempt": retry_attempt,
                        "event_count": len(events),
                        "uuid_list": [e.uuid for e in events],
                    }
                )

            if response.status_code == 200:
                with self.lock:
                    self.total_events_sent += len(events)
                return True

            # Check if we should retry
            if response.status_code in [500, 502, 503, 429]:
                # Retry on server errors
                if retry_attempt < self.max_retries:
                    # Check for Retry-After header
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = int(retry_after)
                        except ValueError:
                            # If not an integer, use exponential backoff
                            delay = 2**retry_attempt
                    else:
                        # Exponential backoff
                        delay = 2**retry_attempt

                    time.sleep(delay)

                    with self.lock:
                        self.total_retries += 1

                    return self.send_batch(events, retry_attempt + 1)

            # Non-retryable error
            with self.lock:
                self.last_error = f"HTTP {response.status_code}: {response.text}"

            return False

        except Exception as e:
            with self.lock:
                self.last_error = str(e)
            return False

    def flush(self) -> int:
        """Flush all pending events. Returns number of events flushed."""
        events_to_send = []

        # Get all queued events
        while not self.queue.empty():
            try:
                events_to_send.append(self.queue.get_nowait())
            except queue.Empty:
                break

        if not events_to_send:
            return 0

        # Send in batches
        batch_size = self.flush_at
        total_sent = 0

        for i in range(0, len(events_to_send), batch_size):
            batch = events_to_send[i : i + batch_size]
            if self.send_batch(batch):
                total_sent += len(batch)

        return total_sent


# Global state
state = SDKState()


@app.route("/health", methods=["GET"])
def health() -> Any:
    """Health check endpoint."""
    return jsonify(
        {
            "sdk_name": "minimal-adapter",
            "sdk_version": "1.0.0",
            "adapter_version": "1.0.0",
        }
    )


@app.route("/init", methods=["POST"])
def init() -> Any:
    """Initialize the SDK."""
    try:
        data = request.json or {}

        state.reset()

        state.api_key = data.get("api_key")
        state.host = data.get("host")
        state.flush_at = data.get("flush_at", 100)
        state.max_retries = data.get("max_retries", 3)

        print(f"Initialized with flush_at={state.flush_at}, max_retries={state.max_retries}", file=sys.stderr)

        if not state.api_key or not state.host:
            return jsonify({"error": "api_key and host are required"}), 400

        return jsonify({"success": True})
    except Exception as e:
        import traceback

        print(f"Error in /init: {e}", file=sys.stderr)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/capture", methods=["POST"])
def capture() -> Any:
    """Capture an event."""
    try:
        if not state.api_key or not state.host:
            return jsonify({"error": "SDK not initialized"}), 400

        data = request.json or {}
        distinct_id = data.get("distinct_id")
        event_name = data.get("event")
        properties = data.get("properties", {})
        timestamp = data.get("timestamp")

        if not distinct_id or not event_name:
            return jsonify({"error": "distinct_id and event are required"}), 400

        # Create event
        event = Event(event_name, distinct_id, properties, timestamp)

        # Add to queue
        state.queue.put(event)

        with state.lock:
            state.total_events_captured += 1

        # Auto-flush if queue is full
        if state.queue.qsize() >= state.flush_at:
            state.flush()

        return jsonify({"success": True, "uuid": event.uuid})
    except Exception as e:
        import traceback

        print(f"Error in /capture: {e}", file=sys.stderr)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/flush", methods=["POST"])
def flush() -> Any:
    """Flush all pending events."""
    try:
        events_flushed = state.flush()
        return jsonify({"success": True, "events_flushed": events_flushed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/state", methods=["GET"])
def get_state() -> Any:
    """Get internal SDK state."""
    try:
        with state.lock:
            return jsonify(
                {
                    "pending_events": state.queue.qsize(),
                    "total_events_captured": state.total_events_captured,
                    "total_events_sent": state.total_events_sent,
                    "total_retries": state.total_retries,
                    "last_error": state.last_error,
                    "requests_made": state.requests_made,
                }
            )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset() -> Any:
    """Reset SDK state."""
    try:
        state.reset()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def main() -> None:
    """Main entry point."""
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting minimal adapter on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    main()
