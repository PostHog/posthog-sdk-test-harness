"""Base test suite interface."""

import time
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, List

from ...types import TestResult, TestSuiteResult


class TestSuite(ABC):
    """Base class for test suites."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Test suite name (e.g., 'capture')."""
        pass

    @property
    @abstractmethod
    def required_adapter_endpoints(self) -> List[str]:
        """Endpoints the adapter must implement for this suite."""
        pass

    @abstractmethod
    async def run(self, ctx: any) -> TestSuiteResult:
        """
        Run all tests in this suite.

        Args:
            ctx: TestContext

        Returns:
            TestSuiteResult with all test results
        """
        pass

    async def run_test(
        self, name: str, test_fn: Callable[[], Awaitable[None]]
    ) -> TestResult:
        """
        Run a single test and return the result.

        Args:
            name: Test name
            test_fn: Async test function

        Returns:
            TestResult
        """
        start_ms = int(time.time() * 1000)
        try:
            await test_fn()
            duration_ms = int(time.time() * 1000) - start_ms
            return TestResult(name=name, passed=True, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int(time.time() * 1000) - start_ms
            return TestResult(
                name=name, passed=False, duration_ms=duration_ms, message=str(e)
            )
