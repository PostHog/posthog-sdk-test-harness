"""Contract-based test suite.

Loads tests from CONTRACT.yaml and executes them.
"""

import time
from typing import Any, Dict, List

from ...contract import ContractExecutor
from ...types import TestResult, TestSuiteResult
from ..context import TestContext
from .base import TestSuite


class ContractTestSuite(TestSuite):
    """Test suite that loads tests from CONTRACT.yaml."""

    def __init__(self, suite_name: str, contract_executor: ContractExecutor):
        """
        Initialize contract-based test suite.

        Args:
            suite_name: Name of the suite from CONTRACT.yaml (e.g., 'capture')
            contract_executor: Contract executor instance
        """
        self._suite_name = suite_name
        self.contract = contract_executor
        self.suite_def = contract_executor.get_test_suites().get(suite_name, {})

    @property
    def name(self) -> str:
        return self._suite_name

    @property
    def required_adapter_endpoints(self) -> List[str]:
        # Extract required endpoints from test definitions
        # For now, return basic capture endpoints
        return ["/capture", "/flush"]

    async def run(self, ctx: TestContext, sdk_type: str = "server") -> TestSuiteResult:
        """Run all tests in this suite from CONTRACT.yaml."""
        results = []

        # Iterate through all categories and tests
        for category_name, category_def in self.suite_def.get("categories", {}).items():
            for test_def in category_def.get("tests", []):
                # Check if test should run for this SDK type
                test_sdk_types = test_def.get("sdk_types", [])
                if test_sdk_types and sdk_type not in test_sdk_types:
                    # Skip this test - not applicable to this SDK type
                    continue

                test_name = f"{category_name}.{test_def['name']}"
                result = await self._run_contract_test(test_name, test_def, ctx)
                results.append(result)

        return TestSuiteResult(name=self.name, results=results)

    async def _run_contract_test(self, test_name: str, test_def: Dict[str, Any], ctx: TestContext) -> TestResult:
        """Run a single test from the contract."""
        start_ms = int(time.time() * 1000)

        try:
            await self.contract.run_test(test_def, ctx)
            duration_ms = int(time.time() * 1000) - start_ms
            return TestResult(name=test_name, passed=True, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int(time.time() * 1000) - start_ms
            return TestResult(
                name=test_name,
                passed=False,
                duration_ms=duration_ms,
                message=str(e),
            )
