"""PostHog SDK Test Harness.

A language-agnostic test harness for validating PostHog SDK compliance.
"""

__version__ = "0.1.0"

from .actions import Action, get_all_actions
from .contract import ContractExecutor
from .mock_server import MockServer, MockServerState
from .sdk_adapter import SDKAdapterClient, SDKAdapterInterface
from .tests import TestContext, run_all_suites
from .types import (
    CaptureRequest,
    HealthResponse,
    InitRequest,
    MockResponse,
    StateResponse,
    TestResult,
    TestSuiteResult,
    TestSummary,
)

__all__ = [
    "__version__",
    "Action",
    "get_all_actions",
    "ContractExecutor",
    "MockServer",
    "MockServerState",
    "SDKAdapterClient",
    "SDKAdapterInterface",
    "TestContext",
    "run_all_suites",
    "CaptureRequest",
    "HealthResponse",
    "InitRequest",
    "MockResponse",
    "StateResponse",
    "TestResult",
    "TestSuiteResult",
    "TestSummary",
]
