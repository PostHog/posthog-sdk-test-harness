"""Tests for the test runner, including parallel dispatch."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.context import TestContext
from posthog_test_harness.tests.runner import run_all_suites


class TestSequentialMode:
    """Verify sequential mode (concurrency=1) works as before."""

    @pytest.mark.asyncio
    async def test_sequential_is_default(self):
        """When concurrency=1, tests run sequentially via _run_sequential."""
        adapter = SDKAdapterClient("http://localhost:8080")
        state = MockServerState()
        ctx = TestContext(adapter, state, "http://localhost:8081")

        with patch("posthog_test_harness.tests.runner.ContractExecutor") as mock_ce:
            mock_contract = MagicMock()
            mock_contract.get_test_suites.return_value = {
                "suite1": {
                    "categories": {
                        "cat": {
                            "tests": [
                                {"name": "t1", "sdk_types": ["server"], "steps": []},
                                {"name": "t2", "sdk_types": ["server"], "steps": []},
                            ]
                        }
                    }
                }
            }
            mock_contract.run_test = AsyncMock()
            mock_ce.return_value = mock_contract

            summary = await run_all_suites(ctx, concurrency=1, supports_parallel=False)

        assert summary.total == 2
        assert summary.passed == 2

    @pytest.mark.asyncio
    async def test_parallel_fallback_when_not_supported(self):
        """When concurrency>1 but supports_parallel=False, runs sequentially."""
        adapter = SDKAdapterClient("http://localhost:8080")
        state = MockServerState()
        ctx = TestContext(adapter, state, "http://localhost:8081")

        with patch("posthog_test_harness.tests.runner.ContractExecutor") as mock_ce:
            mock_contract = MagicMock()
            mock_contract.get_test_suites.return_value = {
                "suite1": {
                    "categories": {
                        "cat": {
                            "tests": [
                                {"name": "t1", "sdk_types": ["server"], "steps": []},
                            ]
                        }
                    }
                }
            }
            mock_contract.run_test = AsyncMock()
            mock_ce.return_value = mock_contract

            summary = await run_all_suites(ctx, concurrency=4, supports_parallel=False)

        assert summary.total == 1
        assert summary.passed == 1


class TestParallelMode:
    """Verify parallel mode dispatches tests concurrently."""

    @pytest.mark.asyncio
    async def test_parallel_creates_scoped_contexts(self):
        """When parallel mode is active, each test gets a unique test_id."""
        adapter = SDKAdapterClient("http://localhost:8080")
        state = MockServerState()
        ctx = TestContext(adapter, state, "http://localhost:8081")

        observed_test_ids = []

        with patch("posthog_test_harness.tests.runner.ContractExecutor") as mock_ce:
            mock_contract = MagicMock()
            mock_contract.get_test_suites.return_value = {
                "suite1": {
                    "categories": {
                        "cat": {
                            "tests": [
                                {"name": "t1", "sdk_types": ["server"], "steps": []},
                                {"name": "t2", "sdk_types": ["server"], "steps": []},
                                {"name": "t3", "sdk_types": ["server"], "steps": []},
                            ]
                        }
                    }
                }
            }

            async def fake_run_test(test_def, test_ctx):
                observed_test_ids.append(test_ctx.test_id)

            mock_contract.run_test = fake_run_test
            mock_ce.return_value = mock_contract

            summary = await run_all_suites(ctx, concurrency=3, supports_parallel=True)

        assert summary.total == 3
        assert summary.passed == 3

        # Each test should have gotten a unique test_id
        assert len(observed_test_ids) == 3
        assert len(set(observed_test_ids)) == 3  # All unique
        assert all(tid.startswith("t-") for tid in observed_test_ids)

    @pytest.mark.asyncio
    async def test_parallel_results_grouped_by_suite(self):
        """Parallel results are correctly grouped back into suite results."""
        adapter = SDKAdapterClient("http://localhost:8080")
        state = MockServerState()
        ctx = TestContext(adapter, state, "http://localhost:8081")

        with patch("posthog_test_harness.tests.runner.ContractExecutor") as mock_ce:
            mock_contract = MagicMock()
            mock_contract.get_test_suites.return_value = {
                "suite_a": {
                    "categories": {
                        "cat": {
                            "tests": [
                                {"name": "t1", "sdk_types": ["server"], "steps": []},
                            ]
                        }
                    }
                },
                "suite_b": {
                    "categories": {
                        "cat": {
                            "tests": [
                                {"name": "t2", "sdk_types": ["server"], "steps": []},
                                {"name": "t3", "sdk_types": ["server"], "steps": []},
                            ]
                        }
                    }
                },
            }
            mock_contract.run_test = AsyncMock()
            mock_ce.return_value = mock_contract

            summary = await run_all_suites(ctx, concurrency=4, supports_parallel=True)

        assert len(summary.suites) == 2
        suite_names = [s.name for s in summary.suites]
        assert "suite_a" in suite_names
        assert "suite_b" in suite_names

        suite_a = next(s for s in summary.suites if s.name == "suite_a")
        suite_b = next(s for s in summary.suites if s.name == "suite_b")
        assert suite_a.total == 1
        assert suite_b.total == 2

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """The semaphore actually limits concurrent test execution."""
        adapter = SDKAdapterClient("http://localhost:8080")
        state = MockServerState()
        ctx = TestContext(adapter, state, "http://localhost:8081")

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        with patch("posthog_test_harness.tests.runner.ContractExecutor") as mock_ce:
            mock_contract = MagicMock()
            mock_contract.get_test_suites.return_value = {
                "suite1": {
                    "categories": {
                        "cat": {
                            "tests": [
                                {"name": f"t{i}", "sdk_types": ["server"], "steps": []}
                                for i in range(10)
                            ]
                        }
                    }
                }
            }

            async def slow_run_test(test_def, test_ctx):
                nonlocal max_concurrent, current_concurrent
                async with lock:
                    current_concurrent += 1
                    max_concurrent = max(max_concurrent, current_concurrent)
                await asyncio.sleep(0.01)
                async with lock:
                    current_concurrent -= 1

            mock_contract.run_test = slow_run_test
            mock_ce.return_value = mock_contract

            summary = await run_all_suites(ctx, concurrency=3, supports_parallel=True)

        assert summary.total == 10
        assert summary.passed == 10
        # Semaphore should have limited to 3
        assert max_concurrent <= 3
