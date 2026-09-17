"""Monotonic arrival/release barriers for real, held flags HTTP requests."""

import asyncio
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field

from .contracts import BoundaryError, require


def deadline_ms(value):
    require(type(value) is int and 1 <= value <= 300000, "invalid_fixture", "Expected a bounded positive deadline")


@dataclass
class Gate:
    id: str
    identity: str | None
    keys: frozenset | None
    timeout_ms: int
    state: str = "armed"
    request_index: int | None = None
    deadline: float | None = None
    wake: threading.Event = field(default_factory=threading.Event)

    def matches(self, body):
        if not isinstance(body, dict):
            return False
        if self.identity is not None and body.get("distinct_id") != self.identity:
            return False
        if self.keys is not None:
            actual = body.get("flag_keys_to_evaluate")
            if not isinstance(actual, list) or not all(isinstance(key, str) for key in actual):
                return False
            return len(actual) == len(self.keys) and frozenset(actual) == self.keys
        return True


class ResponseGates:
    def __init__(self, started):
        self.started = started
        self.condition = threading.Condition()
        self.gates = {}
        self.retired = False
        self.events = []
        self.errors = []

    def event(self, gate, transition, **fields):
        # Called with the condition held; timestamps share the network clock.
        self.events.append(
            {"gate_id": gate.id, "transition": transition, "at_ms": (time.monotonic() - self.started) * 1000, **fields}
        )
        self.condition.notify_all()

    def arm(self, gate_id, *, distinct_id=None, flag_keys=None, timeout_ms=5000):
        deadline_ms(timeout_ms)
        require(isinstance(gate_id, str) and bool(gate_id), "invalid_fixture", "Expected a named response gate")
        require(distinct_id is None or isinstance(distinct_id, str), "invalid_fixture", "Expected an identity string")
        require(
            flag_keys is None
            or (
                isinstance(flag_keys, list)
                and all(isinstance(k, str) for k in flag_keys)
                and len(flag_keys) == len(set(flag_keys))
            ),
            "invalid_fixture",
            "Expected unique flag key strings",
        )
        with self.condition:
            require(not self.retired, "invalid_state", "Mock fixture is retired")
            require(gate_id not in self.gates, "duplicate_id", "Response gate ID already used")
            gate = Gate(gate_id, distinct_id, None if flag_keys is None else frozenset(flag_keys), timeout_ms)
            self.gates[gate_id] = gate
            self.event(gate, "armed")

    def claim(self, body, request_index):
        with self.condition:
            if self.retired:
                return None
            for gate in self.gates.values():
                if gate.state == "armed" and gate.matches(body):
                    gate.state = "arrived"
                    gate.request_index = request_index
                    gate.deadline = time.monotonic() + gate.timeout_ms / 1000
                    self.event(gate, "arrived", request_index=request_index)
                    return gate
        return None

    def expire(self, gate):
        gate.state = "timed_out"
        self.errors.append(BoundaryError("mock_gate_timeout", f"Held response deadline elapsed: {gate.id}"))
        self.event(gate, "timed_out")
        gate.wake.set()

    def expire_overdue(self):
        # Reconcile under the condition lock even if an HTTP waiter has not yet
        # resumed. Teardown and observations must not erase elapsed deadlines.
        now = time.monotonic()
        for gate in self.gates.values():
            if gate.state == "arrived" and now >= gate.deadline:
                self.expire(gate)

    def wait(self, gate):
        gate.wake.wait(max(0, gate.deadline - time.monotonic()))
        with self.condition:
            if gate.state == "arrived":
                self.expire(gate)
            return gate.state

    def release(self, gate_id):
        with self.condition:
            require(gate_id in self.gates, "invalid_fixture", "Unknown response gate")
            gate = self.gates[gate_id]
            require(gate.state == "arrived", "invalid_state", "Only an arrived, held response can be released")
            if time.monotonic() >= gate.deadline:
                self.expire(gate)
                raise self.errors[-1]
            gate.state = "released"
            self.event(gate, "released")
            gate.wake.set()

    def responded(self, gate_id, status):
        with self.condition:
            gate = self.gates[gate_id]
            if gate.state == "released":
                gate.state = "completed"
            self.event(gate, "responded", status=status)

    def wait_for_arrivals(self, gate_ids, timeout_ms):
        deadline_ms(timeout_ms)
        with self.condition:
            require(
                bool(gate_ids) and len(set(gate_ids)) == len(gate_ids) and all(g in self.gates for g in gate_ids),
                "invalid_fixture",
                "Expected distinct known response gate IDs",
            )
            gates = [self.gates[g] for g in gate_ids]
            self.condition.wait_for(
                lambda: self.retired
                or all(g.state == "arrived" for g in gates)
                or any(g.state not in ("armed", "arrived") for g in gates),
                timeout_ms / 1000,
            )
            self.expire_overdue()
            require(not self.retired, "invalid_state", "Mock fixture retired while awaiting requests")
            require(all(g.state in ("armed", "arrived") for g in gates), "invalid_state", "Response is no longer held")
            require(
                all(g.state == "arrived" for g in gates),
                "mock_arrival_timeout",
                "Requests did not reach response gates",
            )
            return [g.request_index for g in gates]

    async def in_flight(self, gate_ids, timeout_ms=5000):
        """Wait for these actual held requests, not scheduler quiescence or SDK completion."""
        return await asyncio.to_thread(self.wait_for_arrivals, list(gate_ids), timeout_ms)

    def retire(self):
        with self.condition:
            if self.retired:
                return
            self.retired = True
            self.expire_overdue()
            for gate in self.gates.values():
                if gate.state in ("armed", "arrived"):
                    gate.state = "aborted"
                    self.event(gate, "aborted")
                    gate.wake.set()
            self.condition.notify_all()

    def diagnostics(self):
        with self.condition:
            return deepcopy(self.events)

    def failures(self):
        with self.condition:
            self.expire_overdue()
            return list(self.errors)
