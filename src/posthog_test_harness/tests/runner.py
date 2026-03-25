"""Test runner."""

import asyncio
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from ..contract import ContractExecutor
from ..mock_server.state import MockServerState
from ..sdk_adapter.client import SDKAdapterClient
from ..types import TestResult, TestSuiteResult, TestSummary
from .context import TestContext
from .suites import ContractTestSuite


async def run_all_suites(
    ctx: TestContext,
    suite_names: Optional[List[str]] = None,
    sdk_type: str = "server",
    concurrency: int = 1,
    supports_parallel: bool = False,
    capabilities: Optional[List[str]] = None,
) -> TestSummary:
    """Run all test suites from CONTRACT.yaml.

    When concurrency > 1 and the adapter supports parallel execution,
    tests are dispatched concurrently with an asyncio.Semaphore limiting
    the number of in-flight tests. Otherwise, tests run sequentially
    using the same code path as before.

    Suites and tests that declare ``requires`` are skipped when the
    adapter's capabilities do not satisfy the requirement.
    """
    start_ms = int(time.time() * 1000)

    contract = ContractExecutor()
    all_suite_defs = contract.get_test_suites()

    if suite_names:
        suites_to_run = {name: all_suite_defs[name] for name in suite_names if name in all_suite_defs}
    else:
        suites_to_run = all_suite_defs

    parallel = concurrency > 1 and supports_parallel

    if parallel:
        summary = await _run_parallel(
            suites_to_run,
            contract,
            ctx,
            sdk_type,
            concurrency,
            capabilities,
        )
    else:
        summary = await _run_sequential(
            suites_to_run,
            contract,
            ctx,
            sdk_type,
            capabilities,
        )

    summary.duration_ms = int(time.time() * 1000) - start_ms
    return summary


async def _run_sequential(
    suites_to_run: Dict[str, Any],
    contract: ContractExecutor,
    ctx: TestContext,
    sdk_type: str,
    capabilities: Optional[List[str]] = None,
) -> TestSummary:
    """Run tests sequentially. Identical code path to the original implementation."""
    summary = TestSummary()

    for suite_name in suites_to_run:
        suite = ContractTestSuite(suite_name, contract)
        tests = suite.collect_tests(sdk_type, capabilities)
        if not tests:
            continue
        print(f"Running test suite: {suite_name} (SDK type: {sdk_type})")
        result = await suite.run(ctx, sdk_type=sdk_type, capabilities=capabilities)
        summary.add_suite(result)

    return summary


async def _run_parallel(
    suites_to_run: Dict[str, Any],
    contract: ContractExecutor,
    ctx: TestContext,
    sdk_type: str,
    concurrency: int,
    capabilities: Optional[List[str]] = None,
) -> TestSummary:
    """Run tests in parallel using asyncio.Semaphore + gather.

    Each test gets a unique test_id and its own scoped TestContext so that
    mock server state and adapter calls are isolated.
    """
    # Collect all tests across suites
    all_tests: List[Tuple[str, str, Dict[str, Any], ContractTestSuite]] = []
    for suite_name in suites_to_run:
        suite = ContractTestSuite(suite_name, contract)
        for test_name, test_def in suite.collect_tests(sdk_type, capabilities):
            all_tests.append((suite_name, test_name, test_def, suite))

    total = len(all_tests)
    print(f"Running {total} tests in parallel (concurrency={concurrency}, SDK type: {sdk_type})")

    # We need the raw (unwrapped) adapter and mock_server to create scoped contexts.
    # ctx in parallel mode should always have test_id=None (the base context).
    base_adapter = ctx.sdk_adapter
    base_mock_server = ctx.mock_server
    assert isinstance(base_adapter, SDKAdapterClient), "Parallel mode requires a base SDKAdapterClient"
    assert isinstance(base_mock_server, MockServerState), "Parallel mode requires a base MockServerState"

    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(
        suite_name: str,
        test_name: str,
        test_def: Dict[str, Any],
        suite: ContractTestSuite,
    ) -> Tuple[str, TestResult]:
        test_id = f"t-{uuid4()}"
        scoped_ctx = TestContext(
            sdk_adapter=base_adapter,
            mock_server=base_mock_server,
            mock_server_url=ctx.mock_server_url,
            api_key=ctx.api_key,
            test_id=test_id,
        )
        async with semaphore:
            result = await suite.run_single_test(test_name, test_def, scoped_ctx)
        return (suite_name, result)

    tasks = [run_one(sn, tn, td, s) for sn, tn, td, s in all_tests]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Group results by suite name
    suite_results: Dict[str, List[TestResult]] = defaultdict(list)
    for entry in results:
        if isinstance(entry, Exception):
            # Should not happen since run_single_test catches exceptions,
            # but handle gracefully
            suite_results["unknown"].append(TestResult(name="unknown", passed=False, duration_ms=0, message=str(entry)))
        else:
            suite_name, test_result = entry
            suite_results[suite_name].append(test_result)

    # Build summary preserving suite order
    summary = TestSummary()
    for suite_name in suites_to_run:
        if suite_name in suite_results:
            summary.add_suite(TestSuiteResult(name=suite_name, results=suite_results[suite_name]))

    return summary
