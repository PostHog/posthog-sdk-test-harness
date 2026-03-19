"""Decide endpoint handler for feature flag evaluation."""

from typing import Any, Callable, Dict, List, Tuple

from flask import Request

from .base import EndpointHandler


class DecideEndpoint(EndpointHandler):
    """Handles feature flag decide endpoints."""

    def routes(self) -> List[Tuple[str, str, Callable]]:
        """Return all decide endpoint routes."""
        handler = self.handle_request

        return [
            ("/decide", "POST", handler),
            ("/decide/", "POST", handler),
        ]

    def handle_request(self, request: Request) -> Tuple[Dict[str, Any], int, Dict[str, str]]:
        """
        Handle a decide request.

        Returns a default feature flags response.
        """
        return {
            "featureFlags": {},
            "featureFlagPayloads": {},
            "errorsWhileComputingFlags": False,
        }, 200, {}
