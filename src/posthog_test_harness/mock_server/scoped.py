"""Scoped mock server state for parallel test execution."""

from typing import List

from ..types import MockResponse, RecordedRequest
from .state import MockServerState


class ScopedMockServerState:
    """Wraps MockServerState and scopes all operations to a specific test_id.

    Actions call the same methods (get_requests, set_response_queue, etc.)
    without knowing about test_id -- the scoping is transparent.
    """

    def __init__(self, state: MockServerState, test_id: str) -> None:
        self._state = state
        self._test_id = test_id

    def get_requests(self) -> List[RecordedRequest]:
        return self._state.get_requests(test_id=self._test_id)

    def clear_requests(self) -> None:
        self._state.clear_requests(test_id=self._test_id)

    def set_response_queue(self, responses: List[MockResponse]) -> None:
        self._state.set_response_queue(responses, test_id=self._test_id)

    def set_default_response(self, response: MockResponse) -> None:
        self._state.set_default_response(response, test_id=self._test_id)

    def reset(self) -> None:
        self._state.reset(test_id=self._test_id)
