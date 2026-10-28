"""HTTP client for SDK adapter."""

import asyncio
from typing import Any, Dict, Optional

import aiohttp

from ..types import CaptureRequest, FeatureFlagRequest, HealthResponse, InitRequest, StateResponse
from .interface import SDKAdapterInterface

_INIT_OPTIONAL_FIELDS = (
    "flush_at",
    "flush_interval_ms",
    "max_retries",
    "enable_compression",
    "disable_geoip",
    "historical_migration",
    "personal_api_key",
    "super_properties",
    "before_send",
)


def _init_payload(config: InitRequest) -> Dict[str, Any]:
    """Adapter body for /init; optional fields are sent only when set."""
    payload: Dict[str, Any] = {"api_key": config.api_key, "host": config.host}
    for name in _INIT_OPTIONAL_FIELDS:
        value = getattr(config, name)
        if value is not None:
            payload[name] = value
    return payload


def _state_response(data: Dict[str, Any]) -> StateResponse:
    return StateResponse(
        pending_events=data["pending_events"],
        total_events_captured=data["total_events_captured"],
        total_events_sent=data["total_events_sent"],
        total_retries=data["total_retries"],
        last_error=data.get("last_error"),
        requests_made=data.get("requests_made", []),
        reported_drops=data.get("reported_drops", []),
        log_lines=data.get("log_lines", []),
    )


def _capture_payload(event: CaptureRequest) -> Dict[str, Any]:
    """Adapter body for /capture and /capture_ai; optional fields are sent only when set."""
    payload: Dict[str, Any] = {
        "distinct_id": event.distinct_id,
        "event": event.event,
        "properties": event.properties,
        "timestamp": event.timestamp,
    }
    if event.options is not None:
        payload["options"] = event.options
    if event.uuid is not None:
        payload["uuid"] = event.uuid
    if event.pad_properties_to_bytes is not None:
        payload["pad_properties_to_bytes"] = event.pad_properties_to_bytes
    if event.marker is not None:
        payload["marker"] = event.marker
    return payload


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

    def _feature_flag_payload(self, request: FeatureFlagRequest) -> Dict[str, Any]:
        """Serialize a feature flag request for the adapter API."""
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

        if request.only_evaluate_locally is not None:
            payload["only_evaluate_locally"] = request.only_evaluate_locally

        return payload

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
        payload = _init_payload(config)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/init",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def capture(self, event: CaptureRequest) -> Dict[str, Any]:
        """Capture an event."""
        payload = _capture_payload(event)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/capture",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def capture_ai(self, event: CaptureRequest) -> Dict[str, Any]:
        """Capture an event on the dedicated AI capture endpoint."""
        payload = _capture_payload(event)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/capture_ai",
                json=payload,
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
                return _state_response(data)

    async def get_feature_flag(self, request: FeatureFlagRequest) -> Dict:
        """Evaluate a feature flag."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/get_feature_flag",
                json=self._feature_flag_payload(request),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def reload_feature_flag_definitions(self, timeout_ms: int = 5000) -> Dict[str, bool]:
        return await self._reload_feature_flag_definitions(timeout_ms)

    async def _reload_feature_flag_definitions(self, timeout_ms: int, test_id: Optional[str] = None) -> Dict[str, bool]:
        if not 0 < timeout_ms <= 30000:
            raise ValueError("timeout_ms must be between 1 and 30000")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000)) as session:
            async with session.post(
                self._url("/reload_feature_flag_definitions", test_id),
                json={"timeout_ms": timeout_ms},
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
        payload = _init_payload(config)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._client._url("/init", self._test_id),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def capture(self, event: CaptureRequest) -> Dict[str, Any]:
        payload = _capture_payload(event)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._client._url("/capture", self._test_id),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def capture_ai(self, event: CaptureRequest) -> Dict[str, Any]:
        payload = _capture_payload(event)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._client._url("/capture_ai", self._test_id),
                json=payload,
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
                return _state_response(data)

    async def get_feature_flag(self, request: FeatureFlagRequest) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._client._url("/get_feature_flag", self._test_id),
                json=self._client._feature_flag_payload(request),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def reload_feature_flag_definitions(self, timeout_ms: int = 5000) -> Dict[str, bool]:
        return await self._client._reload_feature_flag_definitions(timeout_ms, self._test_id)

    async def reset(self) -> Dict[str, bool]:
        async with aiohttp.ClientSession() as session:
            async with session.post(self._client._url("/reset", self._test_id)) as resp:
                resp.raise_for_status()
                return await resp.json()
