import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from lychee_alphadesk.core.audit import init_audit_db, list_audit_records
from lychee_alphadesk.core.config import (
    AlphaDeskConfig,
    ProviderSetupInfo,
    ensure_config_file,
    load_config,
    set_provider_value,
)
from lychee_alphadesk.core.data_engine import build_demo_data_snapshot, write_snapshot_json
from lychee_alphadesk.core.demo import REQUIRED_DEMO_FILES, check_demo_workspace
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR, DEMO_ROOT
from lychee_alphadesk.core.policy import load_policy, validate_policy
from lychee_alphadesk.core.reports import generate_demo_report
from lychee_alphadesk.tui.app import run_tui

console = Console()
app = typer.Typer(
    help="Lychee AlphaDesk terminal-native investment research workbench.",
    invoke_without_command=True,
)
policy_app = typer.Typer(help="Investment policy commands.")
audit_app = typer.Typer(help="Audit trail commands.")
data_app = typer.Typer(help="Market, news, filing, and forecast data commands.")
setup_app = typer.Typer(
    help="Configure provider API keys in the local config file.",
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
    """Create local config and show provider setup guidance."""
    if ctx.invoked_subcommand is not None:
        return
    path = ensure_config_file()
    config = load_config(path)
    console.print(f"Config file: {path}", soft_wrap=True)
    console.print("Run `lychee setup wizard` for the interactive setup flow.")
    console.print("Use `lychee setup providers` to list registration links.")
    console.print("Use `lychee setup set <provider_id> <value>` after getting a key.")
    _print_provider_setup_table(config)


@setup_app.command("providers")
def setup_providers() -> None:
    """List provider registration links and config fields."""
    config = load_config(ensure_config_file())
    _print_provider_setup_table(config)


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


@setup_app.command("wizard")
def setup_wizard() -> None:
    """Run an interactive provider-key setup wizard."""
    path = ensure_config_file()
    config = load_config(path)
    console.print("Lychee AlphaDesk Setup Wizard")
    console.print(f"Config file: {path}", soft_wrap=True)
    console.print("You can skip any provider and configure it later.")

    if not Confirm.ask("Configure provider keys now?", default=True):
        console.print("Skipped provider key configuration")
        return

    providers = _providers_requiring_values(config)
    while True:
        provider = _choose_provider_from_menu(providers)
        if provider is None:
            break

        _print_provider_detail(provider)
        value = _prompt_secret(_provider_value_prompt(provider))
        if not value.strip():
            _print_value_capture_result(received=False)
            console.print(f"Skipped {provider.name}")
        else:
            _print_value_capture_result(received=True)
            path = set_provider_value(provider.provider_id, value.strip())
            config = load_config(path)
            providers = _providers_requiring_values(config)
            console.print(f"Saved {provider.name} in {path}", soft_wrap=True)

        if not Confirm.ask("Configure another provider?", default=True):
            break

    console.print("Setup wizard complete")


def _print_provider_setup_table(config: AlphaDeskConfig) -> None:
    providers = sorted(
        config.providers.values(), key=lambda item: (item.priority, item.provider_id)
    )
    for provider in providers:
        console.print(
            f"Provider: {provider.provider_id} | {provider.name} | "
            f"{provider.registration} | {provider.registration_url}",
            soft_wrap=True,
        )

    table = Table(title="Lychee AlphaDesk Provider Setup")
    table.add_column("Provider ID")
    table.add_column("Name")
    table.add_column("Priority")
    table.add_column("Registration")
    table.add_column("Config Field")
    table.add_column("Registration URL")
    for provider in providers:
        table.add_row(
            provider.provider_id,
            provider.name,
            str(provider.priority),
            provider.registration,
            provider.config_field,
            provider.registration_url,
        )
    console.print(table)


def _choose_provider_from_menu(providers: list[ProviderSetupInfo]) -> ProviderSetupInfo | None:
    if not providers:
        console.print("No providers require setup")
        return None

    if sys.stdin.isatty() and sys.stdout.isatty() and _raw_tty_available():
        return _choose_provider_with_arrow_keys(providers)

    _print_wizard_menu(providers)
    selected = Prompt.ask("Choose provider number, or q to finish", default="q")
    if selected.lower() in {"q", "quit", "done", "finish"}:
        return None
    provider = _resolve_provider_choice(selected, providers)
    if provider is None:
        console.print(f"Unknown provider choice: {selected}")
    return provider


def _choose_provider_with_arrow_keys(
    providers: list[ProviderSetupInfo],
) -> ProviderSetupInfo | None:
    selected_index = 0
    while True:
        _render_arrow_menu(providers, selected_index)
        key = _read_key()
        if key in {"q", "Q", "\x03"}:
            console.print("Provider selection cancelled")
            return None
        if key in {"\r", "\n"}:
            return providers[selected_index]
        if key == "up":
            selected_index = _move_menu_selection(selected_index, "up", len(providers))
        elif key == "down":
            selected_index = _move_menu_selection(selected_index, "down", len(providers))


def _render_arrow_menu(providers: list[ProviderSetupInfo], selected_index: int) -> None:
    console.clear()
    console.print("Provider Key Menu")
    console.print("Use ↑/↓ to move, Enter to view details, q to finish.")
    for index, provider in enumerate(providers):
        marker = ">" if index == selected_index else " "
        console.print(f"{marker} {provider.name:<30} {_provider_config_status(provider)}")


def _print_provider_detail(provider: ProviderSetupInfo) -> None:
    console.clear()
    console.print(provider.name)
    console.print(f"Use: {provider.domain}")
    console.print(f"Signup: {_provider_registration_summary(provider)}")
    console.print(f"Registration URL: {provider.registration_url}", soft_wrap=True)
    console.print(f"Status: {_provider_config_status(provider)}")
    if provider.notes:
        console.print(f"Notes: {_provider_notes(provider)}", soft_wrap=True)


def _provider_registration_summary(provider: ProviderSetupInfo) -> str:
    if provider.config_field == "user_agent":
        return "No API key required; Lychee AlphaDesk handles request identity internally."
    return provider.registration


def _provider_notes(provider: ProviderSetupInfo) -> str:
    if provider.config_field == "user_agent":
        return "Used for compliant SEC access; regular users do not need to configure it."
    return provider.notes


def _provider_value_prompt(provider: ProviderSetupInfo) -> str:
    if provider.config_field == "api_key":
        return f"Paste {provider.name} API key"
    if provider.config_field == "token":
        return f"Paste {provider.name} token"
    if provider.config_field == "user_agent":
        return f"Paste {provider.name} configuration value"
    return f"Paste {provider.name} configuration value"


def _print_value_capture_result(*, received: bool) -> None:
    if received:
        console.print("[green]✅ Value received[/green]")
        return
    console.print("[red]❌ No value entered[/red]")


def _provider_config_status(provider: ProviderSetupInfo) -> str:
    if provider.value and provider.value.strip():
        return f"Configured: {_mask_config_value(provider.value.strip())}"
    return "Not configured"


def _mask_config_value(value: str) -> str:
    if len(value) <= 1:
        return "***"
    if len(value) <= 4:
        return f"{value[0]}***{value[-1]}"
    return f"{value[:4]}***{value[-4:]}"


def _raw_tty_available() -> bool:
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except ImportError:
        return False
    return True


def _read_key() -> str:
    import termios
    import tty

    file_descriptor = sys.stdin.fileno()
    old_settings = termios.tcgetattr(file_descriptor)
    try:
        tty.setraw(file_descriptor)
        first = sys.stdin.read(1)
        if first == "\x1b":
            second = sys.stdin.read(1)
            third = sys.stdin.read(1)
            if second == "[" and third == "A":
                return "up"
            if second == "[" and third == "B":
                return "down"
        return first
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, old_settings)


def _providers_requiring_values(config: AlphaDeskConfig) -> list[ProviderSetupInfo]:
    return [
        provider
        for provider in sorted(
            config.providers.values(), key=lambda item: (item.priority, item.provider_id)
        )
        if provider.requires_value and provider.show_in_wizard
    ]


def _print_wizard_menu(providers: list[ProviderSetupInfo]) -> None:
    table = Table(title="Provider Key Menu")
    table.add_column("#")
    table.add_column("Name")
    table.add_column("Status")
    for index, provider in enumerate(providers, start=1):
        table.add_row(
            str(index),
            provider.name,
            _provider_config_status(provider),
        )
    console.print(table)


def _resolve_provider_choice(
    selected: str, providers: list[ProviderSetupInfo]
) -> ProviderSetupInfo | None:
    if selected.isdigit():
        index = int(selected)
        if 1 <= index <= len(providers):
            return providers[index - 1]
    for provider in providers:
        if provider.provider_id == selected:
            return provider
    return None


def _move_menu_selection(current_index: int, direction: str, total: int) -> int:
    if total <= 0:
        return 0
    if direction == "up":
        return (current_index - 1) % total
    if direction == "down":
        return (current_index + 1) % total
    return current_index


def _prompt_secret(prompt: str) -> str:
    return Prompt.ask(prompt, password=sys.stdin.isatty())


app.add_typer(policy_app, name="policy")
app.add_typer(audit_app, name="audit")
app.add_typer(data_app, name="data")
app.add_typer(setup_app, name="setup")


def main() -> None:
    app()
