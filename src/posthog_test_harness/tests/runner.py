"""Test runner."""

import time
from typing import List, Optional

from ..contract import ContractExecutor
from ..types import TestSummary
from .context import TestContext
from .suites import ContractTestSuite


async def run_all_suites(
    ctx: TestContext, suite_names: Optional[List[str]] = None
) -> TestSummary:
    """
    Run all test suites from CONTRACT.yaml (or specific ones if provided).

    Args:
        ctx: Test context
        suite_names: Optional list of suite names to run (runs all if None)

    Returns:
        TestSummary with results from all suites
    """
    start_ms = int(time.time() * 1000)
    summary = TestSummary()

    # Load contract
    contract = ContractExecutor()
    all_suite_defs = contract.get_test_suites()

    # Determine which suites to run
    if suite_names:
        suites_to_run = {name: all_suite_defs[name] for name in suite_names if name in all_suite_defs}
    else:
        suites_to_run = all_suite_defs

    # Run each suite
    for suite_name in suites_to_run:
        print(f"Running test suite: {suite_name}")
        suite = ContractTestSuite(suite_name, contract)
        result = await suite.run(ctx)
        summary.add_suite(result)

    summary.duration_ms = int(time.time() * 1000) - start_ms
    return summary
