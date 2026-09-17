"""An explicitly negotiated concurrent group, with ordinary per-call receipts."""

import asyncio
from copy import deepcopy

from .contracts import BoundaryError, require

CAPABILITY = "invocation.concurrent.v1"


async def invoke_concurrent(fixture, profile, invokes, timeout_ms):
    fixture.check_active()
    client = fixture.client
    client.deadline(timeout_ms)
    require(not fixture.busy, "invalid_state", "Fixture already has a pending invocation")
    if CAPABILITY not in profile["fixture_capabilities"]:
        raise BoundaryError("missing_fixture", f"Required fixture capability: {CAPABILITY}", "blocked_fixture")
    request = {"fixture_id": fixture.id, "timeout_ms": timeout_ms, "invokes": deepcopy(invokes)}
    fixture.contracts.validate("ConcurrentInvokeRequest", request)
    ids = [call["call_id"] for call in invokes]
    require(
        len(set(ids)) == len(ids) and not set(ids).intersection(client.used_call_ids),
        "duplicate_id",
        "Concurrent call IDs must be new and unique",
    )
    for call in invokes:
        fixture.contracts.validate_invoke(call, fixture.references)
    client.used_call_ids.update(ids)
    for call in invokes:
        client.pending[call["call_id"]] = {"fixture_id": fixture.id, "route": call["route"]}
    fixture.busy = True

    def failed(call, error):
        return {
            "fixture_id": fixture.id,
            "call_id": call["call_id"],
            "route": call["route"],
            "completion": {"kind": "harness", "failure": error.failure()},
        }

    try:
        missing = [call for call in invokes if call["route"] not in client.negotiation["supported_routes"]]
        if missing:
            for call in invokes:
                error = (
                    BoundaryError("missing_operation", "Applicable operation has no binding", "unsupported_binding")
                    if call in missing
                    else BoundaryError("group_not_started", "Group contains an unbound operation", "blocked_fixture")
                )
                fixture._record(failed(call, error))
        else:
            response = await client._post(
                "fixtures/invoke-concurrent",
                "ConcurrentInvokeRequest",
                "ConcurrentInvokeResponse",
                request,
                timeout_ms + 1000,
            )
            require(
                response["fixture_id"] == fixture.id and len(response["calls"]) == len(invokes),
                "invalid_response",
                "Wrong concurrent fixture or receipt count",
            )
            # Validate attribution for the entire response before retaining any result.
            for call, receipt in zip(invokes, response["calls"]):
                require(
                    receipt["fixture_id"] == fixture.id
                    and receipt["call_id"] == call["call_id"]
                    and receipt["route"] == call["route"]
                    and "parent_call_id" not in receipt
                    and "callback_invocation_id" not in receipt,
                    "invalid_response",
                    "Wrong concurrent call attribution",
                )
            for receipt in response["calls"]:
                fixture._record(receipt)
    except BoundaryError as error:
        fixture.invalidate()
        client.errors.append(error)
        for call in invokes:
            if call["call_id"] not in client.calls:
                fixture._record(failed(call, error))
    except asyncio.CancelledError:
        fixture.invalidate()
        error = BoundaryError("caller_cancelled", "Concurrent runner task cancelled", "cancelled")
        client.errors.append(error)
        for call in invokes:
            if call["call_id"] not in client.calls:
                fixture._record(failed(call, error))
        raise
    finally:
        for identity in ids:
            client.pending.pop(identity, None)
        fixture.busy = False
    return [deepcopy(client.calls[identity]) for identity in ids]
