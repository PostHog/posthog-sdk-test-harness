"""Gherkin execution from verified packaged inputs or an explicit local checkout."""

import asyncio
import json
from pathlib import Path

import click

from .. import __version__
from .bundle import specification_inputs
from .contracts import BoundaryError, Contracts
from .discovery import discover as discover_routes
from .discovery import feature_paths
from .migration import migration_paths
from .network import validate_host
from .report import strict_exit_code
from .runner import run as execute
from .runner import summary


def host_option(ctx, param, value):
    try:
        return validate_host(value)
    except ValueError as error:
        raise click.BadParameter(str(error)) from error


@click.group()
def main():
    """Run Gherkin through a negotiated v2 host."""


@main.command()
@click.option(
    "--specs",
    type=click.Path(path_type=Path, file_okay=False),
    help="Explicit local inputs; defaults to the packaged specs bundle.",
)
@click.option("--contracts", type=click.Path(path_type=Path, file_okay=False), help="Defaults to SPECS/contracts/v2")
@click.option("--feature", multiple=True, help="Frozen feature path; defaults to public/flush.feature")
@click.option("--all-features", is_flag=True, help="Include every frozen feature, including missing harness routes")
@click.option(
    "--migration-suite", is_flag=True, help="Execute the versioned YAML-parity suite (adapter-driven selection)"
)
@click.option("--adapter-url", required=True)
@click.option(
    "--allow-private-network",
    is_flag=True,
    help="Permit operator-selected adapter DNS/IP hosts. Does not verify network privacy.",
)
@click.option(
    "--mock-bind-host",
    default="127.0.0.1",
    callback=host_option,
    metavar="HOST",
    show_default=True,
    help="Host/interface for per-case mock listeners. Wildcard binding requires explicit opt-in.",
)
@click.option(
    "--mock-advertised-host",
    default="127.0.0.1",
    callback=host_option,
    metavar="HOST",
    show_default=True,
    help="Host reachable by the SDK; each case still allocates its own ephemeral port.",
)
@click.option("--profile", required=True, help="Exact negotiated execution profile ID")
@click.option("--case-id", multiple=True, help="Exact source case ID; unselected cases remain in the report")
@click.option("--timeout-ms", default=5000, type=click.IntRange(1, 300000), show_default=True)
@click.option("--report", "report_path", required=True, type=click.Path(path_type=Path, dir_okay=False))
def run(
    specs,
    contracts,
    feature,
    all_features,
    migration_suite,
    adapter_url,
    profile,
    case_id,
    timeout_ms,
    report_path,
    mock_bind_host,
    mock_advertised_host,
    allow_private_network,
):
    """Execute a frozen feature scope and write a strict JSON report."""
    if sum((bool(feature), all_features, migration_suite)) > 1:
        raise click.UsageError("Use only one of --feature, --all-features or --migration-suite")
    if contracts is not None and specs is None:
        raise click.UsageError("Use --specs with --contracts for explicit local inputs")
    try:
        with specification_inputs(specs) as (root, bundle):
            feature = (
                migration_paths(root)
                if migration_suite
                else feature_paths(root) if all_features else feature or ("acceptance/public/flush.feature",)
            )
            schemas = Contracts(contracts or root / "contracts/v2")
            report, diagnostics = asyncio.run(
                execute(
                    schemas,
                    root,
                    feature,
                    adapter_url,
                    profile,
                    case_ids=case_id,
                    timeout_ms=timeout_ms,
                    mock_bind_host=mock_bind_host,
                    mock_advertised_host=mock_advertised_host,
                    allow_private_network=allow_private_network,
                )
            )
            exit_code = strict_exit_code(schemas, report)
            diagnostics["distribution"] = distribution(bundle)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        diagnostics_path = report_path.with_name(report_path.name + ".diagnostics.json")
        diagnostics_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False) + "\n")
    except (BoundaryError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(summary(report))
    for row in report["results"]:
        problem = row["result"].get("failure")
        if problem:
            click.echo(f"{row['case_id']}: {problem['code']}: {problem['message']}")
    for error in report["errors"]:
        click.echo(f"{error['code']}: {error['message']}")
    click.echo(f"Report: {report_path}")
    click.echo(f"Diagnostics: {diagnostics_path}")
    click.echo("Strict gate: " + ("passed" if exit_code == 0 else "invalid report" if exit_code == 2 else "not passed"))
    raise SystemExit(exit_code)


@main.command()
@click.option(
    "--specs",
    type=click.Path(path_type=Path, file_okay=False),
    help="Explicit local inputs; defaults to the packaged specs bundle.",
)
@click.option(
    "--feature", multiple=True, help="Limit discovery to explicit paths; defaults to the entire frozen corpus"
)
@click.option("--report", "report_path", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--require-ready", is_flag=True, help="Exit nonzero if any case has missing harness bindings")
@click.option("--migration-suite", is_flag=True, help="Discover the versioned YAML-parity suite")
def discover(specs, feature, report_path, require_ready, migration_suite):
    """List execution routes and missing steps without contacting an SDK host."""
    if feature and migration_suite:
        raise click.UsageError("Use --feature or --migration-suite, not both")
    try:
        with specification_inputs(specs) as (root, bundle):
            report = discover_routes(root, migration_paths(root) if migration_suite else feature or None)
            report["distribution"] = distribution(bundle)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    except (BoundaryError, OSError) as error:
        raise click.ClickException(str(error)) from error
    ready = sum(case["status"] == "harness_ready" for case in report["cases"])
    missing = len(report["cases"]) - ready
    click.echo(f"{len(report['cases'])} discovered, {ready} harness-ready, {missing} missing harness bindings")
    click.echo("All cases are unexecuted; applicability and SDK bindings are not determined by discovery.")
    click.echo(f"Report: {report_path}")
    raise SystemExit(1 if require_ready and missing else 0)


def distribution(bundle):
    if bundle is None:
        return {"mode": "explicit-local-inputs", "runner_version": __version__}
    return {
        "mode": "packaged",
        "runner_version": __version__,
        **{key: bundle[key] for key in ("format", "bundle_sha256", "catalog_sha256", "source")},
    }


@main.command("bundle-info")
def bundle_info():
    """Verify the installed bundle and print its complete identity and file inventory."""
    try:
        with specification_inputs() as (_, manifest):
            click.echo(json.dumps(manifest, indent=2, sort_keys=True))
    except (BoundaryError, OSError) as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    main()
