"""Base endpoint handler interface."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Tuple

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
