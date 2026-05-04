#!/usr/bin/env python3
"""
Parallel-capable SDK adapter example.

Extends the minimal adapter with support for parallel test execution:
- Per-test-id SDK instance isolation
- X-Test-Id header injection into outbound requests
- Declares supports_parallel: true in /health
"""

import os
import queue
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests as http_requests
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
    """Tracks SDK state for a single test instance."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.api_key: Optional[str] = None
        self.host: Optional[str] = None
        self.flush_at = 100
        self.max_retries = 3
        self.extra_headers: Dict[str, str] = {}

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
        if self.flush_thread and self.flush_thread.is_alive():
            self.stop_event.set()
            self.flush_thread.join(timeout=2)

        with self.lock:
            self.api_key = None
            self.host = None
            self.flush_at = 100
            self.max_retries = 3
            self.extra_headers = {}
            self.queue = queue.Queue()
            self.total_events_captured = 0
            self.total_events_sent = 0
            self.total_retries = 0
            self.last_error = None
            self.requests_made = []
            self.stop_event.clear()

    def send_batch(self, events: List[Event], retry_attempt: int = 0) -> bool:
        """Send a batch of events. Returns True if successful."""
        if not self.api_key or not self.host:
            return False

        batch = {
            "api_key": self.api_key,
            "batch": [
                {
                    "event": e.event,
                    "distinct_id": e.distinct_id,
                    "properties": {
                        **e.properties,
                        "$lib": "parallel-adapter",
                        "$lib_version": "1.0.0",
                    },
                    "timestamp": e.timestamp,
                    "uuid": e.uuid,
                }
                for e in events
            ],
        }

        headers = {"Content-Type": "application/json", **self.extra_headers}

        try:
            response = http_requests.post(
                f"{self.host}/batch",
                json=batch,
                headers=headers,
                timeout=10,
            )

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

            if response.status_code in [500, 502, 503, 429]:
                if retry_attempt < self.max_retries:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = int(retry_after)
                        except ValueError:
                            delay = 2**retry_attempt
                    else:
                        delay = 2**retry_attempt

                    time.sleep(delay)

                    with self.lock:
                        self.total_retries += 1

                    return self.send_batch(events, retry_attempt + 1)

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

        while not self.queue.empty():
            try:
                events_to_send.append(self.queue.get_nowait())
            except queue.Empty:
                break

        if not events_to_send:
            return 0

        batch_size = self.flush_at
        total_sent = 0

        for i in range(0, len(events_to_send), batch_size):
            batch = events_to_send[i : i + batch_size]
            if self.send_batch(batch):
                total_sent += len(batch)

        return total_sent


# Global instance (used when no test_id is provided)
global_instance = SDKState()

# Per-test-id instances
test_instances: Dict[str, SDKState] = {}
test_instances_lock = threading.Lock()


def get_instance() -> SDKState:
    """Get the SDK instance for the current request.

    If ?test_id= is present, returns (or creates) the instance for that test.
    Otherwise returns the global instance.
    """
    test_id = request.args.get("test_id")
    if test_id is None:
        return global_instance

    with test_instances_lock:
        if test_id not in test_instances:
            test_instances[test_id] = SDKState()
        return test_instances[test_id]


@app.route("/health", methods=["GET"])
def health() -> Any:
    """Health check endpoint. Declares parallel support."""
    return jsonify(
        {
            "sdk_name": "parallel-adapter",
            "sdk_version": "1.0.0",
            "adapter_version": "1.0.0",
            "supports_parallel": True,
        }
    )


@app.route("/init", methods=["POST"])
def init() -> Any:
    """Initialize the SDK instance."""
    try:
        data = request.json or {}
        instance = get_instance()

        instance.reset()

        instance.api_key = data.get("api_key")
        instance.host = data.get("host")
        instance.flush_at = data.get("flush_at", 100)
        instance.max_retries = data.get("max_retries", 3)

        # If running under a test_id, inject X-Test-Id header into outbound requests
        test_id = request.args.get("test_id")
        if test_id:
            instance.extra_headers = {"X-Test-Id": test_id}

        print(
            f"Initialized (test_id={test_id}) with flush_at={instance.flush_at}, "
            f"max_retries={instance.max_retries}",
            file=sys.stderr,
        )

        if not instance.api_key or not instance.host:
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
        instance = get_instance()

        if not instance.api_key or not instance.host:
            return jsonify({"error": "SDK not initialized"}), 400

        data = request.json or {}
        distinct_id = data.get("distinct_id")
        event_name = data.get("event")
        properties = data.get("properties", {})
        timestamp = data.get("timestamp")

        if not distinct_id or not event_name:
            return jsonify({"error": "distinct_id and event are required"}), 400

        event = Event(event_name, distinct_id, properties, timestamp)
        instance.queue.put(event)

        with instance.lock:
            instance.total_events_captured += 1

        if instance.queue.qsize() >= instance.flush_at:
            instance.flush()

        return jsonify({"success": True, "uuid": event.uuid})
    except Exception as e:
        import traceback

        print(f"Error in /capture: {e}", file=sys.stderr)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/get_feature_flag", methods=["POST"])
def get_feature_flag() -> Any:
    """Evaluate a feature flag."""
    try:
        instance = get_instance()

        if not instance.api_key or not instance.host:
            return jsonify({"error": "SDK not initialized"}), 400

        data = request.json or {}
        key = data.get("key")
        distinct_id = data.get("distinct_id")

        if not key or not distinct_id:
            return jsonify({"error": "key and distinct_id are required"}), 400

        person_properties = data.get("person_properties", {})
        # Auto-add distinct_id to person_properties (matches real SDK behavior)
        person_properties["distinct_id"] = distinct_id

        # This example adapter always evaluates remotely, so it intentionally ignores
        # the optional force_remote adapter hint.

        payload = {
            "token": instance.api_key,
            "distinct_id": distinct_id,
            "person_properties": person_properties,
            "groups": data.get("groups", {}),
            "group_properties": data.get("group_properties", {}),
            "geoip_disable": data.get("disable_geoip", False),
            "flag_keys_to_evaluate": [key],
        }

        headers = {"Content-Type": "application/json", **instance.extra_headers}

        response = http_requests.post(
            f"{instance.host}/flags",
            params={"v": "2"},
            json=payload,
            headers=headers,
            timeout=10,
        )

        result = response.json()
        flag_value = result.get("featureFlags", {}).get(key)

        # Capture the documented $feature_flag_called side-effect event.
        instance.queue.put(
            Event(
                "$feature_flag_called",
                distinct_id,
                {
                    "$feature_flag": key,
                    "$feature_flag_response": flag_value,
                },
            )
        )
        with instance.lock:
            instance.total_events_captured += 1

        return jsonify({"success": True, "value": flag_value})
    except Exception as e:
        import traceback

        print(f"Error in /get_feature_flag: {e}", file=sys.stderr)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/flush", methods=["POST"])
def flush() -> Any:
    """Flush all pending events."""
    try:
        instance = get_instance()
        events_flushed = instance.flush()
        return jsonify({"success": True, "events_flushed": events_flushed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/state", methods=["GET"])
def get_state() -> Any:
    """Get internal SDK state."""
    try:
        instance = get_instance()
        with instance.lock:
            return jsonify(
                {
                    "pending_events": instance.queue.qsize(),
                    "total_events_captured": instance.total_events_captured,
                    "total_events_sent": instance.total_events_sent,
                    "total_retries": instance.total_retries,
                    "last_error": instance.last_error,
                    "requests_made": instance.requests_made,
                }
            )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset() -> Any:
    """Reset SDK state."""
    try:
        test_id = request.args.get("test_id")
        if test_id:
            with test_instances_lock:
                if test_id in test_instances:
                    test_instances[test_id].reset()
                    del test_instances[test_id]
        else:
            global_instance.reset()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def main() -> None:
    """Main entry point."""
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting parallel adapter on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    main()
