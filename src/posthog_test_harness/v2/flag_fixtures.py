"""Optional, declared native flag-state fixture consumer."""

from copy import deepcopy

from .contracts import BoundaryError, require

CAPABILITIES = {
    "definitions_install": "flags.definitions.install.v1",
    "evaluation_cache_put": "flags.evaluation_cache.put.v1",
    "evaluation_activity": "flags.evaluation_activity.v1",
}

PROVENANCE_CAPABILITY = "flags.evaluation_provenance.v1"


class FlagStateControls:
    def __init__(self, context):
        self.context = context

    async def command(self, kind, **fields):
        ctx = self.context
        fixture = ctx.fixture
        require(fixture is not None, "invalid_state", "Allocate a fixture before preparing flag state")
        fixture.check_active()
        require(not fixture.busy, "invalid_state", "Fixture already has a pending invocation")
        capability = PROVENANCE_CAPABILITY if kind == "evaluation_provenance" else CAPABILITIES[kind]
        if capability not in ctx.profile["fixture_capabilities"]:
            raise BoundaryError("missing_fixture", f"Required fixture capability: {capability}", "blocked_fixture")
        fixture.client.deadline(ctx.timeout_ms)
        data = {"fixture_id": fixture.id, "timeout_ms": ctx.timeout_ms, "command": {"kind": kind, **fields}}
        record = {"request": deepcopy(data)}
        ctx.diagnostics["controls"].append(record)
        fixture.busy = True
        try:
            result = await fixture.client._post(
                "fixtures/flags", "FlagStateRequest", "FlagStateResponse", data, ctx.timeout_ms + 1000
            )
            record["response"] = deepcopy(result)
            require(
                result["fixture_id"] == fixture.id and result["command"] == kind,
                "invalid_response",
                "Wrong flag-state fixture attribution",
            )
            if result["kind"] == "failed":
                failure = result["failure"]
                raise BoundaryError(failure["code"], failure["message"], failure["kind"])
            require(
                result["kind"]
                == {"evaluation_activity": "activity", "evaluation_provenance": "provenance"}.get(kind, "applied"),
                "invalid_response",
                "Wrong flag-state response kind",
            )
            if kind == "evaluation_provenance":
                require(
                    result["observation"]["call_id"] == fields["call_id"],
                    "invalid_response",
                    "Wrong evaluation invocation attribution",
                )
            return result["observation"]
        except BoundaryError as error:
            record["failure"] = error.failure()
            if error.kind not in ("blocked_fixture", "blocked_contract", "unsupported_binding"):
                fixture.invalidate()
            raise
        finally:
            fixture.busy = False
