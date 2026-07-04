import json
import select
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from lychee_alphadesk.core.audit import init_audit_db, list_audit_records
from lychee_alphadesk.core.config import (
    AlphaDeskConfig,
    ProviderSetupInfo,
    ensure_config_file,
    load_config,
    set_openai_compatible_llm,
    set_provider_value,
)
from lychee_alphadesk.core.data_engine import build_demo_data_snapshot, write_snapshot_json
from lychee_alphadesk.core.demo import REQUIRED_DEMO_FILES, check_demo_workspace
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR, DEMO_ROOT
from lychee_alphadesk.core.policy import load_policy, validate_policy
from lychee_alphadesk.core.reports import generate_demo_report
from lychee_alphadesk.tui.app import run_tui

console = Console()
ESC_SEQUENCE_TIMEOUT_SECONDS = 0.25
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
    console.print("Lychee AlphaDesk Configuration Center")
    console.print(f"Config file: {path}", soft_wrap=True)
    if not _keyboard_navigation_available():
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

    while True:
        config = load_config(path)
        selected = _choose_setup_area(config)
        if selected is None:
            console.print("Setup complete")
            return
        if selected == "data":
            _configure_data_providers()
        elif selected == "llm":
            _configure_llm_provider(path)


def _choose_setup_area(config: AlphaDeskConfig) -> str | None:
    return _choose_keyboard_menu(
        "Setup",
        [
            ("data", "Data providers", _data_provider_status(config)),
            ("llm", "LLM provider", _llm_provider_status(config)),
        ],
        select_label="select setup area",
        escape_label="finish",
    )


def _data_provider_status(config: AlphaDeskConfig) -> str:
    configured_count = sum(
        1
        for provider in _providers_requiring_values(config)
        if provider.value and provider.value.strip()
    )
    total_count = len(_providers_requiring_values(config))
    return f"{configured_count}/{total_count} configured"


def _llm_provider_status(config: AlphaDeskConfig) -> str:
    provider = config.llm.openai_compatible
    if provider.base_url and provider.api_key and provider.model:
        return f"Configured: {provider.model}"
    if provider.base_url or provider.api_key or provider.model:
        return "Partially configured"
    return "Not configured"


def _configure_data_providers() -> None:
    config = load_config(ensure_config_file())
    providers = _providers_requiring_values(config)
    while True:
        provider = _choose_provider_from_menu(providers)
        if provider is None:
            return

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


def _configure_llm_provider(path: Path) -> None:
    console.print("OpenAI-compatible custom endpoint")
    console.print(f"Config file: {path}", soft_wrap=True)
    console.print(
        "Use this for OpenAI-compatible gateways, self-hosted endpoints, or model routers.",
        soft_wrap=True,
    )

    base_url = Prompt.ask("OpenAI-compatible Base URL").strip()
    if not base_url:
        console.print("[red]Base URL is required[/red]")
        return

    api_key = _prompt_secret("Paste OpenAI-compatible API key")
    if not api_key.strip():
        _print_value_capture_result(received=False)
        return
    _print_value_capture_result(received=True)

    model = _choose_openai_compatible_model(base_url, api_key.strip())
    if not model:
        _print_value_capture_result(received=False)
        return

    try:
        saved_path = set_openai_compatible_llm(base_url, api_key.strip(), model)
    except ValueError as error:
        console.print(str(error))
        return

    console.print(f"Saved OpenAI-compatible LLM provider in {saved_path}", soft_wrap=True)


def _choose_openai_compatible_model(base_url: str, api_key: str) -> str:
    models = _fetch_openai_compatible_models(base_url, api_key)
    if not models:
        console.print("Could not read models from /v1/models. Enter a model name manually.")
        return Prompt.ask("Model name").strip()

    selected_model = _choose_model_with_arrow_keys(models)
    if selected_model is None:
        return ""
    console.print(f"Selected model: {selected_model}")
    return selected_model


def _fetch_openai_compatible_models(base_url: str, api_key: str) -> list[str]:
    endpoint = f"{base_url.rstrip('/')}/models"
    request = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []
    return _parse_openai_compatible_models(payload)


def _parse_openai_compatible_models(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    models: list[str] = []
    seen: set[str] = set()
    for item in data:
        model_id: str | None = None
        if isinstance(item, dict):
            value = item.get("id")
            if isinstance(value, str):
                model_id = value.strip()
        elif isinstance(item, str):
            model_id = item.strip()

        if model_id and model_id not in seen:
            models.append(model_id)
            seen.add(model_id)
    return models


def _choose_model_with_arrow_keys(models: list[str]) -> str | None:
    selected_index = 0
    while True:
        _render_model_arrow_menu(models, selected_index)
        key = _read_key()
        if key in {"escape", "ctrl_c"}:
            console.print("Model selection cancelled")
            return None
        if key in {"\r", "\n", "enter"}:
            return models[selected_index]
        selected_index = _move_menu_selection(selected_index, key, len(models))


def _render_model_arrow_menu(models: list[str], selected_index: int) -> None:
    console.clear()
    console.print("Available models")
    console.print("Use ↑/↓/←/→/Tab to move, Enter to select, Esc to go back.")
    for index, model in enumerate(models):
        marker = ">" if index == selected_index else " "
        console.print(f"{marker} {model}")


def _choose_keyboard_menu(
    title: str,
    rows: list[tuple[str, str, str]],
    *,
    select_label: str,
    escape_label: str,
) -> str | None:
    selected_index = 0
    while True:
        _render_keyboard_menu(title, rows, selected_index, select_label, escape_label)
        key = _read_key()
        if key in {"escape", "ctrl_c"}:
            return None
        if key in {"\r", "\n", "enter"}:
            return rows[selected_index][0]
        selected_index = _move_menu_selection(selected_index, key, len(rows))


def _render_keyboard_menu(
    title: str,
    rows: list[tuple[str, str, str]],
    selected_index: int,
    select_label: str,
    escape_label: str,
) -> None:
    console.clear()
    console.print(title)
    console.print(
        f"Use ↑/↓/←/→/Tab to move, Enter to {select_label}, Esc to {escape_label}."
    )
    for index, (_, label, status) in enumerate(rows):
        marker = ">" if index == selected_index else " "
        console.print(f"{marker} {label:<30} {status}")


def _choose_provider_from_menu(providers: list[ProviderSetupInfo]) -> ProviderSetupInfo | None:
    if not providers:
        console.print("No providers require setup")
        return None

    return _choose_provider_with_arrow_keys(providers)


def _choose_provider_with_arrow_keys(
    providers: list[ProviderSetupInfo],
) -> ProviderSetupInfo | None:
    selected_index = 0
    while True:
        _render_arrow_menu(providers, selected_index)
        key = _read_key()
        if key in {"escape", "ctrl_c"}:
            console.print("Provider selection cancelled")
            return None
        if key in {"\r", "\n", "enter"}:
            return providers[selected_index]
        selected_index = _move_menu_selection(selected_index, key, len(providers))


def _render_arrow_menu(providers: list[ProviderSetupInfo], selected_index: int) -> None:
    console.clear()
    console.print("Provider Key Menu")
    console.print("Use ↑/↓/←/→/Tab to move, Enter to view details, Esc to go back.")
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
        return _normalize_keypress(first, lambda: _read_available_char(file_descriptor))
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, old_settings)


def _read_available_char(file_descriptor: int) -> str:
    if not select.select([file_descriptor], [], [], ESC_SEQUENCE_TIMEOUT_SECONDS)[0]:
        return ""
    return sys.stdin.read(1)


def _normalize_keypress(first: str, read_next: Callable[[], str]) -> str:
    if first == "\x03":
        return "ctrl_c"
    if first in {"\r", "\n"}:
        return "enter"
    if first == "\t":
        return "tab"
    if first != "\x1b":
        return first

    second = read_next()
    if not second:
        return "escape"
    third = read_next()
    if second == "[" and third == "A":
        return "up"
    if second == "[" and third == "B":
        return "down"
    if second == "[" and third == "C":
        return "right"
    if second == "[" and third == "D":
        return "left"
    if second == "[" and third == "Z":
        return "shift_tab"
    return "escape"


def _providers_requiring_values(config: AlphaDeskConfig) -> list[ProviderSetupInfo]:
    return [
        provider
        for provider in sorted(
            config.providers.values(), key=lambda item: (item.priority, item.provider_id)
        )
        if provider.requires_value and provider.show_in_wizard
    ]


def _move_menu_selection(current_index: int, direction: str, total: int) -> int:
    if total <= 0:
        return 0
    if direction in {"up", "left", "shift_tab"}:
        return (current_index - 1) % total
    if direction in {"down", "right", "tab"}:
        return (current_index + 1) % total
    return current_index


def _keyboard_navigation_available() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and _raw_tty_available()


def _prompt_secret(prompt: str) -> str:
    return Prompt.ask(prompt, password=sys.stdin.isatty())


app.add_typer(policy_app, name="policy")
app.add_typer(audit_app, name="audit")
app.add_typer(data_app, name="data")
setup_app.add_typer(llm_setup_app, name="llm")
app.add_typer(setup_app, name="setup")


def main() -> None:
    app()
