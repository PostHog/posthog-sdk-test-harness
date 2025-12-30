"""Command-line interface for the test harness."""

import asyncio
import json
import sys
import threading
from typing import Optional

import click

from .mock_server import MockServer, MockServerState
from .sdk_adapter import SDKAdapterClient
from .tests import TestContext, run_all_suites
from .types import TestSummary


def print_summary(summary: TestSummary, output_format: str = "text") -> None:
    """
    Print test results summary.

    Args:
        summary: Test summary to print
        output_format: Output format ('text' or 'json')
    """
    if output_format == "json":
        result = {
            "total": summary.total,
            "passed": summary.passed,
            "failed": summary.failed,
            "duration_ms": summary.duration_ms,
            "suites": [
                {
                    "name": suite.name,
                    "total": suite.total,
                    "passed": suite.passed,
                    "failed": suite.failed,
                    "results": [
                        {
                            "name": r.name,
                            "passed": r.passed,
                            "duration_ms": r.duration_ms,
                            "message": r.message,
                        }
                        for r in suite.results
                    ],
                }
                for suite in summary.suites
            ],
        }
        print(json.dumps(result, indent=2))
    else:
        # Text output
        print()
        print("=" * 70)
        print(" SDK Compliance Test Results ".center(70))
        print("=" * 70)
        print()

        for suite in summary.suites:
            print(f"\n{suite.name.upper()} Tests:")
            print("-" * 70)

            for result in suite.results:
                status = "✓" if result.passed else "✗"
                color = "\033[92m" if result.passed else "\033[91m"
                reset = "\033[0m"

                print(f"  {color}{status}{reset} {result.name} ({result.duration_ms}ms)")

                if result.message:
                    print(f"    \033[90m{result.message}\033[0m")

        print()
        print("-" * 70)

        passed_color = "\033[92m"
        failed_color = "\033[91m" if summary.failed > 0 else "\033[90m"
        reset = "\033[0m"

        print(
            f"  Total: {summary.total} | "
            f"{passed_color}{summary.passed} passed{reset} | "
            f"{failed_color}{summary.failed} failed{reset} | "
            f"Duration: {summary.duration_ms}ms"
        )

        if summary.failed == 0:
            print()
            print(f"  {passed_color}All tests passed! ✓{reset}")
        else:
            print()
            print(f"  {failed_color}{summary.failed} test(s) failed{reset}")

        print("=" * 70)
        print()


@click.group()
def cli() -> None:
    """PostHog SDK Test Harness."""
    pass


@cli.command()
@click.option("--port", default=8081, help="Port to listen on")
def mock_server(port: int) -> None:
    """Run the mock PostHog server standalone."""
    click.echo(f"Starting mock PostHog server on port {port}...")

    state = MockServerState()
    server = MockServer(state)

    try:
        server.run(host="0.0.0.0", port=port, debug=False)
    except KeyboardInterrupt:
        click.echo("\nShutting down mock server...")


@cli.command()
@click.option(
    "--adapter-url",
    required=True,
    envvar="SDK_ADAPTER_URL",
    help="URL of the SDK adapter",
)
@click.option("--mock-port", default=8081, help="Port for the mock server")
@click.option(
    "--mock-url",
    envvar="MOCK_SERVER_URL",
    help="URL of mock server (default: http://localhost:{mock-port})",
)
@click.option("--timeout", default=30, help="Timeout for adapter health check (seconds)")
@click.option(
    "--output",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
@click.option("--suite", multiple=True, help="Specific test suite(s) to run")
@click.option("--report", help="Path to save report (markdown or json based on extension)")
def run(
    adapter_url: str,
    mock_port: int,
    mock_url: Optional[str],
    timeout: int,
    output: str,
    suite: tuple,
    report: Optional[str],
) -> None:
    """Run compliance tests against an SDK adapter."""
    asyncio.run(_run_tests(adapter_url, mock_port, mock_url, timeout, output, list(suite), report))


async def _run_tests(
    adapter_url: str,
    mock_port: int,
    mock_url: Optional[str],
    timeout: int,
    output: str,
    suite_names: list,
    report_path: Optional[str],
) -> None:
    """Async implementation of run command."""
    # Determine mock server URL
    if not mock_url:
        mock_url = f"http://localhost:{mock_port}"

    click.echo("Starting SDK compliance tests")
    click.echo(f"SDK Adapter: {adapter_url}")
    click.echo(f"Mock Server Port: {mock_port}")
    click.echo(f"Mock Server URL: {mock_url}")
    click.echo()

    # Start mock server in background thread
    state = MockServerState()
    server = MockServer(state)

    def run_server() -> None:
        server.run(host="0.0.0.0", port=mock_port, debug=False)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for server to start
    await asyncio.sleep(0.5)

    # Wait for SDK adapter to be ready
    adapter_client = SDKAdapterClient(adapter_url)
    click.echo("Waiting for SDK adapter to be ready...")

    try:
        health = await adapter_client.wait_for_health(timeout_seconds=timeout)
        click.echo(f"SDK adapter ready: {health.sdk_name} v{health.sdk_version}")
        click.echo()
    except Exception as e:
        click.echo(f"Error: SDK adapter not ready: {e}", err=True)
        sys.exit(1)

    # Create test context
    ctx = TestContext(
        sdk_adapter=adapter_client,
        mock_server=state,
        mock_server_url=mock_url,
    )

    # Run tests
    click.echo("Running tests...")
    summary = await run_all_suites(ctx, suite_names=suite_names if suite_names else None)

    # Print results
    print_summary(summary, output)

    # Generate report if requested
    if report_path:
        from .report import save_report

        # Determine format from file extension
        if report_path.endswith(".json"):
            report_format = "json"
        else:
            report_format = "markdown"

        save_report(summary, report_path, report_format, sdk_name=health.sdk_name)
        click.echo(f"\n✓ Report saved to {report_path}")

    # Exit with error code if any tests failed
    if summary.failed > 0:
        sys.exit(1)


@cli.command()
@click.option(
    "--adapter-url",
    required=True,
    envvar="SDK_ADAPTER_URL",
    help="URL of the SDK adapter",
)
def health(adapter_url: str) -> None:
    """Check health of an SDK adapter."""
    asyncio.run(_check_health(adapter_url))


async def _check_health(adapter_url: str) -> None:
    """Async implementation of health command."""
    client = SDKAdapterClient(adapter_url)

    try:
        health_info = await client.health()
        click.echo("\033[92mSDK Adapter Health: OK\033[0m")
        click.echo(f"  SDK Name: {health_info.sdk_name}")
        click.echo(f"  SDK Version: {health_info.sdk_version}")
        click.echo(f"  Adapter Version: {health_info.adapter_version}")
    except Exception as e:
        click.echo(f"\033[91mSDK Adapter Health: FAILED\033[0m - {e}", err=True)
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
