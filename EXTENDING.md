# Extending the Test Harness

This guide explains how to add new test categories and actions to the test harness.

## Adding New Test Actions

Actions are the building blocks of tests. To add a new action, create a class in `src/posthog_test_harness/actions.py`:

```python
class MyCustomAction(Action):
    @property
    def name(self) -> str:
        return "my_custom_action"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        # Your action logic here
        value = params.get("some_param")

        # You have access to:
        # - ctx.sdk_adapter: SDK adapter client
        # - ctx.mock_server: Mock server state
        # - ctx.mock_server_url: Mock server URL

        # Example: Call adapter
        await ctx.sdk_adapter.capture(...)

        # Example: Check mock server
        requests = ctx.mock_server.get_requests()

        # Example: Assert something
        if value != expected:
            raise AssertionError("Value mismatch")
```

That's it! Your action is automatically registered and available in CONTRACT.yaml.

### Action Types

**Adapter Actions**: Call SDK adapter endpoints
```python
class CaptureAction(Action):
    @property
    def name(self) -> str:
        return "capture"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        return await ctx.sdk_adapter.capture(...)
```

**Mock Server Actions**: Configure mock server behavior
```python
class ConfigureMockResponsesAction(Action):
    @property
    def name(self) -> str:
        return "configure_mock_responses"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        ctx.mock_server.set_response_queue(...)
```

**Assertion Actions**: Verify SDK behavior
```python
class AssertSomethingAction(Action):
    @property
    def name(self) -> str:
        return "assert_something"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        actual = get_actual_value(ctx)
        expected = params["expected"]
        if actual != expected:
            raise AssertionError(f"Expected {expected}, got {actual}")
```

## Adding New Tests

Once you have actions, add tests to `CONTRACT.yaml`:

### 1. Document the Action

Add to the `test_actions` section:

```yaml
test_actions:
  my_custom_action:
    description: Does something custom
    params:
      some_param:
        type: string
        required: true
        description: A parameter
      another_param:
        type: integer
        optional: true
        default: 100
```

### 2. Write Tests

Add to the `test_suites` section:

```yaml
test_suites:
  my_new_suite:
    description: Tests for my new feature
    categories:
      my_category:
        description: Category description
        tests:
          - name: my_test
            description: Test description
            steps:
              - action: init
              - action: my_custom_action
                params:
                  some_param: value
              - action: assert_something
                params:
                  expected: value
```

That's it! No Python code needed.

## Adding New Mock Server Endpoints

To support new PostHog endpoints (e.g., `/decide` for feature flags):

### 1. Create Endpoint Handler

Create `src/posthog_test_harness/mock_server/endpoints/decide.py`:

```python
from typing import Any, Callable, Dict, List, Tuple
from flask import Request
from .base import EndpointHandler

class DecideEndpoint(EndpointHandler):
    """Handles /decide endpoint for feature flags."""

    def routes(self) -> List[Tuple[str, str, Callable]]:
        return [
            ("/decide", "POST", self.handle_request),
            ("/decide/", "POST", self.handle_request),
        ]

    def handle_request(self, request: Request) -> Tuple[Dict[str, Any], int, Dict[str, str]]:
        # Return feature flags
        return {
            "featureFlags": {},
            "errorsWhileComputingFlags": False
        }, 200, {}
```

### 2. Register Endpoint

In `src/posthog_test_harness/mock_server/server.py`:

```python
from .endpoints.decide import DecideEndpoint

# In _setup_routes method:
handlers: List[EndpointHandler] = [
    CaptureEndpoint(),
    DecideEndpoint(),  # Add your endpoint
]
```

### 3. Update Adapter Interface

Add methods to `src/posthog_test_harness/sdk_adapter/interface.py`:

```python
@abstractmethod
async def get_feature_flag(self, key: str, distinct_id: str) -> dict:
    """Get feature flag value."""
    pass
```

### 4. Implement in Client

Add to `src/posthog_test_harness/sdk_adapter/client.py`:

```python
async def get_feature_flag(self, key: str, distinct_id: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{self.base_url}/get_feature_flag",
            json={"key": key, "distinct_id": distinct_id},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
```

### 5. Create Actions and Tests

Follow the "Adding New Tests" section above.

## Example: Adding Feature Flags Support

Here's a complete example of adding feature flags:

**1. Action** (`actions.py`):
```python
class GetFeatureFlagAction(Action):
    @property
    def name(self) -> str:
        return "get_feature_flag"

    async def execute(self, params: Dict[str, Any], ctx: "TestContext") -> Any:
        # Assumes you added this method to the client
        return await ctx.sdk_adapter.get_feature_flag(
            key=params["key"],
            distinct_id=params["distinct_id"]
        )
```

**2. Tests** (`CONTRACT.yaml`):
```yaml
test_suites:
  feature_flags:
    description: Tests for feature flags
    categories:
      evaluation:
        description: Feature flag evaluation
        tests:
          - name: returns_flag_value
            description: SDK should return feature flag value
            steps:
              - action: init
              - action: configure_mock_responses
                params:
                  responses:
                    - status_code: 200
                      body: '{"featureFlags": {"my-flag": true}}'
              - action: get_feature_flag
                params:
                  key: my-flag
                  distinct_id: user123
              - action: assert_flag_value
                params:
                  expected: true
```

## Best Practices

1. **Keep actions simple** - One responsibility per action
2. **Use clear names** - `assert_event_has_uuid` not `check_uuid`
3. **Add good error messages** - Help users debug failures
4. **Test your actions** - Write unit tests for complex actions
5. **Document in CONTRACT.yaml** - Keep the contract up-to-date

## Questions?

- See [examples/minimal_adapter/](examples/minimal_adapter/) for a complete example
- See [CONTRACT.yaml](CONTRACT.yaml) for the full specification
- See [ADAPTER_GUIDE.md](ADAPTER_GUIDE.md) for adapter implementation details
