"""Capture endpoint handler."""

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

from flask import Request

from .base import EndpointHandler

V1_CAPTURE_PATH = "/i/v1/events/analytics"
V1_CAPTURE_PATHS = {V1_CAPTURE_PATH, f"{V1_CAPTURE_PATH}/"}


def is_v1_capture_path(path: str) -> bool:
    return path in V1_CAPTURE_PATHS


class CaptureEndpoint(EndpointHandler):
    """Handles event capture endpoints."""

    def routes(self) -> List[Tuple[str, str, Callable]]:
        """Return all capture endpoint routes."""
        handler = self.handle_request
        v1_handler = self.handle_v1_request

        return [
            # V1 capture endpoint
            (V1_CAPTURE_PATH, "POST", v1_handler),
            (f"{V1_CAPTURE_PATH}/", "POST", v1_handler),
            # Batch endpoint
            ("/batch", "POST", handler),
            ("/batch/", "POST", handler),
            # Event endpoints
            ("/i/v0/e", "POST", handler),
            ("/i/v0/e/", "POST", handler),
            ("/i/v0/e", "GET", handler),
            ("/i/v0/e/", "GET", handler),
            ("/e", "POST", handler),
            ("/e/", "POST", handler),
            ("/e", "GET", handler),
            ("/e/", "GET", handler),
            ("/capture", "POST", handler),
            ("/capture/", "POST", handler),
            ("/capture", "GET", handler),
            ("/capture/", "GET", handler),
            ("/track", "POST", handler),
            ("/track/", "POST", handler),
            ("/track", "GET", handler),
            ("/track/", "GET", handler),
        ]

    def handle_request(self, request: Request) -> Tuple[Dict[str, Any], int, Dict[str, str]]:
        """
        Handle a legacy capture request.

        Returns success response with status 1.
        """
        is_beacon = request.args.get("beacon") == "1"

        if is_beacon:
            return {}, 204, {}

        return {"status": 1}, 200, {}

    def handle_v1_request(self, request: Request) -> Tuple[Dict[str, Any], int, Dict[str, str]]:
        """
        Handle a V1 capture request.

        Returns 200 with UUID-keyed results matching the real capture
        service response format.
        """
        headers: Dict[str, str] = {
            "Date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
        }
        req_id = request.headers.get("PostHog-Request-Id")
        if req_id:
            headers["PostHog-Request-Id"] = req_id

        results: Dict[str, Any] = {}
        try:
            data = json.loads(request.get_data(as_text=True))
            if isinstance(data, dict) and "batch" in data:
                for event in data["batch"]:
                    uuid = event.get("uuid", "")
                    if uuid:
                        results[uuid] = {"result": "ok"}
        except Exception:
            pass

        return {"results": results}, 200, headers
