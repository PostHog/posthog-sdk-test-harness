"""Companion SDK specifications are an explicit integration-test input."""

import os
from pathlib import Path

import pytest

from posthog_test_harness.v2.gherkin import load_cases


@pytest.fixture(scope="session")
def specs():
    source = os.environ.get("SDK_V2_SPECS")
    if not source:
        pytest.skip("Set SDK_V2_SPECS to opt into companion specification tests")
    path = Path(source).resolve()
    if not (path / "migration/yaml-parity-v1").is_dir():
        pytest.fail(f"SDK_V2_SPECS is not a companion specification checkout: {path}")
    return path


@pytest.fixture(scope="module")
def feature_cases(specs, request):
    cases, _ = load_cases(specs, [request.module.FEATURE])
    return cases


@pytest.fixture(scope="module")
def case_ids(feature_cases):
    return [case.id for case in feature_cases]
