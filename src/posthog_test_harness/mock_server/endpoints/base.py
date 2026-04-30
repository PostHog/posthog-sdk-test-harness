"""Base endpoint handler interface."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Request


class EndpointHandler(ABC):
    """Base class for endpoint handlers."""

    @abstractmethod
    def routes(self) -> List[Tuple[str, str, Callable]]:
        """
        Return list of (path, method, handler) tuples.

        Example:
            [
                ('/batch', 'POST', self.handle_batch),
                ('/batch/', 'POST', self.handle_batch),
            ]
        """
        pass

    @abstractmethod
    def handle_request(self, request: Request) -> Tuple[Dict[str, Any], int, Dict[str, str]]:
        """
        Handle a request and return (body_dict, status_code, headers).

        Args:
            request: Flask request object

        Returns:
            Tuple of (response_body_dict, status_code, response_headers)
        """
        pass

    def default_success_body(self, request: Request) -> Optional[Dict[str, Any]]:
        """
        Optional default success body that gets merged under a queue-configured body.

        When a test configures a `MockResponse(body=...)` and the response status is
        2xx, the configured body is overlaid on top of this default — so tests only
        need to spell out the fields they care about (e.g. `featureFlags`) and the
        constant boilerplate (e.g. `errorsWhileComputingFlags: False`) is filled in.

        Return None to opt out of merging (capture endpoints have no shared default).
        """
        return None
