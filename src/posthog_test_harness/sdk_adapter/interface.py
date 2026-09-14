"""SDK adapter interface definition."""

from abc import ABC, abstractmethod
from typing import Dict

from ..types import CaptureRequest, FeatureFlagRequest, HealthResponse, InitRequest, StateResponse


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

    async def capture_ai(self, event: CaptureRequest) -> Dict[str, any]:
        """
        Capture an event on the dedicated AI capture endpoint.

        Optional: only required for adapters that advertise the
        `capture_ai_v0` capability. Override this method to support it.

        Args:
            event: Event to capture

        Returns:
            Dict with {"success": True, "uuid": "..."} on success
        """
        raise NotImplementedError("capture_ai is optional; implement it to support the capture_ai_v0 capability")

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
    async def get_feature_flag(self, request: FeatureFlagRequest) -> Dict:
        """
        Evaluate a feature flag.

        Args:
            request: Feature flag evaluation request

        Returns:
            Dict with feature flag evaluation result
        """
        pass

    async def reload_feature_flags(self) -> Dict:
        """Load client flags through the public SDK and await its completion callback.

        Optional, for client_feature_flags. Uses the initialized identity and
        context; does not identify, evaluate a key, or emit a called-event.
        """
        raise NotImplementedError("reload_feature_flags requires client_feature_flags")

    async def get_cached_feature_flag(self, key: str) -> Dict:
        """Read the public client cache without a reload or context mutation.

        Optional, for client_feature_flags. Preserve the SDK's called-event.
        """
        raise NotImplementedError("get_cached_feature_flag requires client_feature_flags")

    async def reload_feature_flag_definitions(self, timeout_ms: int = 5000) -> Dict[str, bool]:
        """Force a fresh definitions load and await readiness within timeout_ms.

        Optional, only for feature_flags_local_evaluation_v1. Return
        {"success": True, "ready": True} only after the fresh snapshot is usable.
        """
        raise NotImplementedError("reload_feature_flag_definitions requires feature_flags_local_evaluation_v1")

    @abstractmethod
    async def reset(self) -> Dict[str, bool]:
        """
        Reset SDK state.

        Returns:
            Dict with {"success": True} on success
        """
        pass
