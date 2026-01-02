"""Mock server state management."""

import gzip
import json
import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, List

from ..types import MockResponse, RecordedRequest

logger = logging.getLogger(__name__)


class MockServerState:
    """Manages mock server state including request recording and response configuration."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: List[RecordedRequest] = []
        self._response_queue: Deque[MockResponse] = deque()
        self._default_response = MockResponse()

    def record_request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body_raw: bytes,
    ) -> RecordedRequest:
        """
        Record an incoming request and return the response to send.

        Args:
            method: HTTP method
            path: Request path
            headers: Request headers
            query_params: Query parameters
            body_raw: Raw request body

        Returns:
            RecordedRequest with the response that was sent
        """
        with self._lock:
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
            parsed_events = None
            if body_decompressed:
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
                            props = first_event.get('properties')
                            logger.debug(f"Properties value: {props}")
                            logger.debug(f"Properties type: {type(props).__name__ if props else 'None'}")
                            if props and isinstance(props, dict):
                                logger.debug(f"Properties keys: {list(props.keys())[:5]}")
                                logger.debug(f"Properties has $distinct_id: {'$distinct_id' in props}")
                                logger.debug(f"Properties.$distinct_id: {props.get('$distinct_id')}")
                    elif isinstance(data, dict):
                        if "batch" in data:
                            # Batch format (server SDKs): {"api_key": "...", "batch": [...]}
                            parsed_events = data["batch"]
                            logger.debug(f"Found batch with {len(parsed_events)} events")
                        elif "data" in data and isinstance(data["data"], list):
                            # Data array format (if SDK wraps in data key): {"data": [...]}
                            parsed_events = data["data"]
                            logger.debug(f"Found data array with {len(parsed_events)} events")
                        else:
                            # Single event format: {event: "...", properties: {...}}
                            parsed_events = [data]
                            logger.debug("Single event format")
                except Exception as e:
                    logger.warning(f"Failed to parse events from body: {e}")
                    logger.debug(f"Body preview: {body_decompressed[:200]}")
            else:
                logger.debug(f"No body_decompressed (body_raw length={len(body_raw)})")

            # Get next response from queue or use default
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

            self._requests.append(request)
            return request

    def get_requests(self) -> List[RecordedRequest]:
        """Get all recorded requests."""
        with self._lock:
            return list(self._requests)

    def clear_requests(self) -> None:
        """Clear all recorded requests."""
        with self._lock:
            self._requests.clear()

    def set_response_queue(self, responses: List[MockResponse]) -> None:
        """
        Set a queue of responses to return for subsequent requests.

        Args:
            responses: List of MockResponse objects to return in order
        """
        with self._lock:
            self._response_queue = deque(responses)

    def set_default_response(self, response: MockResponse) -> None:
        """
        Set the default response to return when the queue is empty.

        Args:
            response: MockResponse to use as default
        """
        with self._lock:
            self._default_response = response

    def reset(self) -> None:
        """Reset all state."""
        with self._lock:
            self._requests.clear()
            self._response_queue.clear()
            self._default_response = MockResponse()
