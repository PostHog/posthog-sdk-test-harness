"""Mock PostHog server implementation."""

import json
from typing import Any, Dict, List

from flask import Flask, Response, jsonify, request

from .endpoints import CaptureEndpoint, EndpointHandler
from .state import MockServerState


class MockServer:
    """Mock PostHog server with configurable responses."""

    def __init__(self, state: MockServerState | None = None) -> None:
        """
        Initialize the mock server.

        Args:
            state: Optional MockServerState to use (creates new one if not provided)
        """
        self.state = state or MockServerState()
        self.app = Flask(__name__)
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Set up all Flask routes."""
        # Register endpoint handlers
        handlers: List[EndpointHandler] = [
            CaptureEndpoint(),
        ]

        for handler in handlers:
            for path, method, handler_func in handler.routes():
                # Wrap handler to record request and handle response
                def make_handler(h: EndpointHandler) -> Any:
                    def wrapper() -> Response:
                        # Record the request
                        recorded = self.state.record_request(
                            method=request.method,
                            path=request.path,
                            headers=dict(request.headers),
                            query_params=dict(request.args),
                            body_raw=request.get_data(),
                        )

                        # If response was configured with non-200 status, return error
                        if recorded.response_status != 200:
                            body = (
                                json.loads(recorded.response_body)
                                if recorded.response_body
                                else {"error": "Server error"}
                            )
                            return jsonify(body), recorded.response_status, recorded.response_headers

                        # Otherwise, call the handler
                        body_dict, status_code, headers = h.handle_request(request)
                        return jsonify(body_dict), status_code, headers

                    return wrapper

                # Register the route
                self.app.add_url_rule(
                    path,
                    endpoint=f"{path}_{method}",
                    view_func=make_handler(handler),
                    methods=[method],
                )

        # Control endpoints for test harness
        @self.app.route("/_control/requests", methods=["GET"])
        def get_requests() -> Response:
            """Get all recorded requests."""
            requests = self.state.get_requests()
            return jsonify(
                {
                    "requests": [
                        {
                            "timestamp_ms": r.timestamp_ms,
                            "method": r.method,
                            "path": r.path,
                            "headers": r.headers,
                            "query_params": r.query_params,
                            "body_decompressed": r.body_decompressed,
                            "parsed_events": r.parsed_events,
                            "response_status": r.response_status,
                        }
                        for r in requests
                    ]
                }
            )

        @self.app.route("/_control/requests/clear", methods=["POST"])
        def clear_requests() -> Response:
            """Clear all recorded requests."""
            self.state.clear_requests()
            return jsonify({"success": True})

        @self.app.route("/_control/reset", methods=["POST"])
        def reset_state() -> Response:
            """Reset all state."""
            self.state.reset()
            return jsonify({"success": True})

        @self.app.route("/_control/configure", methods=["POST"])
        def configure_responses() -> Response:
            """Configure mock responses."""
            data = request.get_json()
            if not data or "responses" not in data:
                return jsonify({"error": "Missing 'responses' in request body"}), 400

            from ..types import MockResponse

            responses = [
                MockResponse(
                    status_code=r.get("status_code", 200),
                    headers=r.get("headers", {}),
                    body=r.get("body"),
                )
                for r in data["responses"]
            ]

            self.state.set_response_queue(responses)
            return jsonify({"success": True})

        @self.app.route("/", methods=["GET"])
        @self.app.route("/_health", methods=["GET"])
        def health() -> Response:
            """Health check endpoint."""
            return jsonify({"status": "ok"})

    def run(self, host: str = "0.0.0.0", port: int = 8081, debug: bool = False) -> None:
        """
        Run the Flask app.

        Args:
            host: Host to bind to
            port: Port to bind to
            debug: Enable debug mode
        """
        self.app.run(host=host, port=port, debug=debug)
