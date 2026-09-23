"""Sequential Gherkin execution over the versioned transport, with strict reports."""

import asyncio
import hashlib
import json
from collections import Counter
from uuid import uuid4

from .client import Client
from .contracts import VERSION, BoundaryError, require
from .fixtures import CaseServer
from .gherkin import load_cases
from .local_parity_steps import STEPS, no_remote
from .migration import selection
from .steps import Context


def error_record(error, **attribution):
    return {"code": error.code, "message": str(error), **attribution}


def failure(error, executed, step, call_ids):
    status = (
        error.kind
        if error.kind in ("failed_assertion", "blocked_fixture", "blocked_contract", "unsupported_binding")
        else "harness_error"
    )
    return {
        "status": status,
        "executed": executed,
        "failure": {"code": error.code, "message": str(error), "failed_step": step, "call_ids": call_ids},
    }


def failure_diagnostics(error, step, case):
    record = {**error.failure(), "failed_step": step}
    if step is not None:
        record["step_text"] = case.steps[step["index"]].text
    if error.details is not None:
        record["details"] = error.details
    return record


def native_flag_sdk_types(case, *, include_legacy=True):
    types = {tag.removeprefix("@sdk:") for tag in case.tags if tag.startswith("@sdk:")}
    # This existing canonical feature predates explicit @sdk applicability tags.
    if include_legacy and case.source["path"] == "acceptance/public/on-feature-flags.feature":
        types.add("client")
    require(types <= {"client", "server"}, "invalid_source", "Unknown SDK applicability")
    return types


async def run_case(client, case, profile, server, timeout_ms, diagnostics, report, registry):
    ctx = Context(client, case, profile, server, timeout_ms, diagnostics)
    problem, current, executed = None, None, False
    error_start = len(client.errors)
    cancel_count = asyncio.current_task().cancelling()
    caller_cancellation = None
    try:
        if native_flag_sdk_types(case) and "sdk_type" not in profile:
            raise BoundaryError(
                "sdk_type_undeclared",
                "Selected native flag scenario requires an explicit client/server SDK type declaration",
                "blocked_contract",
            )
        bindings = []
        for index, step in enumerate(case.steps):
            current = {"index": index, "source": step.source}
            bindings.append(registry.bind(step))
        for index, (handler, _) in enumerate(bindings):
            current = {"index": index, "source": case.steps[index].source}
            requirements = registry.requirements[handler]
            missing = set(requirements["routes"]) - set(client.negotiation["supported_routes"])
            if missing:
                raise BoundaryError(
                    "missing_operation",
                    "Required public operation unavailable: " + ", ".join(sorted(missing)),
                    "unsupported_binding",
                )
            # Draft2 allocation guarantees storage isolation. Other prototype controls
            # have no public binding yet, even if an adapter claims their old names.
            missing = set(requirements["fixtures"]) - {"storage.empty.v1"}
            missing |= set(requirements["fixtures"]) - set(profile["fixture_capabilities"])
            if missing:
                raise BoundaryError(
                    "fixture_unavailable",
                    "Required harness fixture unavailable: " + ", ".join(sorted(missing)),
                    "blocked_fixture",
                )
        capabilities = {tag.removeprefix("@requires:") for tag in case.tags if tag.startswith("@requires:")}
        missing = capabilities - set(profile["sdk_capabilities"])
        if missing:
            current = None
            raise BoundaryError(
                "sdk_capability_unavailable",
                "Required SDK capability unavailable: " + ", ".join(sorted(missing)),
                "unsupported_binding",
            )
        for index, (step, (handler, args)) in enumerate(zip(case.steps, bindings)):
            current = {"index": index, "source": step.source}
            ctx.step_index = index
            executed = True
            await handler(ctx, step, *args)
            if server.gates.failures():
                raise server.gates.failures()[0]
        require(ctx.fixture is not None, "missing_fixture", "Case executed without an allocated fixture")
    except asyncio.CancelledError:
        for task in ctx.pending_tasks:
            task.cancel()
        raise
    except BoundaryError as error:
        problem = error
    except AssertionError as error:
        problem = BoundaryError("assertion_failed", str(error) or "Assertion failed", "failed_assertion")
    except Exception as error:
        problem = BoundaryError("runner_exception", f"Step execution raised {type(error).__name__}")
    finally:
        if ctx.pending_tasks:
            server.retire()
            for task in ctx.pending_tasks:
                try:
                    await task
                except asyncio.CancelledError as error:
                    if asyncio.current_task().cancelling() > cancel_count:
                        caller_cancellation = error
                        for pending in ctx.pending_tasks:
                            pending.cancel()
                    if problem is None:
                        problem = BoundaryError("runner_cancelled", "Concurrent execution cancelled", "cancelled")
                except BoundaryError as error:
                    if problem is None:
                        problem = error
                except Exception as error:
                    report["errors"].append(
                        {"code": "concurrent_cleanup", "message": type(error).__name__, "case_id": case.id}
                    )
                    if problem is None:
                        problem = BoundaryError("concurrent_cleanup", "Concurrent invocation did not settle")
        fixture = client.fixtures.get(diagnostics["fixture_id"])
        if fixture is not None:
            try:
                await fixture.close()
            except BoundaryError as error:
                report["errors"].append(error_record(error, case_id=case.id))
                if problem is None:
                    problem = error
        if problem is None and len(client.errors) > error_start:
            problem = client.errors[error_start]
        server.retire()
        try:
            await asyncio.to_thread(server.wait_idle)
        except BoundaryError as error:
            report["errors"].append(error_record(error, case_id=case.id))
            if problem is None:
                problem = error
        if getattr(ctx, "local_evaluation", False):
            try:
                no_remote(ctx)
            except BoundaryError as error:
                if problem is None:
                    problem = error
        for error in server.gates.failures():
            report["errors"].append(error_record(error, case_id=case.id))
            if problem is None or problem.kind == "failed_assertion":
                if problem is not None:
                    diagnostics["secondary_failure"] = failure_diagnostics(problem, current, case)
                problem = error
        diagnostics["network_gates"] = server.gates.diagnostics()
        diagnostics["network"] = server.requests()
        diagnostics["flag_requests"] = server.flag_request_summary()
        diagnostics["ingestion"] = [
            {
                "path": r.path,
                "status": r.response_status,
                "event_names": [e.get("event") for e in (r.parsed_events or []) if isinstance(e, dict)],
            }
            for r in server.state.get_requests()
        ]
        if case.migration:
            diagnostics["wire_requests"] = [
                {
                    key: getattr(r, key)
                    for key in (
                        "timestamp_ms",
                        "path",
                        "query_params",
                        "body_decompressed",
                        "headers",
                        "parsed_events",
                        "response_status",
                        "response_headers",
                        "response_body",
                    )
                }
                for r in server.state.get_requests()
            ]
    if caller_cancellation is not None:
        raise caller_cancellation
    call_ids = [identity for identity, call in client.calls.items() if call["fixture_id"] == diagnostics["fixture_id"]]
    if problem:
        diagnostics["failure"] = failure_diagnostics(problem, current, case)
        if problem.code in ("undefined_step", "ambiguous_step", "runner_exception"):
            report["errors"].append(error_record(problem, case_id=case.id))
        return failure(problem, executed, current, call_ids)
    return {"status": "passed", "executed": True, "call_ids": call_ids}


async def run(
    contracts,
    specs,
    features,
    adapter_url,
    profile_id,
    *,
    case_ids=(),
    tagged_acceptance=False,
    timeout_ms=5000,
    registry=STEPS,
    mock_bind_host="127.0.0.1",
    mock_advertised_host="127.0.0.1",
    allow_private_network=False,
):
    require(not tagged_acceptance or not case_ids, "invalid_selector", "Tag-selected acceptance cannot use case IDs")
    require(
        not tagged_acceptance or all(str(path).startswith("acceptance/") for path in features),
        "invalid_selector",
        "Tag-selected acceptance requires acceptance feature paths",
    )
    run_id = str(uuid4())
    report = {
        "contract_version": VERSION,
        "run_id": run_id,
        "scope_id": "gherkin-selection-v1:"
        + hashlib.sha256(
            json.dumps(
                {
                    "features": sorted(features),
                    "cases": sorted(case_ids),
                    **({"tagged_acceptance": True} if tagged_acceptance else {}),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "profiles": [],
        "inventory": [],
        "results": [],
        "fixtures": [],
        "calls": [],
        "errors": [],
    }
    diagnostics = {
        "run_id": run_id,
        "purpose": "Harness execution evidence; not an SDK conformance declaration",
        "network_config": {
            "mock_bind_host": mock_bind_host,
            "mock_advertised_host": mock_advertised_host,
            "allow_private_network": allow_private_network,
        },
        "inputs": [],
        "discovered": [],
        "cases": [],
    }
    servers, client = [], None
    try:
        cases, inputs = load_cases(specs, features)
        diagnostics["inputs"] = inputs
        diagnostics["discovered"] = [
            {
                "case_id": c.id,
                "name": c.name,
                "tags": c.tags,
                "source": c.source,
                **({"migration": c.migration} if c.migration else {}),
            }
            for c in cases
        ]
        require(bool(cases), "zero_cases", "No Gherkin cases discovered")
        client = Client(adapter_url, contracts, allow_private_network=allow_private_network)
        async with client:
            report["profiles"] = client.negotiation["profiles"]
            profiles = {p["id"]: p for p in report["profiles"]}
            require(profile_id in profiles, "unknown_profile", "Requested profile was not negotiated")
            profile = profiles[profile_id]
            diagnostics["adapter"] = client.negotiation
            client.deadline(timeout_ms)
            invalid_selector = len(set(case_ids)) != len(case_ids) or bool(set(case_ids) - {c.id for c in cases})
            if invalid_selector:
                report["errors"].append({"code": "invalid_selector", "message": "Unknown or duplicate case selector"})
            for case in cases:
                identity = {"case_id": case.id, "source": case.source, "profile_id": profile_id}
                required_types = native_flag_sdk_types(case)
                declared_type = profile.get("sdk_type")
                selected = (
                    declared_type in native_flag_sdk_types(case, include_legacy=False)
                    if tagged_acceptance
                    else not invalid_selector and (not case_ids or case.id in case_ids)
                )
                reason = "SDK type not opted into acceptance case" if tagged_acceptance else "Excluded by case selector"
                if case.migration:
                    decision = selection(
                        case, profile, client.negotiation["supported_routes"], explicit=case.id in case_ids
                    )
                    if selected:
                        selected = decision["selected"]
                        reason = decision["reason"]
                    diagnostics.setdefault("selection", []).append(
                        {"case_id": case.id, **decision, "selected": selected, "reason": reason}
                    )
                applicability = {"kind": "applicable"}
                if required_types and declared_type is not None and declared_type not in required_types:
                    applicability = {
                        "kind": "not_applicable",
                        "rule": "native-flag-sdk-type-v1",
                        "reason": "Scenario requires its declared native server/client context",
                    }
                report["inventory"].append({**identity, "selected": selected, "applicability": applicability})
                result = (
                    failure(
                        BoundaryError("run_aborted", "Case did not execute because the run stopped"), False, None, []
                    )
                    if selected
                    else {"status": "not_selected", "executed": False, "reason": reason}
                )
                if selected and applicability["kind"] == "not_applicable":
                    result = {
                        "status": "not_applicable",
                        "executed": False,
                        "reason": applicability["reason"],
                        "applicability_rule": applicability["rule"],
                    }
                report["results"].append({**identity, "result": result})
            for case, entry, row in zip(cases, report["inventory"], report["results"]):
                if not entry["selected"] or entry["applicability"]["kind"] == "not_applicable":
                    continue
                case_diagnostics = {
                    "case_id": case.id,
                    "source": case.source,
                    "profile_id": profile_id,
                    "fixture_id": str(uuid4()),
                    "controls": [],
                    "invocations": [],
                }
                diagnostics["cases"].append(case_diagnostics)
                server = CaseServer(bind_host=mock_bind_host, advertised_host=mock_advertised_host)
                servers.append(server)
                case_diagnostics["mock_url"] = server.url
                row["result"] = await run_case(
                    client, case, profile, server, timeout_ms, case_diagnostics, report, registry
                )
    except BoundaryError as error:
        if error_record(error) not in report["errors"]:
            report["errors"].append(error_record(error))
    except Exception as error:
        report["errors"].append({"code": "runner_exception", "message": f"Runner raised {type(error).__name__}"})
    finally:
        if client is not None:
            report["fixtures"] = [
                {"fixture_id": f.id, "case_id": f.case_id, "profile_id": f.profile_id} for f in client.fixtures.values()
            ]
            report["calls"] = list(client.calls.values())
            for error in client.errors:
                record = error_record(error)
                if record not in report["errors"]:
                    report["errors"].append(record)
        for server in servers:
            try:
                await asyncio.to_thread(server.close)
            except BoundaryError as error:
                report["errors"].append(error_record(error))
    return report, diagnostics


def summary(report):
    counts = Counter(row["result"]["status"] for row in report["results"])
    return ", ".join(f"{count} {status}" for status, count in sorted(counts.items())) or "0 executed cases"
