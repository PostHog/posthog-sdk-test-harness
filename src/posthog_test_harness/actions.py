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
import re
import uuid as uuid_module
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from .types import CaptureRequest, FeatureFlagRequest, InitRequest, MockResponse


def _is_rfc3339(value: str) -> bool:
    """Check if a string is a valid RFC 3339 timestamp."""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


def _is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid_module.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


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


class GetFeatureFlagAction(Action):
    """Evaluate a feature flag via the adapter."""

    @property
    def name(self) -> str:
        return "get_feature_flag"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        return await ctx.sdk_adapter.get_feature_flag(
            FeatureFlagRequest(
                key=params["key"],
                distinct_id=params["distinct_id"],
                person_properties=params.get("person_properties"),
                groups=params.get("groups"),
                group_properties=params.get("group_properties"),
                disable_geoip=params.get("disable_geoip"),
            )
        )


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
                v1_event_results=r.get("v1_event_results"),
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
                raise AssertionError(
                    f"Expected {prop_name}='{expected}', got '{actual}'. Available properties: {available_props}"
                )


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
                    raise AssertionError(f"Duplicate event UUID '{uuid}' found in request {i}")
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
    """Assert that at least one request succeeded.

    By default checks for 200. Pass success_statuses to override
    (e.g. [204] for V1 capture).
    """

    @property
    def name(self) -> str:
        return "assert_final_success"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        ok = set(params.get("success_statuses", [200]))
        success_count = sum(1 for r in requests if r.response_status in ok)
        if success_count == 0:
            statuses = [r.response_status for r in requests]
            raise AssertionError(f"No successful request (expected one of {sorted(ok)}). " f"Got statuses: {statuses}")


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
# Assertion Actions - Decide (Feature Flags)
# ============================================================================


class AssertDecideRequestCountAction(Action):
    """Assert exact number of /decide requests made to mock server."""

    @property
    def name(self) -> str:
        return "assert_decide_request_count"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        decide_requests = [r for r in requests if "/decide" in r.path]
        actual = len(decide_requests)
        expected = params["expected"]
        if actual != expected:
            raise AssertionError(f"Expected {expected} /decide requests, got {actual}")


class AssertDecideRequestFieldAction(Action):
    """Assert a field value in the /decide request body, with dot notation for nested fields."""

    @property
    def name(self) -> str:
        return "assert_decide_request_field"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        decide_requests = [r for r in requests if "/decide" in r.path]
        if not decide_requests:
            raise AssertionError("No /decide requests recorded")

        body_str = decide_requests[0].body_decompressed
        if not body_str:
            raise AssertionError("No body in /decide request")

        try:
            body = json.loads(body_str)
        except json.JSONDecodeError:
            raise AssertionError("Body of /decide request is not valid JSON")

        field = params["field"]
        expected = params["expected"]

        # Support dot notation for nested fields (e.g., "person_properties.$device_id")
        parts = field.split(".")
        current = body
        for part in parts:
            if not isinstance(current, dict):
                raise AssertionError(
                    f"Cannot traverse into non-dict at '{part}' in field path '{field}'. "
                    f"Value is: {current!r}"
                )
            if part not in current:
                raise AssertionError(
                    f"Field '{part}' not found in /decide request body at path '{field}'. "
                    f"Available keys: {list(current.keys())}"
                )
            current = current[part]

        if current != expected:
            raise AssertionError(
                f"Expected {field}={expected!r}, got {current!r}"
            )


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
                token = (
                    event.get("token")
                    or event.get("api_key")
                    or event.get("properties", {}).get("token")
                    or event.get("properties", {}).get("api_key")
                )
                if token == expected_token:
                    return  # Token found!

        raise AssertionError(f"Token '{expected_token}' not found in any events")


# ============================================================================
# V1 Path Assertions
# ============================================================================


class AssertRequestPathAction(Action):
    """Assert all capture requests hit a specific path."""

    @property
    def name(self) -> str:
        return "assert_request_path"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        expected = params["expected"].rstrip("/")
        for i, req in enumerate(requests):
            actual = req.path.rstrip("/")
            if actual != expected:
                raise AssertionError(f"Request {i} path '{req.path}' != expected '{params['expected']}'")


class AssertNoRequestsToPathsAction(Action):
    """Assert no requests were made to any of the listed paths."""

    @property
    def name(self) -> str:
        return "assert_no_requests_to_paths"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        forbidden = {p.rstrip("/") for p in params["paths"]}

        for req in requests:
            normalized = req.path.rstrip("/")
            if normalized in forbidden:
                raise AssertionError(f"Unexpected request to forbidden path '{req.path}'")


# ============================================================================
# V1 Header Assertions
# ============================================================================


class AssertHeaderValueMatchesAction(Action):
    """Assert a header value matches a regex pattern."""

    @property
    def name(self) -> str:
        return "assert_header_value_matches"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        header_name = params["header"].lower()
        pattern = params["pattern"]
        optional = params.get("optional", False)

        headers = requests[0].headers
        value = headers.get(header_name)
        if value is None:
            if optional:
                return
            raise AssertionError(f"Header '{params['header']}' not found in request")
        if not re.match(pattern, value):
            raise AssertionError(f"Header '{params['header']}' value '{value}' " f"does not match pattern '{pattern}'")


class AssertHeaderIsUuidAction(Action):
    """Assert a header value is a valid UUID."""

    @property
    def name(self) -> str:
        return "assert_header_is_uuid"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        header_name = params["header"].lower()
        value = requests[0].headers.get(header_name)
        if value is None:
            raise AssertionError(f"Header '{params['header']}' not found")
        if not _is_valid_uuid(value):
            raise AssertionError(f"Header '{params['header']}' value '{value}' " f"is not a valid UUID")


class AssertHeaderIsRfc3339Action(Action):
    """Assert a header value is a valid RFC 3339 timestamp."""

    @property
    def name(self) -> str:
        return "assert_header_is_rfc3339"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        header_name = params["header"].lower()
        value = requests[0].headers.get(header_name)
        if value is None:
            raise AssertionError(f"Header '{params['header']}' not found")
        if not _is_rfc3339(value):
            raise AssertionError(f"Header '{params['header']}' value '{value}' " f"is not valid RFC 3339")


class AssertHeaderIsIntegerAction(Action):
    """Assert a header value is a valid integer, optionally check exact value."""

    @property
    def name(self) -> str:
        return "assert_header_is_integer"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        header_name = params["header"].lower()
        value = requests[0].headers.get(header_name)
        if value is None:
            raise AssertionError(f"Header '{params['header']}' not found")
        try:
            int_val = int(value)
        except ValueError:
            raise AssertionError(f"Header '{params['header']}' value '{value}' " f"is not a valid integer")

        if "expected" in params and int_val != params["expected"]:
            raise AssertionError(f"Header '{params['header']}' = {int_val}, " f"expected {params['expected']}")


class AssertAuthorizationAndTokenMatchAction(Action):
    """Assert Authorization bearer value matches PostHog-Api-Token header.

    If Authorization header is absent, the assertion passes (optional header).
    If present, it must use Bearer scheme and the token must match PostHog-Api-Token.
    """

    @property
    def name(self) -> str:
        return "assert_authorization_and_token_match"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        headers = requests[0].headers
        auth = headers.get("authorization")
        token_header = headers.get("posthog-api-token")

        if auth is None:
            return

        if not auth.startswith("Bearer "):
            raise AssertionError(
                f"Authorization header present but not Bearer scheme: '{auth}'"
            )

        bearer_value = auth[len("Bearer ") :]
        if bearer_value != token_header:
            raise AssertionError(
                f"Bearer token '{bearer_value}' != "
                f"PostHog-Api-Token '{token_header}'"
            )


# ============================================================================
# V1 Body Assertions
# ============================================================================


class AssertV1BodyFormatAction(Action):
    """Assert V1 batch body has created_at (RFC 3339) and batch (non-empty array)."""

    @property
    def name(self) -> str:
        return "assert_v1_body_format"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        body = requests[0].body_decompressed
        if not body:
            raise AssertionError("No body in request")

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AssertionError("Body is not valid JSON") from exc

        if "created_at" not in data:
            raise AssertionError("V1 body missing 'created_at' field")

        if not _is_rfc3339(str(data["created_at"])):
            raise AssertionError(f"V1 body 'created_at' is not valid RFC 3339: " f"{data['created_at']}")

        if "batch" not in data:
            raise AssertionError("V1 body missing 'batch' field")

        if not isinstance(data["batch"], list) or len(data["batch"]) == 0:
            raise AssertionError("V1 body 'batch' must be a non-empty array")


class AssertBodyFieldAbsentAction(Action):
    """Assert specific fields are absent from the request body root."""

    @property
    def name(self) -> str:
        return "assert_body_field_absent"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        body = requests[0].body_decompressed
        if not body:
            return  # No body means fields are absent

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return  # Not JSON means fields are absent

        fields = params["fields"]
        for field in fields:
            if field in data:
                raise AssertionError(f"Body should not contain '{field}' field")


class AssertV1CreatedAtRecentAction(Action):
    """Assert V1 body created_at is within max_age_seconds of current time."""

    @property
    def name(self) -> str:
        return "assert_v1_created_at_recent"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        body = requests[0].body_decompressed
        if not body:
            raise AssertionError("No body in request")

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AssertionError("Body is not valid JSON") from exc

        created_at_str = data.get("created_at")
        if not created_at_str:
            raise AssertionError("V1 body missing 'created_at'")

        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AssertionError(f"Invalid created_at: {created_at_str}") from exc

        now = datetime.now(timezone.utc)
        diff = abs((now - created_at).total_seconds())
        max_age = params.get("max_age_seconds", 5)

        if diff > max_age:
            raise AssertionError(f"created_at is {diff:.1f}s from now, " f"exceeds max {max_age}s")


# ============================================================================
# V1 Event Assertions
# ============================================================================


class AssertV1EventFormatAction(Action):
    """Assert all events have V1 required root fields."""

    @property
    def name(self) -> str:
        return "assert_v1_event_format"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        events = requests[0].parsed_events
        if not events:
            raise AssertionError("No events in request")

        required = {"event", "uuid", "distinct_id", "timestamp"}
        for i, event in enumerate(events):
            missing = required - set(event.keys())
            if missing:
                raise AssertionError(f"Event {i} missing required V1 fields: " f"{sorted(missing)}")


class AssertEventFieldIsRfc3339Action(Action):
    """Assert an event field is a valid RFC 3339 timestamp."""

    @property
    def name(self) -> str:
        return "assert_event_field_is_rfc3339"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests or not requests[0].parsed_events:
            raise AssertionError("No events found")

        field = params["field"]
        for i, event in enumerate(requests[0].parsed_events):
            value = event.get(field)
            if value is None:
                raise AssertionError(f"Event {i} missing '{field}' field")
            if not _is_rfc3339(str(value)):
                raise AssertionError(f"Event {i} field '{field}' value '{value}' " f"is not valid RFC 3339")


class AssertEventFieldIsStringAction(Action):
    """Assert an event field is a string."""

    @property
    def name(self) -> str:
        return "assert_event_field_is_string"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests or not requests[0].parsed_events:
            raise AssertionError("No events found")

        field = params["field"]
        event = requests[0].parsed_events[0]
        value = event.get(field)
        if value is None:
            raise AssertionError(f"Event missing '{field}' field")
        if not isinstance(value, str):
            raise AssertionError(f"Event field '{field}' is {type(value).__name__}, " f"expected string")


class AssertEventFieldNotInPropertiesAction(Action):
    """Assert a field exists at event root but not inside properties."""

    @property
    def name(self) -> str:
        return "assert_event_field_not_in_properties"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests or not requests[0].parsed_events:
            raise AssertionError("No events found")

        field = params["field"]
        event = requests[0].parsed_events[0]

        if field not in event:
            raise AssertionError(f"Event missing '{field}' at root level")

        props = event.get("properties", {})
        if isinstance(props, dict) and field in props:
            raise AssertionError(f"'{field}' should be at event root, " f"not in properties")


class AssertEventPropertyIsObjectAction(Action):
    """Assert an event property value is an object (dict)."""

    @property
    def name(self) -> str:
        return "assert_event_property_is_object"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests or not requests[0].parsed_events:
            raise AssertionError("No events found")

        prop_name = params["property"]
        event = requests[0].parsed_events[0]
        props = event.get("properties", {})

        if prop_name not in props:
            raise AssertionError(f"Event missing property '{prop_name}'")
        if not isinstance(props[prop_name], dict):
            raise AssertionError(f"Property '{prop_name}' is " f"{type(props[prop_name]).__name__}, expected object")


# ============================================================================
# V1 Header Retry Assertions
# ============================================================================


class AssertAttemptHeaderIncrementsAction(Action):
    """Assert PostHog-Attempt increments across retry attempts (1, 2, 3...)."""

    @property
    def name(self) -> str:
        return "assert_attempt_header_increments"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests to check retry")

        for i, req in enumerate(requests):
            value = req.headers.get("posthog-attempt")
            if value is None:
                raise AssertionError(f"Request {i} missing PostHog-Attempt header")
            expected = i + 1
            actual = int(value)
            if actual != expected:
                raise AssertionError(f"Request {i} PostHog-Attempt = {actual}, " f"expected {expected}")


class AssertRequestIdPreservedOnRetryAction(Action):
    """Assert PostHog-Request-Id is identical across retry attempts."""

    @property
    def name(self) -> str:
        return "assert_request_id_preserved_on_retry"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests to check retry")

        first_id = requests[0].headers.get("posthog-request-id")
        if not first_id:
            raise AssertionError("First request missing PostHog-Request-Id")

        for i, req in enumerate(requests[1:], start=1):
            rid = req.headers.get("posthog-request-id")
            if rid != first_id:
                raise AssertionError(f"Request {i} PostHog-Request-Id '{rid}' != " f"first request '{first_id}'")


class AssertAttemptTimestampChangesOnRetryAction(Action):
    """Assert PostHog-Attempt-Timestamp differs between retry attempts."""

    @property
    def name(self) -> str:
        return "assert_attempt_timestamp_changes_on_retry"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests to check retry")

        first_ts = requests[0].headers.get("posthog-attempt-timestamp")
        if not first_ts:
            raise AssertionError("First request missing PostHog-Attempt-Timestamp")

        second_ts = requests[1].headers.get("posthog-attempt-timestamp")
        if not second_ts:
            raise AssertionError("Second request missing PostHog-Attempt-Timestamp")

        if first_ts == second_ts:
            raise AssertionError(
                f"PostHog-Attempt-Timestamp should change on retry, "
                f"but both are '{first_ts}'"
            )


class AssertDifferentRequestIdsAction(Action):
    """Assert two independent requests have different PostHog-Request-Id values."""

    @property
    def name(self) -> str:
        return "assert_different_request_ids"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests")

        first_id = requests[0].headers.get("posthog-request-id")
        second_id = requests[1].headers.get("posthog-request-id")

        if not first_id or not second_id:
            raise AssertionError("Requests missing PostHog-Request-Id headers")

        if first_id == second_id:
            raise AssertionError(
                f"Different requests should have different " f"PostHog-Request-Id, but both are '{first_id}'"
            )


class AssertHeaderAbsentAction(Action):
    """Assert a specific header is NOT present in the request."""

    @property
    def name(self) -> str:
        return "assert_header_absent"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        header_name = params["header"].lower()
        value = requests[0].headers.get(header_name)
        if value is not None:
            raise AssertionError(f"Header '{params['header']}' should be absent " f"but has value '{value}'")


class AssertCompressedBodyDecompressibleAction(Action):
    """Assert the mock server could decompress the body and parse events."""

    @property
    def name(self) -> str:
        return "assert_compressed_body_decompressible"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        req = requests[0]
        encoding = req.headers.get("content-encoding")
        if not encoding:
            raise AssertionError("No Content-Encoding header found")

        if not req.body_decompressed:
            raise AssertionError(f"Body with Content-Encoding '{encoding}' " f"could not be decompressed")

        if not req.parsed_events:
            raise AssertionError("Decompressed body did not contain parseable events")


class AssertPartialBatchRetryPruningAction(Action):
    """Assert the retried batch only contains events with result "retry" from the partial response."""

    @property
    def name(self) -> str:
        return "assert_partial_batch_retry_pruning"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if len(requests) < 2:
            raise AssertionError("Need at least 2 requests for partial batch retry check")

        # Parse the first response body for per-event results
        first_response_body = requests[0].response_body
        if not first_response_body:
            raise AssertionError("First response has no body (expected partial batch)")

        try:
            partial = json.loads(first_response_body)
        except json.JSONDecodeError as exc:
            raise AssertionError("First response body is not valid JSON") from exc

        results = partial.get("results", [])
        if not results:
            raise AssertionError("First response has no 'results' array")

        # Match results to first request's events by position
        first_events = requests[0].parsed_events or []

        retry_uuids: set[str] = set()
        no_retry_uuids: set[str] = set()
        for i, r in enumerate(results):
            event_uuid = first_events[i].get("uuid", "") if i < len(first_events) else ""
            if r.get("result") == "retry":
                retry_uuids.add(event_uuid)
            else:
                no_retry_uuids.add(event_uuid)

        # Check the retried batch (second request)
        second_events = requests[1].parsed_events or []
        second_uuids = {e.get("uuid") for e in second_events}

        unexpected = second_uuids & no_retry_uuids
        if unexpected:
            raise AssertionError(
                f"Retried batch contains events that should not be "
                f"retried (ok/drop): {unexpected}"
            )

        missing = retry_uuids - second_uuids
        if missing:
            raise AssertionError(
                f"Retried batch is missing events that should be "
                f"retried: {missing}"
            )


class AssertEventsInBatchCountAction(Action):
    """Assert the number of events in the first request's batch."""

    @property
    def name(self) -> str:
        return "assert_events_in_batch_count"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        requests = ctx.mock_server.get_requests()
        if not requests:
            raise AssertionError("No requests recorded")

        events = requests[0].parsed_events or []
        expected = params["expected"]
        operator = params.get("operator", "eq")

        if operator == "gte":
            if len(events) < expected:
                raise AssertionError(f"Expected >= {expected} events in batch, " f"got {len(events)}")
        elif len(events) != expected:
            raise AssertionError(f"Expected {expected} events in batch, " f"got {len(events)}")


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
