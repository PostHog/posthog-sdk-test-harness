"""Capability gap for simultaneous operations on one isolated receiver."""

from .contracts import BoundaryError

CAPABILITY = "invocation.concurrent.v1"


async def invoke_concurrent(fixture, profile, invokes, timeout_ms):
    raise BoundaryError("fixture_unavailable", "Concurrent invocation has no draft2 binding", "blocked_fixture")
