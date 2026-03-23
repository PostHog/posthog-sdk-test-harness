"""Test context for running SDK tests."""

from typing import Optional, Union

from ..mock_server import MockServerState, ScopedMockServerState
from ..sdk_adapter import SDKAdapterClient
from ..sdk_adapter.client import ScopedSDKAdapterClient
from ..types import InitRequest


class TestContext:
    """Context for running tests.

    When test_id is provided, the adapter and mock server are wrapped in
    scoped variants that transparently route all operations to the correct
    partition. Actions remain unaware of parallelism.
    """

    def __init__(
        self,
        sdk_adapter: SDKAdapterClient,
        mock_server: MockServerState,
        mock_server_url: str,
        api_key: str = "phc_test_key",
        test_id: Optional[str] = None,
    ):
        self.mock_server_url = mock_server_url
        self.api_key = api_key
        self.test_id = test_id

        if test_id is not None:
            self.sdk_adapter: Union[SDKAdapterClient, ScopedSDKAdapterClient] = ScopedSDKAdapterClient(
                sdk_adapter, test_id
            )
            self.mock_server: Union[MockServerState, ScopedMockServerState] = ScopedMockServerState(
                mock_server, test_id
            )
        else:
            self.sdk_adapter = sdk_adapter
            self.mock_server = mock_server

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
        """Initialize the SDK with default test configuration."""
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
