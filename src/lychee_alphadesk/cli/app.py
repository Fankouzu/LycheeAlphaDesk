import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lychee_alphadesk.core import setup as setup_helpers
from lychee_alphadesk.core.audit import init_audit_db, list_audit_records
from lychee_alphadesk.core.config import (
    AlphaDeskConfig,
    ProviderSetupInfo,
    ensure_config_file,
    set_openai_compatible_llm,
    set_provider_value,
)
from lychee_alphadesk.core.data_engine import build_demo_data_snapshot, write_snapshot_json
from lychee_alphadesk.core.demo import REQUIRED_DEMO_FILES, check_demo_workspace
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR, DEMO_ROOT
from lychee_alphadesk.core.policy import load_policy, validate_policy
from lychee_alphadesk.core.reports import generate_demo_report
from lychee_alphadesk.tui.app import run_tui
from lychee_alphadesk.tui.setup import run_setup_tui

console = Console()
app = typer.Typer(
    help="Lychee AlphaDesk terminal-native investment research workbench.",
    invoke_without_command=True,
)
policy_app = typer.Typer(help="Investment policy commands.")
audit_app = typer.Typer(help="Audit trail commands.")
data_app = typer.Typer(help="Market, news, filing, and forecast data commands.")
setup_app = typer.Typer(
    help="Open the configuration center or write a single config value.",
    invoke_without_command=True,
)
llm_setup_app = typer.Typer(
    help="Write a single LLM provider config value.",
    invoke_without_command=True,
)


@app.callback()
def root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        run_tui()


@app.command()
def demo() -> None:
    """Check that bundled demo files are available."""
    missing = check_demo_workspace(DEMO_ROOT)
    if missing:
        for path in missing:
            console.print(f"Missing demo file: {path}")
        raise typer.Exit(code=1)

    init_audit_db(DEFAULT_OUTPUT_DIR)
    console.print("Demo workspace ready")
    for name in REQUIRED_DEMO_FILES:
        console.print(f"- examples/demo/{name}")
    console.print(f"Output directory: {DEFAULT_OUTPUT_DIR}")


@app.command()
def report(
    demo: Annotated[
        bool,
        typer.Option("--demo", help="Generate a report from bundled demo data."),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Report output directory."),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """Generate a Markdown daily report."""
    if not demo:
        console.print("Only --demo report generation is available in v0.1.")
        raise typer.Exit(code=1)

    result = generate_demo_report(output_dir=output_dir)
    console.print(f"Report written: {result.report_path}")
    console.print(f"Audit record: {result.audit_record.report_id}")


@policy_app.command("check")
def policy_check(path: Path) -> None:
    """Validate an investment policy YAML file."""
    policy = load_policy(path)
    result = validate_policy(policy)

    if result.ok:
        console.print("Policy check passed")
    else:
        console.print("Policy check failed")

    for item in result.passes:
        console.print(f"PASS: {item}")
    for item in result.warnings:
        console.print(f"WARNING: {item}")
    for item in result.errors:
        console.print(f"ERROR: {item}")

    if not result.ok:
        raise typer.Exit(code=1)


@audit_app.command("list")
def audit_list(
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Audit output directory."),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """List generated audit records."""
    records = list_audit_records(output_dir)
    if not records:
        console.print("No audit records found")
        return

    for record in records:
        console.print(
            f"Record: {record.report_id} {record.mode} {Path(record.report_path).name} "
            f"{record.report_path}"
        )

    table = Table(title="Lychee AlphaDesk Audit Records")
    table.add_column("Report ID")
    table.add_column("Created")
    table.add_column("Mode")
    table.add_column("Report File")
    table.add_column("Report Path")
    for record in records:
        table.add_row(
            record.report_id,
            record.created_at,
            record.mode,
            Path(record.report_path).name,
            record.report_path,
        )
    console.print(table)


@data_app.command("snapshot")
def data_snapshot(
    demo: Annotated[
        bool,
        typer.Option("--demo", help="Build a data snapshot from bundled demo providers."),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Snapshot output directory."),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """Write a unified JSON data snapshot."""
    if not demo:
        console.print("Only --demo data snapshots are available in v0.1.")
        raise typer.Exit(code=1)

    snapshot = build_demo_data_snapshot(DEMO_ROOT)
    output_path = write_snapshot_json(snapshot, output_dir)
    console.print(f"Data snapshot written: {output_path}")
    console.print(f"Providers: {', '.join(snapshot.provider_names)}")
    console.print(f"Prices: {snapshot.counts['prices']}")
    console.print(f"News events: {snapshot.counts['news_events']}")
    console.print(f"Filings: {snapshot.counts['filings']}")
    console.print(f"Forecasts: {snapshot.counts['forecasts']}")


@data_app.command("health")
def data_health(
    demo: Annotated[
        bool,
        typer.Option("--demo", help="Check bundled demo provider health."),
    ] = False,
) -> None:
    """Show provider data quality checks."""
    if not demo:
        console.print("Only --demo data health checks are available in v0.1.")
        raise typer.Exit(code=1)

    snapshot = build_demo_data_snapshot(DEMO_ROOT)
    console.print(f"Providers: {', '.join(snapshot.provider_names)}")
    table = Table(title="Lychee AlphaDesk Data Health")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Provider")
    table.add_column("Message")
    for check in snapshot.quality_checks:
        table.add_row(check.name, check.status, check.provider, check.message)
    console.print(table)


@setup_app.callback()
def setup(ctx: typer.Context) -> None:
    """Open the interactive configuration center."""
    if ctx.invoked_subcommand is not None:
        return
    path = ensure_config_file()
    _run_configuration_center(path)


@setup_app.command("set")
def setup_set(provider_id: str, value: str) -> None:
    """Save a provider API key or token in the config file."""
    try:
        path = set_provider_value(provider_id, value)
    except KeyError as error:
        console.print(f"Unknown provider: {provider_id}")
        console.print(str(error))
        raise typer.Exit(code=1) from error
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(code=1) from error

    console.print(f"Saved {provider_id} in {path}", soft_wrap=True)


@llm_setup_app.callback()
def setup_llm(ctx: typer.Context) -> None:
    """Require a non-interactive LLM setup command."""
    if ctx.invoked_subcommand is not None:
        return
    console.print("Use `lychee setup llm set <base_url> <api_key> MODEL_NAME`.")
    raise typer.Exit(code=2)


@llm_setup_app.command("set")
def setup_llm_set(
    base_url: str,
    api_key: str,
    model: Annotated[
        str | None,
        typer.Argument(help="Optional model name, such as gpt-4.1-mini."),
    ] = None,
) -> None:
    """Save an OpenAI-compatible LLM endpoint in the config file."""
    try:
        path = set_openai_compatible_llm(base_url, api_key, model)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(code=1) from error

    console.print(f"Saved OpenAI-compatible LLM provider in {path}", soft_wrap=True)


def _run_configuration_center(path: Path) -> None:
    if not _keyboard_navigation_available():
        console.print("Lychee AlphaDesk Configuration Center")
        console.print(f"Config file: {path}", soft_wrap=True)
        console.print(
            "Interactive setup requires an interactive terminal with keyboard navigation.",
            soft_wrap=True,
        )
        console.print("For automation, use `lychee setup set <provider_id> <value>`.")
        console.print(
            "For LLM automation, use `lychee setup llm set <base_url> <api_key> MODEL_NAME`.",
            soft_wrap=True,
        )
        raise typer.Exit(code=2)

    run_setup_tui(path)


def _print_provider_detail(provider: ProviderSetupInfo) -> None:
    console.clear()
    console.print(setup_helpers.provider_detail_text(provider), soft_wrap=True)


def _providers_requiring_values(config: AlphaDeskConfig) -> list[ProviderSetupInfo]:
    return setup_helpers.providers_requiring_values(config)


def _provider_config_status(provider: ProviderSetupInfo) -> str:
    return setup_helpers.provider_config_status(provider)


def _provider_menu_label(provider: ProviderSetupInfo) -> str:
    return setup_helpers.provider_menu_label(provider)


def _print_value_capture_result(*, received: bool) -> None:
    if received:
        console.print("[green]✅ Value received[/green]")
        return
    console.print("[red]❌ No value entered[/red]")


def _keyboard_navigation_available() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


app.add_typer(policy_app, name="policy")
app.add_typer(audit_app, name="audit")
app.add_typer(data_app, name="data")
setup_app.add_typer(llm_setup_app, name="llm")
app.add_typer(setup_app, name="setup")


def main() -> None:
    app()
