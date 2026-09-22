"""Declared flush controls and isolated instances of the existing mock service."""

import json
import threading
import time
from copy import deepcopy

from flask import g, request
from werkzeug.serving import WSGIRequestHandler, make_server

from ..mock_server import MockServer, MockServerState
from ..mock_server.endpoints.capture import CaptureEndpoint
from ..types import MockResponse
from .contracts import BoundaryError, require
from .network import url_host, validate_host
from .network_gates import ResponseGates, deadline_ms

CAPABILITIES = {
    "scheduler_manual": "scheduler.manual.v1",
    "clock_fixed": "clock.fixed.v1",
    "storage_empty": "storage.empty.v1",
    "queue_snapshot": "queue.snapshot.v1",
}
INGESTION_PATHS = {path for path, _, _ in CaptureEndpoint().routes() if "/ai/" not in path}
FLAGS_PATHS = {"/flags", "/flags/"}


class QuietHandler(WSGIRequestHandler):
    timeout = 5

    def log(self, type, message, *args):
        pass


class CaseServer:
    """One URL/state per case. Retired URLs reject late traffic until run teardown."""

    def __init__(self, bind_host="127.0.0.1", advertised_host="127.0.0.1"):
        validate_host(bind_host)
        validate_host(advertised_host)
        self.state = MockServerState()
        self.mock = MockServer(self.state, response_provider=self.select_response)
        self.traffic = []
        self.lock = threading.Lock()
        self.idle = threading.Condition(self.lock)
        self.active_requests = 0
        self.retired = False
        self.next_ingestion_status = None
        self.flag_responses = {}
        self.use_response_queue_for_flags = False
        self.next_flags_status = None
        self.started = time.monotonic()
        self.gates = ResponseGates(self.started)

        @self.mock.app.before_request
        def record_start():
            with self.lock:
                if self.retired:
                    return {"error": "Fixture closed"}, 410
                g.traffic_index = len(self.traffic)
                self.active_requests += 1
                self.traffic.append(
                    {
                        "method": request.method,
                        "path": request.path,
                        "status": None,
                        "at_ms": (time.monotonic() - self.started) * 1000,
                    }
                )

        @self.mock.app.after_request
        def record_end(response):
            if hasattr(g, "traffic_index"):
                with self.lock:
                    self.traffic[g.traffic_index]["status"] = response.status_code
                    if hasattr(g, "response_gate"):
                        self.gates.responded(g.response_gate, response.status_code)
                    self.active_requests -= 1
                    self.idle.notify_all()
            return response

        self.server = make_server(bind_host, 0, self.mock.app, request_handler=QuietHandler, threaded=True)
        self.url = f"http://{url_host(advertised_host)}:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def select_response(self, incoming):
        # Read the body outside the case lock: one slow/held request must not
        # prevent another request from reaching its own barrier.
        body = incoming.get_json(silent=True) if incoming.path in FLAGS_PATHS else None
        with self.lock:
            if self.retired:
                return MockResponse(status_code=410)
            if incoming.path in INGESTION_PATHS and self.next_ingestion_status is not None:
                response = MockResponse(status_code=self.next_ingestion_status)
                self.next_ingestion_status = None
                return response
            if incoming.path not in FLAGS_PATHS:
                return None
            if self.use_response_queue_for_flags:
                return None
            identity = body.get("distinct_id") if isinstance(body, dict) else None
            if self.next_flags_status is not None:
                response = MockResponse(status_code=self.next_flags_status)
                self.next_flags_status = None
            elif isinstance(identity, str) and identity in self.flag_responses:
                response = MockResponse(body=json.dumps(self.flag_responses[identity]))
            else:
                response = MockResponse()
            gate = self.gates.claim(body, g.traffic_index)
            if gate is not None:
                g.response_gate = gate.id
        if gate is not None:
            state = self.gates.wait(gate)
            if state != "released":
                return MockResponse(status_code=504 if state == "timed_out" else 410)
        return response

    def requests(self):
        with self.lock:
            return deepcopy(self.traffic)

    def fail_next_ingestion(self, status):
        with self.lock:
            require(self.next_ingestion_status is None, "invalid_state", "An ingestion failure is already configured")
            self.next_ingestion_status = status

    def fail_next_flags(self, status):
        with self.lock:
            require(self.next_flags_status is None, "invalid_state", "A flag failure is already configured")
            self.next_flags_status = status

    def set_flags(self, identity, values, payloads):
        """Configure service data by identity, independently of ingestion failures."""
        with self.lock:
            self.flag_responses[identity] = deepcopy(
                {
                    "featureFlags": values,
                    "featureFlagPayloads": {key: json.dumps(value) for key, value in payloads.items()},
                    "errorsWhileComputingFlags": False,
                }
            )

    def flag_requests(self):
        return [r for r in self.state.get_requests() if r.path in FLAGS_PATHS]

    def flag_request_summary(self):
        summaries = []
        for recorded in self.flag_requests():
            entry = {"path": recorded.path, "status": recorded.response_status}
            try:
                body = json.loads(recorded.body_decompressed or "")
            except (ValueError, TypeError):
                body = None
            entry["json_object"] = isinstance(body, dict)
            if isinstance(body, dict):
                entry["context"] = {key: body[key] for key in ("distinct_id", "flag_keys_to_evaluate") if key in body}
            summaries.append(entry)
        return summaries

    def reset(self):
        with self.lock:
            require(
                not self.traffic and not self.gates.diagnostics(),
                "invalid_state",
                "Mock reset would discard case observations",
            )
            self.next_ingestion_status = None
            self.flag_responses.clear()
            self.use_response_queue_for_flags = False
            self.next_flags_status = None
            self.state.reset()

    def retire(self):
        with self.lock:
            self.retired = True
            self.gates.retire()

    def wait_idle(self, timeout_ms=5000):
        deadline_ms(timeout_ms)
        with self.idle:
            require(
                self.idle.wait_for(lambda: self.active_requests == 0, timeout_ms / 1000),
                "teardown_failed",
                "Mock requests did not finish",
            )

    def close(self):
        self.retire()
        try:
            self.wait_idle()
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
        require(not self.thread.is_alive(), "teardown_failed", "Mock server did not stop")


class FlushControls:
    def __init__(self, fixture, profile, timeout_ms, diagnostics):
        self.fixture, self.profile = fixture, profile
        self.timeout_ms, self.diagnostics = timeout_ms, diagnostics

    async def command(self, kind, **fields):
        raise BoundaryError("fixture_unavailable", f"No public fixture binding for {kind}", "blocked_fixture")
