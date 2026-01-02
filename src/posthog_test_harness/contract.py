"""Contract-based test execution.

This module parses CONTRACT.yaml and executes tests defined in it.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

import yaml

from .actions import get_all_actions

if TYPE_CHECKING:
    from .tests.context import TestContext


class IncludeLoader(yaml.SafeLoader):
    """YAML loader that supports !include directive."""

    def __init__(self, stream):
        self._root = Path(stream.name).parent
        super().__init__(stream)


def include_constructor(loader: IncludeLoader, node: yaml.Node) -> Any:
    """Construct included YAML file."""
    filepath = loader._root / loader.construct_scalar(node)
    with open(filepath) as f:
        return yaml.load(f, IncludeLoader)


# Register the !include constructor
IncludeLoader.add_constructor("!include", include_constructor)


class ContractExecutor:
    """Executes tests defined in CONTRACT.yaml."""

    def __init__(self, contract_path: str = "CONTRACT.yaml"):
        """Initialize the executor with a contract file."""
        self.contract_path = Path(contract_path)
        self.contract: Dict[str, Any] = {}
        self.actions = get_all_actions()
        self._load_contract()

    def _load_contract(self) -> None:
        """Load and parse the CONTRACT.yaml file with !include support."""
        if not self.contract_path.exists():
            # Try relative to this file
            self.contract_path = Path(__file__).parent.parent.parent / "CONTRACT.yaml"

        with open(self.contract_path) as f:
            self.contract = yaml.load(f, IncludeLoader)

    async def execute_action(self, action_name: str, params: Dict[str, Any], ctx: "TestContext") -> Any:
        """
        Execute a single test action.

        Args:
            action_name: Action name (e.g., 'init', 'capture', 'assert_request_count')
            params: Action parameters
            ctx: Test context

        Returns:
            Action result (if any)

        Raises:
            ValueError: If action is not found
        """
        if action_name not in self.actions:
            raise ValueError(f"Unknown action: {action_name}")

        action = self.actions[action_name]
        return await action.execute(params, ctx)

    async def run_test(self, test_def: Dict[str, Any], ctx: "TestContext") -> None:
        """
        Run a single test from the contract.

        Args:
            test_def: Test definition from CONTRACT.yaml
            ctx: Test context
        """
        # Reset state before test
        await ctx.reset()

        # Execute each step
        for step in test_def.get("steps", []):
            action = step["action"]
            # All params must be under 'params' key
            params = step.get("params", {})

            try:
                await self.execute_action(action, params, ctx)
            except KeyError as e:
                raise ValueError(
                    f"Missing required parameter {e} for action '{action}'. " f"Available params: {list(params.keys())}"
                ) from e

    def get_test_suites(self) -> Dict[str, Any]:
        """Get all test suites from the contract."""
        return self.contract.get("test_suites", {})

    def get_test_actions(self) -> Dict[str, Any]:
        """Get all test action definitions from the contract."""
        # Merge adapter_actions and test_actions
        adapter_actions = self.contract.get("adapter_actions", {})
        test_actions = self.contract.get("test_actions", {})
        return {**adapter_actions, **test_actions}
