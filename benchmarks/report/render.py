"""Render bounded Markdown from data, never artifact-provided markup or URLs."""

import json
import unicodedata
from pathlib import Path

MAX_REPORT_BYTES = 58_000  # Reserve room for the publisher's trusted markers.


def safe_text(value, limit: int = 240) -> str:
    text = str(value)
    text = "".join(" " if unicodedata.category(c).startswith("C") or c.isspace() else c for c in text)
    # Bound encoded bytes, including entity expansion, without splitting an entity.
    pieces = []
    size = 0
    for c in text:
        # Entities stop Markdown/HTML syntax; the separator prevents mentions and URL autolinking.
        piece = c if c.isalnum() or c == " " else f"&#{ord(c)};" + ("&#8203;" if c in "@:." else "")
        length = len(piece.encode("utf-8"))
        if size + length > limit - 3:
            pieces.append("…")
            break
        pieces.append(piece)
        size += length
    return "".join(pieces)


def json_text(value) -> str:
    # Safe even when a consumer embeds the comparison in HTML; it remains ordinary JSON on disk.
    text = json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026") + "\n"


def _number(value) -> str:
    return "—" if value is None else f"{value:,.4g}"


def _row(row: dict, details=False, workload=None) -> str:
    metric = row["head"] or row["base"] or {}
    label = safe_text(row["metric"], 100)
    if metric.get("quality") == "estimated":
        label += " (estimated)"
    label += " · " + safe_text(metric.get("unit", ""), 30)
    change = _number(row.get("change_percent"))
    if row.get("change_percent") is not None:
        change += "%"
    status = safe_text(row["status"])
    if row["status"] != "inconclusive":
        status += ": " + safe_text("; ".join(row.get("reasons", [])), 180)
    if details:
        status += "; " + safe_text("; ".join(row.get("reasons", [])), 220)
        if "paired_delta" in row:
            delta = row["paired_delta"]
            status += (
                f"; {row['pairs']} pairs, paired Δ median {_number(delta['median'])}, "
                f"range {_number(delta['min'])}…{_number(delta['max'])}"
            )
        for explanation in row.get("explanations", []):
            status += "; " + safe_text(explanation, 220)
        for role, context in (workload or {}).items():
            failures = context.get("failures", [])
            for failure in failures[:3]:
                reason = failure.get("reason") or failure.get("error")
                if reason:
                    status += (
                        f"; {role} {safe_text(failure['pass'], 32)} repetition {failure['repetition']}: "
                        + safe_text(reason, 220)
                    )
            if len(failures) > 3:
                status += f"; {len(failures) - 3} more {role} failures in comparison.json"
        status += "; method: " + safe_text(metric.get("method", "unavailable"), 160)
    return (
        f"| {safe_text(row['workload'], 100)} | {label} | {_number(row.get('base_median'))} | "
        f"{_number(row.get('head_median'))} | {change} · {status} |\n"
    )


def headline_rows(comparison: dict) -> list[dict]:
    """Show representative workload families without letting unsupported rows crowd out measurements."""
    rows = comparison["rows"]
    selected = []
    identities = set()

    def add(row):
        identity = (row["workload"], row["metric"])
        if identity not in identities:
            identities.add(identity)
            selected.append(row)

    for row in rows:
        if row.get("execution_failed"):
            add(row)

    workloads = comparison["workloads"]

    def parameters(workload):
        context = workloads[workload]
        return (context.get("head") or context["base"])["parameters"]

    def pick(workload, metric):
        for row in rows:
            if row["workload"] == workload and row["metric"] == metric and "base_median" in row:
                add(row)
                break

    # These are workload/metric semantics shared by adapters, not profiler-specific behavior.
    for mode, metric, all_sizes in (
        ("single", "elapsed_time_per_op", False),
        ("all", "elapsed_time_per_op", True),
        ("all", "allocated_bytes_per_op", True),
        ("all", "observed_workload_peak_rss", False),
        ("refresh", "post_settling_process_rss", False),
        ("capture", "elapsed_time_per_op", False),
        ("drain", "observed_workload_peak_rss", False),
        ("single", "process_cpu_time_per_op", False),
    ):
        candidates = [w for w in workloads if parameters(w).get("mode") == mode]
        # Select the boolean single case, ascending scaling sizes, and the largest payload.
        # Parameter values are untrusted arbitrary JSON; sorting serialized values is total.
        candidates.sort(
            key=lambda w: (
                parameters(w).get("flag_index") != 0 if mode == "single" else False,
                len(str(parameters(w).get("flag_count", 0))),
                str(parameters(w).get("flag_count", 0)),
                len(str(parameters(w).get("payload_bytes", 0))),
                str(parameters(w).get("payload_bytes", 0)),
                w,
            ),
            reverse=mode in ("capture", "drain"),
        )
        for workload in candidates:
            before = len(selected)
            pick(workload, metric)
            if not all_sizes and len(selected) > before:
                break

    for row in rows:
        if "base_median" in row:
            add(row)
    for row in rows:
        add(row)
    return selected[:12]


def render(comparison: dict) -> str:
    chunks = [
        f"## SDK performance: {safe_text(comparison['sdk'])}\n\n",
        "Informational medians of paired samples; all numeric changes are **inconclusive**. "
        "Paired ranges describe variability, not confidence intervals. No aggregate score or performance gate.\n\n",
    ]
    if comparison["execution_failed"]:
        chunks.append("**Benchmark execution failed. Partial samples are not evidence of improvement.**\n\n")
    for role in ("base", "head"):
        context = comparison["context"][role]
        execution = context["execution"]
        chunks.append(f"**{role.title()}**: {safe_text(context['revision']['commit'])}")
        if context["revision"].get("dirty"):
            chunks.append(" (dirty working tree)")
        chunks.append(
            f" · {safe_text(execution['runtime'])} {safe_text(execution['runtime_version'])}"
            f" · {safe_text(execution['os'])}/{safe_text(execution['arch'])}"
            f" · CPU: {safe_text(execution['cpu'])} · runner: {safe_text(execution['runner_class'])}\n\n"
        )
        chunks.append(
            "Resource limits: " + safe_text(json.dumps(execution["resource_limits"], sort_keys=True), 1000) + "\n\n"
        )
    chunks.append("Full comparison, configuration, samples and checkpoints: `comparison.json` (job artifact).\n\n")
    table = "| Workload | Metric · unit | Base | PR | Change · interpretation |\n| --- | --- | --- | --- | --- |\n"
    chunks.append(table)
    rows = comparison["rows"]
    size = len("".join(chunks).encode("utf-8"))
    for row in headline_rows(comparison):
        line = _row(row)
        if size + len(line.encode("utf-8")) > MAX_REPORT_BYTES - 1500:
            break
        chunks.append(line)
        size += len(line.encode("utf-8"))
    chunks.append("\n<details>\n<summary>Detailed cases and paired variability</summary>\n\n" + table)
    size = len("".join(chunks).encode("utf-8"))
    shown = 0
    for row in rows:
        line = _row(row, details=True, workload=comparison["workloads"][row["workload"]])
        if size + len(line.encode("utf-8")) > MAX_REPORT_BYTES - 1000:
            break
        chunks.append(line)
        size += len(line.encode("utf-8"))
        shown += 1
    chunks.append("\n</details>\n\n")
    chunks.append(
        f"{len(rows)} metric rows; {shown} detailed rows shown. "
        "Omitted or shortened data remain in `comparison.json`.\n"
    )
    report = "".join(chunks)
    assert len(report.encode("utf-8")) < MAX_REPORT_BYTES
    return report


def write_outputs(output_directory: str, comparison: dict, report: str, environ: dict) -> tuple[Path, Path]:
    directory = Path(output_directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report_path, comparison_path = directory / "report.md", directory / "comparison.json"
    report_path.write_text(report, encoding="utf-8")
    comparison_path.write_text(json_text(comparison), encoding="utf-8")
    if environ.get("GITHUB_STEP_SUMMARY"):
        with Path(environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(report)
    if environ.get("GITHUB_OUTPUT"):
        # Paths are caller-controlled, but must not inject additional action outputs.
        if any(c in str(directory) for c in "\r\n"):
            raise ValueError("Output directory contains a newline")
        with Path(environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(f"report-path={report_path}\ncomparison-path={comparison_path}\n")
    return report_path, comparison_path
