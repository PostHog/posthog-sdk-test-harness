"""A native in-flight coordinator: share clean omissions, not another identity's values."""

import asyncio

from tests.v2_concurrent_host import ConcurrentHost
from tests.v2_flags_host import Snapshot
from tests.v2_local_flags_host import LocalFlagsEngine


class ProbeEngine(LocalFlagsEngine):
    def __init__(self, *args):
        super().__init__(*args)
        self.probes = {}
        self.probe_events = []
        self.joined = asyncio.Event()

    async def remote(self, args):
        scope = set(args.get("flag_keys") or [])
        missing = frozenset(scope.difference(self.definitions))
        if not missing or not self.ready.is_set() or self.defect == "uncoordinated_probes":
            return await super().remote(args)
        overlaps = [
            (keys, future)
            for keys, future in self.probes.items()
            if keys.intersection(missing) or self.defect == "serialize_all_probes"
        ]
        if overlaps:
            keys, future = overlaps[0]
            self.probe_events.append(("waiting", args.get("distinct_id")))
            self.joined.set()
            result = await asyncio.shield(future)
            if result is not None:
                if self.defect == "share_positive_probe":
                    return result
                if result.remote_clean and scope <= keys and not scope.intersection(result.values):
                    return Snapshot(self, args.get("distinct_id"), {}, {})
            own = await self.remote(args)
            return result if result is not None and self.defect == "wrong_waiter_value" else own
        future = asyncio.get_running_loop().create_future()
        self.probes[missing] = future
        self.probe_events.append(("started", args.get("distinct_id")))
        result = None
        try:
            result = await super().remote(args)
            return result
        finally:
            self.probes.pop(missing)
            self.probe_events.append(("settled", args.get("distinct_id")))
            future.set_result(result)


class ProbeHost(ConcurrentHost):
    engine_type = ProbeEngine

    def __init__(self, contracts, **options):
        super().__init__(contracts, **options)
        self.profile["id"] = "controlled-shared-probes-v1"
        self.profile["module"]["entry"] = "tests.v2_probe_host.ProbeEngine"
