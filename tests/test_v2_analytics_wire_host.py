"""Controlled encoders must not block the adapter's asyncio event loop."""

import asyncio
import threading

import pytest

from tests.v2_analytics_wire_host import AnalyticsWireEngine


@pytest.mark.parametrize("compression,command", [("br", "brotli"), ("zstd", "zstd")])
async def test_post_keeps_event_loop_responsive_during_subprocess_encoding(monkeypatch, compression, command):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    engine = AnalyticsWireEngine({}, "http://unused", {"compression": compression}, "fixture", "analytics_v1", None)

    def encode(args, **kwargs):
        assert args[0] == command
        assert kwargs["timeout"] == 10
        loop.call_soon_threadsafe(started.set)
        assert release.wait(2), "Encoding blocked the event loop"
        raise RuntimeError("Encoder failed")

    monkeypatch.setattr("tests.v2_analytics_wire_host.subprocess.run", encode)
    task = asyncio.create_task(engine.post("/i/v1/analytics/events", {"batch": []}))
    try:
        await asyncio.wait_for(started.wait(), 3)
        # This coroutine can resume while the synchronous encoder is still waiting.
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="Encoder failed"):
            await task
