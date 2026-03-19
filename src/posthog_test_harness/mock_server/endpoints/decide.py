"""Flags endpoint handler for feature flag evaluation."""

from typing import Any, Callable, Dict, List, Tuple

from flask import Request

from .base import EndpointHandler


class FlagsEndpoint(EndpointHandler):
    """Handles feature flag endpoints."""

    def routes(self) -> List[Tuple[str, str, Callable]]:
        """Return all flags endpoint routes."""
        handler = self.handle_request

        return [
            ("/flags", "POST", handler),
            ("/flags/", "POST", handler),
        ]

    def handle_request(self, request: Request) -> Tuple[Dict[str, Any], int, Dict[str, str]]:
        """
        Handle a flags request.

        Returns a default feature flags response.
        """
        return {
            "featureFlags": {},
            "featureFlagPayloads": {},
            "errorsWhileComputingFlags": False,
        }, 200, {}
