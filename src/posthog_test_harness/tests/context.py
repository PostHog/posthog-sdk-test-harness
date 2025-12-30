"""Test context for running SDK tests."""

from typing import Optional

from ..mock_server import MockServerState
from ..sdk_adapter import SDKAdapterClient
from ..types import InitRequest


class TestContext:
    """Context for running tests."""

    def __init__(
        self,
        sdk_adapter: SDKAdapterClient,
        mock_server: MockServerState,
        mock_server_url: str,
        api_key: str = "phc_test_key",
    ):
        """
        Initialize test context.

        Args:
            sdk_adapter: SDK adapter client
            mock_server: Mock server state
            mock_server_url: URL of the mock server
            api_key: API key to use for tests
        """
        self.sdk_adapter = sdk_adapter
        self.mock_server = mock_server
        self.mock_server_url = mock_server_url
        self.api_key = api_key

    async def reset(self) -> None:
        """Reset both SDK adapter and mock server state."""
        self.mock_server.reset()
        await self.sdk_adapter.reset()

    async def init_sdk(
        self,
        flush_at: Optional[int] = 1,
        flush_interval_ms: Optional[int] = 100,
        max_retries: Optional[int] = 3,
        enable_compression: Optional[bool] = False,
    ) -> None:
        """
        Initialize the SDK with default test configuration.

        Args:
            flush_at: Number of events to batch before flushing
            flush_interval_ms: Time in ms to wait before flushing
            max_retries: Maximum number of retries
            enable_compression: Enable gzip compression
        """
        await self.sdk_adapter.init(
            InitRequest(
                api_key=self.api_key,
                host=self.mock_server_url,
                flush_at=flush_at,
                flush_interval_ms=flush_interval_ms,
                max_retries=max_retries,
                enable_compression=enable_compression,
            )
        )
