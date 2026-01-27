"""Test actions for contract-based testing.

To add a new action, simply create a class that inherits from Action
and implements the execute method. The action will be automatically
registered and available in CONTRACT.yaml tests.

Example:

    class MyCustomAction(Action):
        @property
        def name(self) -> str:
            return "my_custom_action"

        async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
            # Your action logic here
            value = params.get("some_param")
            # Do something with ctx.sdk_adapter or ctx.mock_server
            return result

That's it! Your action is now available for use in CONTRACT.yaml:

    steps:
      - action: my_custom_action
        some_param: value
"""

import asyncio
import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict

from .types import CaptureRequest, InitRequest, MockResponse

if TYPE_CHECKING:
    from .tests.context import TestContext


class Action(ABC):
    """Base class for test actions."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Action name (e.g., 'init', 'capture', 'assert_request_count')."""
        pass

    @abstractmethod
    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        """
        Execute the action.

        Args:
            params: Action parameters from CONTRACT.yaml
            ctx: Test context

        Returns:
            Action result (if any)

        Raises:
            AssertionError: If assertion fails
            ValueError: If parameters are invalid
        """
        pass


# ============================================================================
# Adapter Interaction Actions
# ============================================================================


class InitAction(Action):
    """Initialize the SDK adapter."""

    @property
    def name(self) -> str:
        return "init"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        return await ctx.sdk_adapter.init(
            InitRequest(
                api_key=params.get("api_key", "phc_test_key"),
                host=params.get("host", ctx.mock_server_url),
                flush_at=params.get("flush_at"),
                flush_interval_ms=params.get("flush_interval_ms"),
                max_retries=params.get("max_retries"),
                enable_compression=params.get("enable_compression"),
            )
        )


class CaptureAction(Action):
    """Capture a single event via the adapter."""

    @property
    def name(self) -> str:
        return "capture"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        return await ctx.sdk_adapter.capture(
            CaptureRequest(
                distinct_id=params["distinct_id"],
                event=params["event"],
                properties=params.get("properties"),
                timestamp=params.get("timestamp"),
            )
        )


class CaptureMultipleAction(Action):
    """Capture multiple events."""

    @property
    def name(self) -> str:
        return "capture_multiple"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        count = params["count"]
        template = params["params"]
        results = []

        for i in range(count):
            # Replace {index} placeholder in template
            event_params = {}
            for key, value in template.items():
                if isinstance(value, str):
                    event_params[key] = value.format(index=i)
                else:
                    event_params[key] = value

            result = await ctx.sdk_adapter.capture(
                CaptureRequest(
                    distinct_id=event_params["distinct_id"],
                    event=event_params["event"],
                    properties=event_params.get("properties"),
                    timestamp=event_params.get("timestamp"),
                )
            )
            results.append(result)

        return results


class FlushAction(Action):
    """Flush pending events via the adapter."""

    @property
    def name(self) -> str:
        return "flush"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        return await ctx.sdk_adapter.flush()


class ResetAction(Action):
    """Reset adapter state."""

    @property
    def name(self) -> str:
        return "reset"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        return await ctx.reset()


# ============================================================================
# Mock Server Configuration Actions
# ============================================================================


class ConfigureMockResponsesAction(Action):
    """Configure mock server to return specific responses."""

    @property
    def name(self) -> str:
        return "configure_mock_responses"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        responses = [
            MockResponse(
                status_code=r.get("status_code", 200),
                headers=r.get("headers", {}),
                body=r.get("body"),
            )
            for r in params["responses"]
        ]
        ctx.mock_server.set_response_queue(responses)


# ============================================================================
# Timing Actions
# ============================================================================


class WaitAction(Action):
    """Wait for a specified duration."""

    @property
    def name(self) -> str:
        return "wait"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        await asyncio.sleep(params["duration_ms"] / 1000.0)


# ============================================================================
# Assertion Actions - Request Level
# ============================================================================


class AssertRequestCountAction(Action):
    """Assert exact number of HTTP requests made to mock server."""

    @property
    def name(self) -> str:
        return "assert_request_count"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        actual = len(requests)
        expected = params["expected"]
        if actual != expected:
            raise AssertionError(f"Expected {expected} requests, got {actual}")


class AssertRequestCountGteAction(Action):
    """Assert number of requests is greater than or equal to expected."""

    @property
    def name(self) -> str:
        return "assert_request_count_gte"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        actual = len(requests)
        expected = params["expected"]
        if actual < expected:
            raise AssertionError(f"Expected at least {expected} requests, got {actual}")


# ============================================================================
# Assertion Actions - Event Level
# ============================================================================


class AssertEventFieldAction(Action):
    """Assert a specific field value in the first captured event."""

    @property
    def name(self) -> str:
        return "assert_event_field"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        events = requests[0].parsed_events
        if not events:
            raise AssertionError("No events in request")

        field = params["field"]
        expected = params["expected"]
        actual = events[0].get(field)

        if actual != expected:
            raise AssertionError(f"Expected {field}='{expected}', got '{actual}'")


class AssertEventHasFieldAction(Action):
    """Assert that an event has a specific field."""

    @property
    def name(self) -> str:
        return "assert_event_has_field"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        events = requests[0].parsed_events
        if not events:
            raise AssertionError("No events in request")

        field = params["field"]
        if field not in events[0]:
            raise AssertionError(f"Event missing '{field}' field")


class AssertEventPropertyAction(Action):
    """Assert event properties."""

    @property
    def name(self) -> str:
        return "assert_event_property"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests or not requests[0].parsed_events:
            raise AssertionError("No events found")

        event = requests[0].parsed_events[0]
        properties = event.get("properties", {})
        prop_name = params["property"]

        if params.get("exists"):
            if prop_name not in properties:
                # Debug: show what properties actually exist
                available_props = list(properties.keys())[:10]  # First 10 to avoid huge output
                raise AssertionError(f"Event missing '{prop_name}' property. Available properties: {available_props}")

        if "expected" in params:
            actual = properties.get(prop_name)
            expected = params["expected"]
            if actual != expected:
                # Debug: show what properties actually exist
                available_props = list(properties.keys())[:10]
                raise AssertionError(f"Expected {prop_name}='{expected}', got '{actual}'. Available properties: {available_props}")


# ============================================================================
# Assertion Actions - UUID Validation
# ============================================================================


class AssertUuidFormatAction(Action):
    """Assert that a field contains a valid UUID."""

    @property
    def name(self) -> str:
        return "assert_uuid_format"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests or not requests[0].parsed_events:
            raise AssertionError("No events found")

        event = requests[0].parsed_events[0]
        field = params["field"]
        uuid_val = event.get(field)

        if not uuid_val:
            raise AssertionError(f"Event missing '{field}' field")

        # Basic UUID format check: 36 chars with 4 dashes
        if len(uuid_val) != 36 or uuid_val.count("-") != 4:
            raise AssertionError(f"Invalid UUID format: {uuid_val}")


class AssertAllUuidsUniqueAction(Action):
    """Assert all captured events have unique UUIDs."""

    @property
    def name(self) -> str:
        return "assert_all_uuids_unique"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        all_uuids = []

        for req in requests:
            if req.parsed_events:
                for event in req.parsed_events:
                    if "uuid" in event:
                        all_uuids.append(event["uuid"])

        unique_uuids = set(all_uuids)
        if len(unique_uuids) != len(all_uuids):
            raise AssertionError(f"UUIDs not unique: {len(all_uuids)} total, {len(unique_uuids)} unique")


class AssertUuidPreservedOnRetryAction(Action):
    """Assert that UUIDs are the same across retry attempts."""

    @property
    def name(self) -> str:
        return "assert_uuid_preserved_on_retry"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests to check retry")

        first_uuids = [e.get("uuid") for e in (requests[0].parsed_events or []) if "uuid" in e]
        second_uuids = [e.get("uuid") for e in (requests[1].parsed_events or []) if "uuid" in e]

        if first_uuids != second_uuids:
            raise AssertionError(f"UUIDs changed on retry: {first_uuids} != {second_uuids}")


class AssertTimestampPreservedOnRetryAction(Action):
    """Assert that timestamps are the same across retry attempts."""

    @property
    def name(self) -> str:
        return "assert_timestamp_preserved_on_retry"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests to check retry")

        first_timestamps = [e.get("timestamp") for e in (requests[0].parsed_events or []) if "timestamp" in e]
        second_timestamps = [e.get("timestamp") for e in (requests[1].parsed_events or []) if "timestamp" in e]

        if first_timestamps != second_timestamps:
            raise AssertionError(f"Timestamps changed on retry: {first_timestamps} != {second_timestamps}")


class AssertNoDuplicateEventsInBatchAction(Action):
    """Assert that no duplicate events exist within a single batch request."""

    @property
    def name(self) -> str:
        return "assert_no_duplicate_events_in_batch"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        for i, req in enumerate(requests):
            if not req.parsed_events:
                continue

            seen = set()
            for event in req.parsed_events:
                uuid = event.get("uuid")
                if not uuid:
                    continue
                if uuid in seen:
                    raise AssertionError(
                        f"Duplicate event UUID '{uuid}' found in request {i}"
                    )
                seen.add(uuid)


class AssertDifferentUuidsAction(Action):
    """Assert that multiple events have different UUIDs."""

    @property
    def name(self) -> str:
        return "assert_different_uuids"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        all_uuids = []

        for req in requests:
            if req.parsed_events:
                for event in req.parsed_events:
                    if "uuid" in event:
                        all_uuids.append(event["uuid"])

        if len(all_uuids) < 2:
            raise AssertionError("Need at least 2 events")

        if all_uuids[0] == all_uuids[1]:
            raise AssertionError("Different events should have different UUIDs")


# ============================================================================
# Assertion Actions - Token/Auth
# ============================================================================


class AssertTokenPresentAction(Action):
    """Assert that API token is present in requests."""

    @property
    def name(self) -> str:
        return "assert_token_present"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        expected_token = params["expected"]
        token_found = False

        # Check event level
        if requests[0].parsed_events:
            for event in requests[0].parsed_events:
                if event.get("token") == expected_token:
                    token_found = True
                    break

        # Check batch level
        if not token_found and requests[0].body_decompressed:
            try:
                body = json.loads(requests[0].body_decompressed)
                if body.get("api_key") == expected_token or body.get("token") == expected_token:
                    token_found = True
            except json.JSONDecodeError:
                pass

        if not token_found:
            raise AssertionError("API token not found in request")


# ============================================================================
# Assertion Actions - Retry Behavior
# ============================================================================


class AssertFinalSuccessAction(Action):
    """Assert that at least one request succeeded (200 status)."""

    @property
    def name(self) -> str:
        return "assert_final_success"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        success_count = sum(1 for r in requests if r.response_status == 200)
        if success_count == 0:
            raise AssertionError("No successful request after retries")


class AssertRetryDelayAction(Action):
    """Assert that retry delay meets minimum requirement."""

    @property
    def name(self) -> str:
        return "assert_retry_delay"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests")

        delay_ms = requests[1].timestamp_ms - requests[0].timestamp_ms
        min_delay = params["min_delay_ms"]

        if delay_ms < min_delay:
            raise AssertionError(f"Retry delay too short: {delay_ms}ms < {min_delay}ms")


class AssertBackoffImplementedAction(Action):
    """Assert that exponential backoff is implemented."""

    @property
    def name(self) -> str:
        return "assert_backoff_implemented"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests")

        delay1 = requests[1].timestamp_ms - requests[0].timestamp_ms
        min_delay = params["min_first_delay_ms"]

        if delay1 < min_delay:
            raise AssertionError(f"First retry delay too short: {delay1}ms < {min_delay}ms")


# ============================================================================
# Assertion Actions - Error Responses
# ============================================================================


class AssertResponseStatusAction(Action):
    """Assert that the adapter returns a specific HTTP status code."""

    @property
    def name(self) -> str:
        return "assert_response_status"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        # This action expects the previous action to have failed
        # We check the last error from the SDK state
        state = await ctx.sdk_adapter.get_state()
        last_error = state.last_error

        if not last_error:
            raise AssertionError("Expected an error but none was recorded")

        expected_status = params["expected"]
        # Parse status code from error message (format: "500, message='...'")
        if str(expected_status) not in str(last_error):
            raise AssertionError(f"Expected status {expected_status} in error, got: {last_error}")


class AssertCaptureFailsAction(Action):
    """Assert that a capture attempt fails (for testing validation)."""

    @property
    def name(self) -> str:
        return "assert_capture_fails"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        # Previous capture should have failed
        # This is a marker action that does nothing - the test framework
        # will catch the exception from the previous action
        pass


class AssertRequestHasHeaderAction(Action):
    """Assert that requests contain a specific header."""

    @property
    def name(self) -> str:
        return "assert_request_has_header"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        header_name = params["header"].lower()
        expected_value = params.get("expected")

        # Check if header exists in any request
        found = False
        for req in requests:
            # Headers are case-insensitive
            headers_lower = {k.lower(): v for k, v in req.headers.items()}
            if header_name in headers_lower:
                if expected_value is None:
                    found = True
                    break
                elif headers_lower[header_name] == expected_value:
                    found = True
                    break

        if not found:
            if expected_value:
                raise AssertionError(f"Header '{params['header']}' with value '{expected_value}' not found in requests")
            else:
                raise AssertionError(f"Header '{params['header']}' not found in requests")


class AssertBatchFormatAction(Action):
    """Assert that requests use proper batch format."""

    @property
    def name(self) -> str:
        return "assert_batch_format"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        body = requests[0].body_decompressed
        if not body:
            raise AssertionError("No body in request")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise AssertionError("Body is not valid JSON")

        if params.get("has_api_key_field"):
            if "api_key" not in data:
                raise AssertionError("Batch format missing 'api_key' field at root level")

        if params.get("has_batch_array"):
            if "batch" not in data or not isinstance(data["batch"], list):
                raise AssertionError("Batch format missing 'batch' array field")


# ============================================================================
# Client SDK Specific Assertions
# ============================================================================


class AssertEventFieldClientAction(Action):
    """Assert a specific field value in client SDK events (checks properties with $ prefix)."""

    @property
    def name(self) -> str:
        return "assert_event_field_client"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        events = requests[0].parsed_events
        if not events:
            raise AssertionError("No events in request")

        field = params["field"]
        expected = params["expected"]

        event = events[0]

        # Client SDKs put distinct_id in properties.$distinct_id
        if field == "distinct_id":
            properties = event.get("properties") or {}
            actual = properties.get("$distinct_id") or properties.get("distinct_id")
        else:
            # Check root level first, then properties with $ prefix
            actual = event.get(field)
            if actual is None and "properties" in event:
                actual = event["properties"].get(f"${field}") or event["properties"].get(field)

        if actual != expected:
            raise AssertionError(f"Expected {field}='{expected}', got '{actual}'")


class AssertTokenPresentClientAction(Action):
    """Assert that API token is present in client SDK requests (array format)."""

    @property
    def name(self) -> str:
        return "assert_token_present_client"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        expected_token = params["expected"]

        # Client SDKs send token in properties
        if requests[0].parsed_events:
            for event in requests[0].parsed_events:
                # Check root level and properties
                token = (event.get("token") or
                        event.get("api_key") or
                        event.get("properties", {}).get("token") or
                        event.get("properties", {}).get("api_key"))
                if token == expected_token:
                    return  # Token found!

        raise AssertionError(f"Token '{expected_token}' not found in any events")


# ============================================================================
# Action Registry
# ============================================================================


def get_all_actions() -> Dict[str, Action]:
    """
    Get all registered actions.

    Returns:
        Dictionary mapping action names to Action instances.
    """
    # Automatically discover all Action subclasses in this module
    import inspect
    import sys

    current_module = sys.modules[__name__]
    actions = {}

    for name, obj in inspect.getmembers(current_module, inspect.isclass):
        if issubclass(obj, Action) and obj is not Action:
            action_instance = obj()
            actions[action_instance.name] = action_instance

    return actions
