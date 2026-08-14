"""Tests for UTC report timestamps."""

import json
import os
import time
from datetime import datetime, timezone

import pytest

from posthog_test_harness import report as report_module
from posthog_test_harness.report import generate_json_report, generate_markdown_report
from posthog_test_harness.types import TestSummary as Summary


@pytest.fixture
def fixed_utc_now(monkeypatch):
    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return datetime(2025, 1, 2, 3, 4, 5, 678901, tzinfo=tz)

    monkeypatch.setattr(report_module, "datetime", FixedDateTime)


@pytest.fixture
def non_utc_local_timezone():
    """Run a test with a non-UTC process timezone where supported."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is not available on this platform")

    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC-08"
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_json_report_timestamp_is_utc_in_non_utc_timezone(non_utc_local_timezone, fixed_utc_now):
    timestamp = generate_json_report(Summary())["timestamp"]

    assert timestamp == "2025-01-02T03:04:05.678901+00:00"


def test_markdown_report_date_is_honest_utc_in_non_utc_timezone(non_utc_local_timezone, fixed_utc_now):
    report = generate_markdown_report(Summary())

    assert "**Date**: 2025-01-02T03:04:05.678901+00:00" in report.splitlines()


def test_json_report_timestamp_survives_serialization(non_utc_local_timezone, fixed_utc_now):
    report = json.loads(json.dumps(generate_json_report(Summary())))

    assert report["timestamp"] == "2025-01-02T03:04:05.678901+00:00"
