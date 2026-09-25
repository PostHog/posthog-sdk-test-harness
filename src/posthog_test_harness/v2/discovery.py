"""Offline execution-route discovery, separate from SDK capability negotiation."""

from pathlib import Path

from .contracts import BoundaryError, require
from .gherkin import load_cases
from .local_parity_steps import STEPS


def feature_paths(specs):
    paths = sorted(p.relative_to(specs).as_posix() for p in Path(specs).rglob("*.feature"))
    require(bool(paths), "zero_cases", "No Gherkin features")
    return paths


def acceptance_paths(specs):
    paths = sorted(p.relative_to(specs).as_posix() for p in (Path(specs) / "acceptance").rglob("*.feature"))
    require(bool(paths), "zero_cases", "No acceptance features")
    return paths


def execution_route(case, registry=STEPS):
    """Declare actual bindings and retain every unresolved step with its source.

    Readiness means harness bindings exist, not that a host implements fixtures,
    the SDK exposes operations, applicability is decided, or the case has passed.
    """
    steps, routes, fixtures, missing = [], set(), set(), []
    for index, step in enumerate(case.steps):
        row = {"index": index, "source": step.source, "text": step.text}
        try:
            handler, _ = registry.bind(step)
        except BoundaryError as error:
            if error.code != "undefined_step":
                raise
            row.update(status="missing_harness", code=error.code)
            missing.append(index)
        else:
            requirements = registry.requirements.get(handler, {"routes": [], "fixtures": []})
            row.update(status="bound", binding=f"{handler.__module__}.{handler.__name__}", **requirements)
            routes.update(requirements["routes"])
            fixtures.update(requirements["fixtures"])
        steps.append(row)
    return {
        "case_id": case.id,
        "source": case.source,
        **({"migration": case.migration} if case.migration else {}),
        "name": case.name,
        "tags": case.tags,
        "status": "missing_harness" if missing else "harness_ready",
        "execution_status": "unexecuted",
        "applicability": "unresolved",
        "sdk_binding_status": "not_negotiated",
        "required_routes": sorted(routes),
        "required_fixture_capabilities": sorted(fixtures),
        "requirements_complete": not missing,
        "missing_step_indexes": missing,
        "steps": steps,
    }


def discover(specs, paths=None, registry=STEPS):
    cases, inputs = load_cases(specs, feature_paths(specs) if paths is None else paths)
    require(bool(cases), "zero_cases", "No Gherkin cases discovered")
    return {
        "format": "harness-execution-discovery-v1",
        "purpose": "Harness route discovery, not execution or SDK conformance",
        "inputs": inputs,
        "cases": [execution_route(case, registry) for case in cases],
    }
