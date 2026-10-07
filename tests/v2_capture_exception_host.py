"""Controlled exception delivery over the existing analytics transport."""

import json
from copy import deepcopy

from tests.v2_group_identify_host import GroupIdentifyHost


class CaptureExceptionHost(GroupIdentifyHost):
    def __init__(self, contracts, **options):
        super().__init__(contracts, **options)
        if options.get("missing_route") != "/capture_exception":
            self.routes.append("/capture_exception")

    async def invoke(self, fixture, call):
        if call["route"] != "/capture_exception":
            return await super().invoke(fixture, call)
        if fixture.defect == "operation_throw":
            raise RuntimeError("Controlled operation failure")
        args = call["args"]
        exception = {
            "type": args["error"]["type"],
            "value": args["error"]["message"],
            "mechanism": {"handled": True},
            "stacktrace": {"frames": [{"filename": "fixture.py", "function": "report_exception"}]},
        }
        if fixture.defect and fixture.defect.startswith("exception:"):
            _, field, value = fixture.defect.split(":", 2)
            value = json.loads(value)
            if field == "handled":
                exception["mechanism"]["handled"] = value
            else:
                exception[field] = value
        properties = {**deepcopy(args.get("properties", {})), "$exception_list": [exception]}
        await fixture.engine.capture(
            {"event": "$exception", "distinct_id": args["distinct_id"], "properties": properties}
        )
