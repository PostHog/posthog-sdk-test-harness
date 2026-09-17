"""Controlled concurrent public calls into the actual local flags engine."""

import asyncio
from copy import deepcopy
from uuid import uuid4

from aiohttp import web

from posthog_test_harness.v2.concurrent import CAPABILITY
from posthog_test_harness.v2.contracts import require
from tests.v2_local_flags_host import LocalFlagsHost


class ConcurrentHost(LocalFlagsHost):
    def __init__(self, contracts, **options):
        missing = options.pop("missing_capability", None)
        super().__init__(contracts, **options)
        self.profile["id"] = "controlled-concurrent-flags-v1"
        self.profile["fixture_capabilities"].append(CAPABILITY)
        if missing:
            self.profile["fixture_capabilities"].remove(missing)
        self.groups = {}
        self.extensions["fixtures/invoke-concurrent"] = ("ConcurrentInvoke", self.concurrent)
        self.extensions["cancel"] = ("Cancel", self.cancel_group)

    def record(self, fixture_id, fixture, call, completion, receipts):
        receipt = {
            "fixture_id": fixture_id,
            "call_id": call["call_id"],
            "route": call["route"],
            "completion": completion,
        }
        receipts[call["call_id"]] = receipt
        fixture.observations.append(
            {"kind": "call", "sequence": len(fixture.observations) + 1, "receipt": deepcopy(receipt)}
        )

    async def concurrent(self, fixture_id, fixture, data):
        calls = data["invokes"]
        ids = [c["call_id"] for c in calls]
        require(
            len(set(ids)) == len(ids) and not set(ids).intersection(self.call_ids),
            "duplicate_id",
            "Duplicate group call",
        )
        for call in calls:
            self.contracts.validate_invoke(call, fixture.references)
            require(call["route"] in self.routes, "missing_operation", "Unknown binding")
        self.call_ids.update(ids)
        self.inputs.extend({"fixture_id": fixture_id, "invoke": deepcopy(c)} for c in calls)
        receipts = {}
        group = self.groups[fixture_id] = {"calls": calls, "receipts": receipts, "jobs": []}

        async def native(call):
            try:
                if fixture.defect == "concurrent_hang":
                    await asyncio.Event().wait()
                result = await self.invoke(fixture, call)
                completion = {"kind": "sdk", "outcome": self.outcome(call, result)}
            except Exception as error:
                identity = str(uuid4())
                fixture.retained[identity] = error
                fixture.references[identity] = "exception"
                completion = {
                    "kind": "sdk",
                    "outcome": {"kind": "thrown", "error": {"kind": "exception", "id": identity}},
                }
            self.record(fixture_id, fixture, call, completion, receipts)

        async def launch():
            if fixture.defect == "serial_group":
                for call in calls:
                    await native(call)
            else:
                jobs = group["jobs"] = [asyncio.create_task(native(call)) for call in calls]
                await asyncio.gather(*jobs)

        async def bounded():
            failure = None
            try:
                await asyncio.wait_for(launch(), data["timeout_ms"] / 1000)
            except TimeoutError:
                failure = {"kind": "timeout", "code": "host_deadline", "message": "Concurrent native group timed out"}
            except asyncio.CancelledError:
                failure = {
                    "kind": "cancelled",
                    "code": "group_cancelled",
                    "message": "Concurrent native group cancelled",
                }
            finally:
                for job in group["jobs"]:
                    if not job.done():
                        job.cancel()
                await asyncio.gather(*group["jobs"], return_exceptions=True)
            if failure:
                fixture.expired = True
                fixture.references.clear()
                for call in calls:
                    if call["call_id"] not in receipts:
                        self.record(fixture_id, fixture, call, {"kind": "harness", "failure": failure}, receipts)
            return [receipts[identity] for identity in ids]

        fixture.task = asyncio.create_task(bounded())
        result = await asyncio.shield(fixture.task)
        if fixture.defect == "wrong_group_attribution":
            result = deepcopy(result)
            result[0]["call_id"] = "wrong"
        if fixture.defect == "missing_group_receipt":
            result = result[:1]
        if fixture.defect == "extra_group_receipt":
            result = result + result[:1]
        if fixture.defect == "reordered_group_receipts":
            result = list(reversed(result))
        return self.response("ConcurrentInvokeResponse", {"fixture_id": fixture_id, "calls": result})

    async def cancel_group(self, fixture_id, fixture, data):
        group = self.groups.get(fixture_id)
        if not group or data["call_id"] not in {c["call_id"] for c in group["calls"]}:
            return web.Response(status=404)
        state = "already_completed"
        if data["call_id"] not in group["receipts"]:
            fixture.task.cancel()
            await asyncio.gather(fixture.task, return_exceptions=True)
            state = "cancelled"
        return self.response("CancelResponse", {"fixture_id": fixture_id, "call_id": data["call_id"], "state": state})
