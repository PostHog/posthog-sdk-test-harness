"""Capture endpoint handler."""

from typing import Any, Callable, Dict, List, Tuple

from flask import Request

from .base import EndpointHandler


class CaptureEndpoint(EndpointHandler):
    """Handles event capture endpoints."""

    def routes(self) -> List[Tuple[str, str, Callable]]:
        """Return all capture endpoint routes."""
        handler = self.handle_request
        v1_handler = self.handle_v1_request

        return [
            # V1 capture endpoint
            ("/i/v1/e", "POST", v1_handler),
            ("/i/v1/e/", "POST", v1_handler),
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

        Returns 200 with empty results array for full batch success.
        """
        return {"results": []}, 200, {}
