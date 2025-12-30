"""SDK adapter interface definition."""

from abc import ABC, abstractmethod
from typing import Dict, Optional

from ..types import CaptureRequest, HealthResponse, InitRequest, StateResponse


class SDKAdapterInterface(ABC):
    """Interface that all SDK adapters must implement."""

    @abstractmethod
    async def health(self) -> HealthResponse:
        """
        Get SDK adapter health information.

        Returns:
            HealthResponse with SDK name, version, and adapter version
        """
        pass

    @abstractmethod
    async def init(self, config: InitRequest) -> Dict[str, bool]:
        """
        Initialize the SDK with the given configuration.

        Args:
            config: SDK initialization configuration

        Returns:
            Dict with {"success": True} on success
        """
        pass

    @abstractmethod
    async def capture(self, event: CaptureRequest) -> Dict[str, any]:
        """
        Capture an event.

        Args:
            event: Event to capture

        Returns:
            Dict with {"success": True, "uuid": "..."} on success
        """
        pass

    @abstractmethod
    async def flush(self) -> Dict[str, any]:
        """
        Flush all pending events.

        Returns:
            Dict with {"success": True, "events_flushed": N} on success
        """
        pass

    @abstractmethod
    async def get_state(self) -> StateResponse:
        """
        Get internal SDK state.

        Returns:
            StateResponse with SDK internal state
        """
        pass

    @abstractmethod
    async def reset(self) -> Dict[str, bool]:
        """
        Reset SDK state.

        Returns:
            Dict with {"success": True} on success
        """
        pass
