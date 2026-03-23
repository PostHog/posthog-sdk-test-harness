"""HTTP client for SDK adapter."""

import asyncio
from typing import Any, Dict, Optional

import aiohttp

from ..types import CaptureRequest, FeatureFlagRequest, HealthResponse, InitRequest, StateResponse
from .interface import SDKAdapterInterface


class SDKAdapterClient(SDKAdapterInterface):
    """HTTP client for communicating with SDK adapters."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _url(self, path: str, test_id: Optional[str] = None) -> str:
        """Build a URL, appending test_id as a query parameter when provided."""
        url = f"{self.base_url}{path}"
        if test_id is not None:
            url = f"{url}?test_id={test_id}"
        return url

    async def health(self) -> HealthResponse:
        """Get SDK adapter health information."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/health") as resp:
                resp.raise_for_status()
                data = await resp.json()
                return HealthResponse(
                    sdk_name=data["sdk_name"],
                    sdk_version=data["sdk_version"],
                    adapter_version=data["adapter_version"],
                    supports_parallel=data.get("supports_parallel", False),
                    capabilities=data.get("capabilities", []),
                )

    async def init(self, config: InitRequest) -> Dict[str, bool]:
        """Initialize the SDK."""
        payload: Dict[str, Any] = {
            "api_key": config.api_key,
            "host": config.host,
        }

        if config.flush_at is not None:
            payload["flush_at"] = config.flush_at
        if config.flush_interval_ms is not None:
            payload["flush_interval_ms"] = config.flush_interval_ms
        if config.max_retries is not None:
            payload["max_retries"] = config.max_retries
        if config.enable_compression is not None:
            payload["enable_compression"] = config.enable_compression

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/init",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def capture(self, event: CaptureRequest) -> Dict[str, Any]:
        """Capture an event."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/capture",
                json={
                    "distinct_id": event.distinct_id,
                    "event": event.event,
                    "properties": event.properties,
                    "timestamp": event.timestamp,
                },
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def flush(self) -> Dict[str, Any]:
        """Flush all pending events."""
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/flush") as resp:
                resp.raise_for_status()
                return await resp.json()

    async def get_state(self) -> StateResponse:
        """Get internal SDK state."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/state") as resp:
                resp.raise_for_status()
                data = await resp.json()
                return StateResponse(
                    pending_events=data["pending_events"],
                    total_events_captured=data["total_events_captured"],
                    total_events_sent=data["total_events_sent"],
                    total_retries=data["total_retries"],
                    last_error=data.get("last_error"),
                    requests_made=data.get("requests_made", []),
                )

    async def get_feature_flag(self, request: FeatureFlagRequest) -> Dict:
        """Evaluate a feature flag."""
        payload: Dict[str, Any] = {
            "key": request.key,
            "distinct_id": request.distinct_id,
        }

        if request.person_properties is not None:
            payload["person_properties"] = request.person_properties
        if request.groups is not None:
            payload["groups"] = request.groups
        if request.group_properties is not None:
            payload["group_properties"] = request.group_properties
        if request.disable_geoip is not None:
            payload["disable_geoip"] = request.disable_geoip
        if request.force_remote is not None:
            payload["force_remote"] = request.force_remote

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/get_feature_flag",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def reset(self) -> Dict[str, bool]:
        """Reset SDK state."""
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/reset") as resp:
                resp.raise_for_status()
                return await resp.json()

    async def wait_for_health(self, timeout_seconds: int = 30) -> HealthResponse:
        """Wait for the SDK adapter to be ready."""
        start = asyncio.get_event_loop().time()
        last_error = None

        while asyncio.get_event_loop().time() - start < timeout_seconds:
            try:
                return await self.health()
            except Exception as e:
                last_error = e
                await asyncio.sleep(0.5)

        raise TimeoutError(f"SDK adapter not ready after {timeout_seconds}s. Last error: {last_error}")


class ScopedSDKAdapterClient(SDKAdapterInterface):
    """Adapter client that scopes all requests to a specific test_id.

    Wraps an SDKAdapterClient and appends ?test_id= to all request URLs.
    Actions remain unaware of test_id -- they call the same interface.
    """

    def __init__(self, base_client: SDKAdapterClient, test_id: str) -> None:
        self._client = base_client
        self._test_id = test_id

    async def health(self) -> HealthResponse:
        # Health is a global endpoint, not scoped
        return await self._client.health()

    async def init(self, config: InitRequest) -> Dict[str, bool]:
        payload: Dict[str, Any] = {
            "api_key": config.api_key,
            "host": config.host,
        }
        if config.flush_at is not None:
            payload["flush_at"] = config.flush_at
        if config.flush_interval_ms is not None:
            payload["flush_interval_ms"] = config.flush_interval_ms
        if config.max_retries is not None:
            payload["max_retries"] = config.max_retries
        if config.enable_compression is not None:
            payload["enable_compression"] = config.enable_compression

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._client._url("/init", self._test_id),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def capture(self, event: CaptureRequest) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._client._url("/capture", self._test_id),
                json={
                    "distinct_id": event.distinct_id,
                    "event": event.event,
                    "properties": event.properties,
                    "timestamp": event.timestamp,
                },
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def flush(self) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(self._client._url("/flush", self._test_id)) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def get_state(self) -> StateResponse:
        async with aiohttp.ClientSession() as session:
            async with session.get(self._client._url("/state", self._test_id)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return StateResponse(
                    pending_events=data["pending_events"],
                    total_events_captured=data["total_events_captured"],
                    total_events_sent=data["total_events_sent"],
                    total_retries=data["total_retries"],
                    last_error=data.get("last_error"),
                    requests_made=data.get("requests_made", []),
                )

    async def reset(self) -> Dict[str, bool]:
        async with aiohttp.ClientSession() as session:
            async with session.post(self._client._url("/reset", self._test_id)) as resp:
                resp.raise_for_status()
                return await resp.json()
