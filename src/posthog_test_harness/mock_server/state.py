"""Mock server state management."""

import gzip
import json
import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from ..types import MockResponse, RecordedRequest

logger = logging.getLogger(__name__)

TEST_ID_HEADER = "x-test-id"


class MockServerState:
    """Manages mock server state including request recording and response configuration.

    Supports optional partitioning by test_id for parallel test execution.
    When test_id is None, uses global (unpartitioned) state identical to the
    original behavior. When test_id is provided, state is isolated per test.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Global state (used when test_id is None)
        self._requests: List[RecordedRequest] = []
        self._response_queue: Deque[MockResponse] = deque()
        self._default_response = MockResponse()

        # Partitioned state (used when test_id is provided)
        self._partitioned_requests: Dict[str, List[RecordedRequest]] = {}
        self._partitioned_response_queue: Dict[str, Deque[MockResponse]] = {}
        self._partitioned_default_response: Dict[str, MockResponse] = {}

    def _ensure_partition(self, test_id: str) -> None:
        """Ensure a partition exists for the given test_id. Must be called with lock held."""
        if test_id not in self._partitioned_requests:
            self._partitioned_requests[test_id] = []
        if test_id not in self._partitioned_response_queue:
            self._partitioned_response_queue[test_id] = deque()
        if test_id not in self._partitioned_default_response:
            self._partitioned_default_response[test_id] = MockResponse()

    def record_request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body_raw: bytes,
    ) -> RecordedRequest:
        """Record an incoming request and return the response to send.

        The test_id is extracted from the X-Test-Id header. When present,
        the request is recorded in the partition for that test_id and the
        response is taken from that partition's queue. When absent, uses
        global state.
        """
        with self._lock:
            # Extract test_id from headers (already lowercased by server.py)
            test_id = headers.get(TEST_ID_HEADER)

            # Decompress body if needed
            body_decompressed = None
            if headers.get("content-encoding") == "gzip":
                try:
                    body_decompressed = gzip.decompress(body_raw).decode("utf-8")
                except Exception:
                    body_decompressed = None
            else:
                try:
                    body_decompressed = body_raw.decode("utf-8")
                except Exception:
                    body_decompressed = None

            # Try to parse events from body
            parsed_events = self._parse_events(body_decompressed)

            # Get response from the appropriate queue
            if test_id is not None:
                self._ensure_partition(test_id)
                if self._partitioned_response_queue[test_id]:
                    response_config = self._partitioned_response_queue[test_id].popleft()
                else:
                    response_config = self._partitioned_default_response[test_id]
            else:
                if self._response_queue:
                    response_config = self._response_queue.popleft()
                else:
                    response_config = self._default_response

            # Create recorded request
            request = RecordedRequest(
                timestamp_ms=int(time.time() * 1000),
                method=method,
                path=path,
                headers=dict(headers),
                query_params=dict(query_params),
                body_raw=body_raw,
                body_decompressed=body_decompressed,
                parsed_events=parsed_events,
                response_status=response_config.status_code,
                response_headers=dict(response_config.headers),
                response_body=response_config.body,
            )

            # Store in the appropriate partition
            if test_id is not None:
                self._partitioned_requests[test_id].append(request)
            else:
                self._requests.append(request)

            return request

    def _parse_events(self, body_decompressed: Optional[str]) -> Optional[List[Dict]]:
        """Parse events from a decompressed request body."""
        if not body_decompressed:
            return None

        try:
            data = json.loads(body_decompressed)
            logger.debug(
                f"Parsed body: type={type(data).__name__}, "
                f"keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}"
            )

            # Handle multiple request formats (matches Rust capture service)
            if isinstance(data, list):
                # Array format (posthog-js batched): [{event1}, {event2}, ...]
                parsed_events = data
                logger.debug(f"Array of {len(parsed_events)} events")
                if parsed_events:
                    first_event = parsed_events[0]
                    logger.debug(f"First event keys: {list(first_event.keys())}")
                    logger.debug(f"First event has properties: {'properties' in first_event}")
                    props = first_event.get("properties")
                    logger.debug(f"Properties value: {props}")
                    logger.debug(f"Properties type: {type(props).__name__ if props else 'None'}")
                    if props and isinstance(props, dict):
                        logger.debug(f"Properties keys: {list(props.keys())[:5]}")
                        logger.debug(f"Properties has $distinct_id: {'$distinct_id' in props}")
                        logger.debug(f"Properties.$distinct_id: {props.get('$distinct_id')}")
                return parsed_events
            elif isinstance(data, dict):
                if "batch" in data:
                    # Batch format (server SDKs): {"api_key": "...", "batch": [...]}
                    parsed_events = data["batch"]
                    logger.debug(f"Found batch with {len(parsed_events)} events")
                    return parsed_events
                elif "data" in data and isinstance(data["data"], list):
                    # Data array format (if SDK wraps in data key): {"data": [...]}
                    parsed_events = data["data"]
                    logger.debug(f"Found data array with {len(parsed_events)} events")
                    return parsed_events
                else:
                    # Single event format: {event: "...", properties: {...}}
                    logger.debug("Single event format")
                    return [data]
        except Exception as e:
            logger.warning(f"Failed to parse events from body: {e}")
            logger.debug(f"Body preview: {body_decompressed[:200]}")

        return None

    def get_requests(self, test_id: Optional[str] = None) -> List[RecordedRequest]:
        """Get all recorded requests, optionally filtered by test_id."""
        with self._lock:
            if test_id is not None:
                return list(self._partitioned_requests.get(test_id, []))
            return list(self._requests)

    def clear_requests(self, test_id: Optional[str] = None) -> None:
        """Clear recorded requests, optionally for a specific test_id."""
        with self._lock:
            if test_id is not None:
                if test_id in self._partitioned_requests:
                    self._partitioned_requests[test_id].clear()
            else:
                self._requests.clear()

    def set_response_queue(self, responses: List[MockResponse], test_id: Optional[str] = None) -> None:
        """Set a queue of responses to return for subsequent requests."""
        with self._lock:
            if test_id is not None:
                self._ensure_partition(test_id)
                self._partitioned_response_queue[test_id] = deque(responses)
            else:
                self._response_queue = deque(responses)

    def set_default_response(self, response: MockResponse, test_id: Optional[str] = None) -> None:
        """Set the default response when the queue is empty."""
        with self._lock:
            if test_id is not None:
                self._ensure_partition(test_id)
                self._partitioned_default_response[test_id] = response
            else:
                self._default_response = response

    def reset(self, test_id: Optional[str] = None) -> None:
        """Reset state. When test_id is provided, only resets that partition."""
        with self._lock:
            if test_id is not None:
                self._partitioned_requests.pop(test_id, None)
                self._partitioned_response_queue.pop(test_id, None)
                self._partitioned_default_response.pop(test_id, None)
            else:
                self._requests.clear()
                self._response_queue.clear()
                self._default_response = MockResponse()
