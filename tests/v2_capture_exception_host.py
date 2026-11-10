"""Controlled exception delivery over the existing analytics transport."""

import json
import re
from copy import deepcopy

from tests.v2_group_identify_host import GroupIdentifyHost


def custom_properties(value):
    if isinstance(value, dict):
        return {key: custom_properties(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [custom_properties(item) for item in value]
    return value


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
        exceptions = []

        def append(descriptor, parent=None, source=None):
            raw = "value" in descriptor
            if raw:
                value = descriptor["value"]
                message = value.get("message", "object") if isinstance(value, dict) else json.dumps(value)
                type_name = "Error"
            else:
                type_name, message = descriptor["type"], descriptor["message"]
            stack = descriptor.get("stack") or (
                descriptor["value"].get("stack") if raw and isinstance(descriptor["value"], dict) else None
            )
            mechanism = {"synthetic": raw, "exception_id": len(exceptions)}
            if parent is None:
                mechanism.update(type="generic", handled=True)
            else:
                mechanism.update(type="chained", source=source, parent_id=parent)
            frames = [{"filename": "fixture.py", "function": "report_exception"}]
            if stack:
                functions = re.findall(r"at ([^ ]+) ", stack)
                frames = [{"filename": "exception-fixture.js", "function": name} for name in reversed(functions)]
            exception = {"type": type_name, "value": message, "mechanism": mechanism, "stacktrace": {"frames": frames}}
            exceptions.append(exception)
            parent = mechanism["exception_id"]
            if "cause" in descriptor:
                append(descriptor["cause"], parent, "cause")
            for member in descriptor.get("errors", []):
                append(member, parent, "member")

        append(args["error"])
        exception = exceptions[0]
        if fixture.defect and fixture.defect.startswith("exception:"):
            _, field, value = fixture.defect.split(":", 2)
            value = json.loads(value)
            if field == "handled":
                exception["mechanism"]["handled"] = value
            else:
                exception[field] = value
        properties = {
            **custom_properties(
                {
                    key: deepcopy(value)
                    for key, value in args.get("properties", {}).items()
                    if key != "$exception_source"
                }
            ),
            "$exception_list": exceptions,
            "$exception_level": "error",
        }
        await fixture.engine.capture(
            {"event": "$exception", "distinct_id": args["distinct_id"], "properties": properties}
        )
