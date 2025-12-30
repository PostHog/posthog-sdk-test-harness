"""HTTP client for SDK adapter."""

import asyncio
from typing import Any, Dict

import aiohttp

from ..types import CaptureRequest, HealthResponse, InitRequest, StateResponse
from .interface import SDKAdapterInterface


class SDKAdapterClient(SDKAdapterInterface):
    """HTTP client for communicating with SDK adapters."""

    def __init__(self, base_url: str) -> None:
        """
        Initialize the client.

        Args:
            base_url: Base URL of the SDK adapter (e.g., "http://localhost:8080")
        """
        self.base_url = base_url.rstrip("/")

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
                )

    async def init(self, config: InitRequest) -> Dict[str, bool]:
        """Initialize the SDK."""
        # Build payload, excluding None values
        payload = {
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

    async def reset(self) -> Dict[str, bool]:
        """Reset SDK state."""
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/reset") as resp:
                resp.raise_for_status()
                return await resp.json()

    async def wait_for_health(self, timeout_seconds: int = 30) -> HealthResponse:
        """
        Wait for the SDK adapter to be ready.

        Args:
            timeout_seconds: Maximum time to wait in seconds

        Returns:
            HealthResponse when adapter is ready

        Raises:
            TimeoutError: If adapter doesn't become ready in time
            aiohttp.ClientError: If adapter returns an error
        """
        start = asyncio.get_event_loop().time()
        last_error = None

        while asyncio.get_event_loop().time() - start < timeout_seconds:
            try:
                return await self.health()
            except Exception as e:
                last_error = e
                await asyncio.sleep(0.5)

        raise TimeoutError(
            f"SDK adapter not ready after {timeout_seconds}s. Last error: {last_error}"
        )
