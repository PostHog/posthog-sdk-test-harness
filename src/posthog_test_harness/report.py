"""Report generation for test results."""

import json
from datetime import datetime
from typing import Any, Dict

from .types import TestSummary


def generate_markdown_report(summary: TestSummary, sdk_name: str = "Unknown SDK") -> str:
    """
    Generate a markdown report suitable for GitHub Actions or PR comments.

    Args:
        summary: Test summary
        sdk_name: Name of the SDK being tested

    Returns:
        Markdown-formatted report
    """
    lines = []

    # Header
    lines.append(f"# PostHog SDK Compliance Report")
    lines.append(f"")
    lines.append(f"**SDK**: {sdk_name}")
    lines.append(f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**Duration**: {summary.duration_ms}ms")
    lines.append(f"")

    # Summary
    if summary.failed == 0:
        lines.append(f"## ✅ All Tests Passed!")
        lines.append(f"")
        lines.append(f"**{summary.passed}/{summary.total}** tests passed")
    else:
        lines.append(f"## ⚠️ Some Tests Failed")
        lines.append(f"")
        lines.append(f"**{summary.passed}/{summary.total}** tests passed, **{summary.failed}** failed")

    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # Test suites
    for suite in summary.suites:
        lines.append(f"## {suite.name.title()} Tests")
        lines.append(f"")

        if suite.failed == 0:
            lines.append(f"✅ **{suite.passed}/{suite.total}** tests passed")
        else:
            lines.append(f"⚠️ **{suite.passed}/{suite.total}** tests passed, **{suite.failed}** failed")

        lines.append(f"")
        lines.append(f"<details>")
        lines.append(f"<summary>View Details</summary>")
        lines.append(f"")
        lines.append(f"| Test | Status | Duration |")
        lines.append(f"|------|--------|----------|")

        for result in suite.results:
            status = "✅" if result.passed else "❌"
            duration = f"{result.duration_ms}ms"
            test_name = result.name.replace("_", " ").title()

            lines.append(f"| {test_name} | {status} | {duration} |")

        lines.append(f"")

        # Show failures
        failures = [r for r in suite.results if not r.passed]
        if failures:
            lines.append(f"### Failures")
            lines.append(f"")
            for result in failures:
                lines.append(f"**{result.name}**")
                lines.append(f"```")
                lines.append(result.message or "No error message")
                lines.append(f"```")
                lines.append(f"")

        lines.append(f"</details>")
        lines.append(f"")

    return "\n".join(lines)


def generate_json_report(summary: TestSummary, sdk_name: str = "Unknown SDK") -> Dict[str, Any]:
    """
    Generate a JSON report.

    Args:
        summary: Test summary
        sdk_name: Name of the SDK being tested

    Returns:
        JSON-serializable dictionary
    """
    return {
        "sdk_name": sdk_name,
        "timestamp": datetime.now().isoformat(),
        "duration_ms": summary.duration_ms,
        "summary": {
            "total": summary.total,
            "passed": summary.passed,
            "failed": summary.failed,
        },
        "suites": [
            {
                "name": suite.name,
                "total": suite.total,
                "passed": suite.passed,
                "failed": suite.failed,
                "tests": [
                    {
                        "name": result.name,
                        "passed": result.passed,
                        "duration_ms": result.duration_ms,
                        "message": result.message,
                    }
                    for result in suite.results
                ],
            }
            for suite in summary.suites
        ],
    }


def save_report(summary: TestSummary, output_path: str, format: str = "markdown", sdk_name: str = "Unknown SDK") -> None:
    """
    Save report to a file.

    Args:
        summary: Test summary
        output_path: Path to save the report
        format: Report format ('markdown' or 'json')
        sdk_name: Name of the SDK being tested
    """
    if format == "markdown":
        content = generate_markdown_report(summary, sdk_name)
    elif format == "json":
        content = json.dumps(generate_json_report(summary, sdk_name), indent=2)
    else:
        raise ValueError(f"Unsupported format: {format}")

    with open(output_path, "w") as f:
        f.write(content)
