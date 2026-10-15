"""Controlled public group identify over the existing analytics transport."""

from copy import deepcopy

from tests.test_v2_identify_alias import IdentifyAliasHost


class GroupIdentifyHost(IdentifyAliasHost):
    def __init__(self, contracts, **options):
        super().__init__(contracts, **options)
        if options.get("missing_route") != "/group_identify":
            self.routes.append("/group_identify")

    async def invoke(self, fixture, call):
        if call["route"] != "/group_identify":
            return await super().invoke(fixture, call)
        if fixture.defect == "operation_throw":
            raise RuntimeError("Controlled operation failure")
        args = call["args"]
        properties = {"$group_type": args["group_type"], "$group_key": args["group_key"]}
        if "properties" in args:
            properties["$group_set"] = deepcopy(args["properties"])
        if fixture.defect == "flatten_group_set":
            properties.update(properties.pop("$group_set"))
        await fixture.engine.capture(
            {"event": "$groupidentify", "distinct_id": args["distinct_id"], "properties": properties}
        )
