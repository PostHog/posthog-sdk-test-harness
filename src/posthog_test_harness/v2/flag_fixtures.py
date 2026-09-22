"""Explicit capability gaps for canonical state preparation without public bindings."""

from .contracts import BoundaryError


class FlagStateControls:
    def __init__(self, context):
        self.context = context

    async def command(self, kind, **fields):
        raise BoundaryError("fixture_unavailable", f"No public fixture binding for {kind}", "blocked_fixture")
