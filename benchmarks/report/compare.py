"""Descriptive paired comparisons, without a statistical performance gate."""

import math
from statistics import median

from .validate import require, validate

# These describe the SDK under test, not the measurement environment.
SDK_BUILD_FIELDS = {"dependency_lock_digest", "build_digest", "core_build", "entry", "sdk_version", "packages"}
FAILURES = {"error", "timeout", "oom"}
SOURCE_STATUSES = {
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "neutral",
    "skipped",
    "stale",
    "startup_failure",
}


def without(value: dict, keys: set) -> dict:
    return {key: item for key, item in value.items() if key not in keys}


def global_mismatches(base: dict, head: dict) -> list[str]:
    reasons = []
    if base["sdk"] != head["sdk"]:
        reasons.append("SDK differs")
    if base["suite"] != head["suite"]:
        reasons.append("suite/source/profile/measurement configuration differs")
    if without(base["execution"], SDK_BUILD_FIELDS) != without(head["execution"], SDK_BUILD_FIELDS):
        reasons.append("paired execution environment differs")
    return reasons


def run_configuration(workload: dict):
    return sorted(
        (
            run["pass"],
            run["repetition"],
            run.get("operations"),
            run.get("warmup_operations"),
            [point["operations"] for point in run.get("checkpoints", [])],
        )
        for run in workload.get("runs", [])
    )


def failed_statuses(workload: dict) -> list[str]:
    return sorted({s for s in [workload["status"], *(r["status"] for r in workload.get("runs", []))] if s != "ok"})


def _percentage(base: float, head: float):
    if base == 0:
        return None
    value = (head - base) / base * 100
    return value if math.isfinite(value) else None


def compare(base: dict, head: dict, *, source_status: str | None = None) -> dict:
    require(
        source_status is None or (isinstance(source_status, str) and source_status in SOURCE_STATUSES),
        "Invalid source execution status",
    )
    source_failed = source_status not in (None, "success")
    validate(base)
    validate(head)
    require(base["revision"]["role"] == "base" and head["revision"]["role"] == "head", "Wrong revision roles")
    mismatch = global_mismatches(base, head)
    rows = []
    workloads = {}
    execution_failed = source_failed
    bases = {b["id"]: b for b in base["benchmarks"]}
    heads = {b["id"]: b for b in head["benchmarks"]}
    for workload_id in sorted(bases.keys() | heads.keys()):
        left, right = bases.get(workload_id), heads.get(workload_id)
        workloads[workload_id] = {
            role: {
                "parameters": workload["parameters"],
                "fixture_digest": workload["fixture_digest"],
                "status": workload["status"],
                "reason": workload.get("reason"),
                "failures": [
                    {
                        key: run[key]
                        for key in (
                            "pass",
                            "repetition",
                            "status",
                            "reason",
                            "error",
                            "code",
                            "signal",
                            "rss_limit_exceeded",
                        )
                        if key in run
                    }
                    for run in workload.get("runs", [])
                    if run["status"] != "ok"
                ],
                "checkpoints": [
                    {"repetition": r["repetition"], "pass": r["pass"], "points": r["checkpoints"]}
                    for r in workload.get("runs", [])
                    if "checkpoints" in r
                ],
            }
            for role, workload in (("base", left), ("head", right))
            if workload
        }
        metrics = [{m["id"]: m for m in w["metrics"]} if w else {} for w in (left, right)]
        # A failed workload with no metrics still needs a prominent row.
        for metric_id in sorted(metrics[0].keys() | metrics[1].keys()) or ["(workload)"]:
            bm, hm = (m.get(metric_id) for m in metrics)
            row = {"workload": workload_id, "metric": metric_id, "base": bm, "head": hm, "status": "inconclusive"}
            failures = []
            row["explanations"] = []
            row_failed = False
            for role, workload, metric in (("base", left, bm), ("head", right, hm)):
                if workload:
                    for source in (workload, metric):
                        if source and source["status"] != "ok" and source.get("reason"):
                            row["explanations"].append(f"{role}: {source['reason']}")
                    statuses = set(failed_statuses(workload))
                    if metric and metric["status"] != "ok":
                        statuses.add(metric["status"])
                    failures.extend(f"{role}: {status}" for status in sorted(statuses))
                    row_failed |= bool(statuses & FAILURES)
            execution_failed |= row_failed
            reasons = list(mismatch)
            if source_failed or failures:
                row.update(
                    status="unavailable",
                    reasons=([f"Source execution: {source_status}"] if source_failed else []) + failures,
                    execution_failed=source_failed or row_failed,
                )
            elif left is None or bm is None:
                row.update(status="no baseline", reasons=["baseline workload or metric missing"])
            elif right is None or hm is None:
                row.update(status="missing head", reasons=["head workload or metric missing"])
            else:
                if left["parameters"] != right["parameters"] or left["fixture_digest"] != right["fixture_digest"]:
                    reasons.append("workload parameters or fixture differ")
                if run_configuration(left) != run_configuration(right):
                    reasons.append("raw run identities, operations, warmup or checkpoints differ")
                if without(bm, {"status", "samples", "reason"}) != without(hm, {"status", "samples", "reason"}):
                    reasons.append("metric unit, method, quality, direction or configuration differs")
                bs = {s["repetition"]: s for s in bm.get("samples", [])}
                hs = {s["repetition"]: s for s in hm.get("samples", [])}
                if bs.keys() != hs.keys() or any(
                    bs[k]["operations"] != hs[k]["operations"] for k in bs.keys() & hs.keys()
                ):
                    reasons.append("paired sample identities or operation counts differ")
                if reasons:
                    row.update(status="not comparable", reasons=reasons)
                elif bs:
                    bvalues, hvalues = ([s[k]["value"] for k in sorted(s)] for s in (bs, hs))
                    bmedian, hmedian = median(bvalues), median(hvalues)
                    deltas = [h - b for b, h in zip(bvalues, hvalues)]
                    percentages = [_percentage(b, h) for b, h in zip(bvalues, hvalues)]
                    row.update(
                        base_median=bmedian,
                        head_median=hmedian,
                        change_percent=_percentage(bmedian, hmedian),
                        pairs=len(bs),
                        paired_delta={"median": median(deltas), "min": min(deltas), "max": max(deltas)},
                        paired_change_percent=percentages,
                        reasons=["Descriptive only; uncertainty is not calibrated"],
                    )
                    if bmedian == 0:
                        row["reasons"].append("Zero baseline; percentage undefined")
                    if len(bs) != base["suite"]["repetitions"]:
                        row["reasons"].append("Incomplete repetitions")
                else:
                    row.update(status="unavailable", reasons=["No completed paired samples"])
            rows.append(row)
    rows.sort(
        key=lambda r: (not r.get("execution_failed", False), r["status"] == "inconclusive", r["workload"], r["metric"])
    )
    return {
        "comparison_version": 1,
        "sdk": head["sdk"],
        "execution_failed": execution_failed,
        "source_execution_status": source_status,
        "compatibility": mismatch,
        "context": {
            role: {k: doc[k] for k in ("revision", "suite", "execution")}
            for role, doc in (("base", base), ("head", head))
        },
        "workloads": workloads,
        "rows": rows,
    }
