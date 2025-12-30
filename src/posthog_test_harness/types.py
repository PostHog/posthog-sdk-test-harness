"""Shared types for the test harness."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class InitRequest:
    """SDK initialization request."""

    api_key: str
    host: str
    flush_at: Optional[int] = None
    flush_interval_ms: Optional[int] = None
    max_retries: Optional[int] = None
    enable_compression: Optional[bool] = None


@dataclass
class CaptureRequest:
    """Event capture request."""

    distinct_id: str
    event: str
    properties: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None


@dataclass
class HealthResponse:
    """SDK adapter health response."""

    sdk_name: str
    sdk_version: str
    adapter_version: str


@dataclass
class StateResponse:
    """SDK adapter state response."""

    pending_events: int
    total_events_captured: int
    total_events_sent: int
    total_retries: int
    last_error: Optional[str]
    requests_made: List[Dict[str, Any]]


@dataclass
class MockResponse:
    """Configured mock server response."""

    status_code: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[str] = None


@dataclass
class RecordedRequest:
    """A request recorded by the mock server."""

    timestamp_ms: int
    method: str
    path: str
    headers: Dict[str, str]
    query_params: Dict[str, str]
    body_raw: bytes
    body_decompressed: Optional[str]
    parsed_events: Optional[List[Dict[str, Any]]]
    response_status: int
    response_headers: Dict[str, str]
    response_body: Optional[str]


@dataclass
class TestResult:
    """Result of a single test."""

    name: str
    passed: bool
    duration_ms: int
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@dataclass
class TestSuiteResult:
    """Result of a test suite."""

    name: str
    results: List[TestResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)


@dataclass
class TestSummary:
    """Summary of all test results."""

    suites: List[TestSuiteResult] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def total(self) -> int:
        return sum(suite.total for suite in self.suites)

    @property
    def passed(self) -> int:
        return sum(suite.passed for suite in self.suites)

    @property
    def failed(self) -> int:
        return sum(suite.failed for suite in self.suites)

    def add_suite(self, suite: TestSuiteResult) -> None:
        self.suites.append(suite)
