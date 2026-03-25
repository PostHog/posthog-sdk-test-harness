"""Contract-based test suite.

Loads tests from CONTRACT.yaml and executes them.
"""

import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from ...contract import ContractExecutor
from ...types import TestResult, TestSuiteResult
from ..context import TestContext
from .base import TestSuite


def _requirements_met(
    requires: Union[str, List[str]],
    capabilities: Set[str],
) -> bool:
    """Check if all requirements are satisfied by the given capabilities."""
    if isinstance(requires, str):
        return requires in capabilities
    return all(r in capabilities for r in requires)


class ContractTestSuite(TestSuite):
    """Test suite that loads tests from CONTRACT.yaml."""

    def __init__(self, suite_name: str, contract_executor: ContractExecutor):
        self._suite_name = suite_name
        self.contract = contract_executor
        self.suite_def = contract_executor.get_test_suites().get(suite_name, {})

    @property
    def name(self) -> str:
        return self._suite_name

    @property
    def required_adapter_endpoints(self) -> List[str]:
        return ["/capture", "/flush"]

    def collect_tests(
        self,
        sdk_type: str = "server",
        capabilities: Optional[List[str]] = None,
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Collect all tests applicable to the given SDK type and capabilities.

        Returns a list of (test_name, test_def) tuples.
        """
        cap_set = set(capabilities) if capabilities else set()

        # Check suite-level requires
        suite_requires = self.suite_def.get("requires")
        if suite_requires and not _requirements_met(suite_requires, cap_set):
            return []

        tests = []
        for category_name, category_def in self.suite_def.get("categories", {}).items():
            for test_def in category_def.get("tests", []):
                test_sdk_types = test_def.get("sdk_types", [])
                if test_sdk_types and sdk_type not in test_sdk_types:
                    continue
                # Check test-level requires
                test_requires = test_def.get("requires")
                if test_requires and not _requirements_met(test_requires, cap_set):
                    continue
                test_name = f"{category_name}.{test_def['name']}"
                tests.append((test_name, test_def))
        return tests

    async def run(
        self,
        ctx: TestContext,
        sdk_type: str = "server",
        capabilities: Optional[List[str]] = None,
    ) -> TestSuiteResult:
        """Run all tests in this suite from CONTRACT.yaml."""
        results = []
        for test_name, test_def in self.collect_tests(sdk_type, capabilities):
            result = await self._run_contract_test(test_name, test_def, ctx)
            results.append(result)
        return TestSuiteResult(name=self.name, results=results)

    async def run_single_test(self, test_name: str, test_def: Dict[str, Any], ctx: TestContext) -> TestResult:
        """Run a single test. Public interface for the parallel runner."""
        return await self._run_contract_test(test_name, test_def, ctx)

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
