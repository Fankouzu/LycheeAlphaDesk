import tomllib
from io import StringIO
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

import lychee_alphadesk.cli.app as cli_app
from lychee_alphadesk.cli.app import (
    _choose_provider_from_menu,
    _move_menu_selection,
    _providers_requiring_values,
    _resolve_provider_choice,
    app,
)
from lychee_alphadesk.core.config import (
    config_file_path,
    default_config,
    ensure_config_file,
    load_config,
    save_config,
)

runner = CliRunner()


def test_pyproject_exposes_lychee_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["lychee"] == "lychee_alphadesk.cli.app:main"


def test_config_file_path_uses_xdg_config_home(monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/lad-config-test")

    assert config_file_path() == Path("/tmp/lad-config-test/lychee-alphadesk/config.yaml")


def test_ensure_config_file_creates_private_yaml(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = ensure_config_file()

    assert path == tmp_path / "lychee-alphadesk" / "config.yaml"
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    config = load_config(path)
    assert "alpha_vantage" in config.providers
    assert config.providers["alpha_vantage"].registration_url.startswith("https://")


def test_load_config_migrates_provider_metadata_from_registry(tmp_path: Path) -> None:
    config = default_config()
    config.providers["sec_edgar"] = config.providers["sec_edgar"].model_copy(
        update={"config_field": "user_agent", "value": "old-user-agent"}
    )
    config.providers["alpha_vantage"] = config.providers["alpha_vantage"].model_copy(
        update={"value": "demo-key"}
    )
    path = save_config(config, tmp_path / "config.yaml")

    migrated = load_config(path)

    assert migrated.providers["sec_edgar"].config_field == "none"
    assert migrated.providers["sec_edgar"].value is None
    assert migrated.providers["alpha_vantage"].value == "demo-key"
    assert "SEC EDGAR" not in [
        provider.name for provider in _providers_requiring_values(migrated)
    ]


def test_setup_command_shows_config_path_and_provider_urls(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert str(tmp_path / "lychee-alphadesk" / "config.yaml") in result.stdout
    assert "lychee setup providers" in result.stdout
    assert "Alpha Vantage" in result.stdout
    assert "https://www.alphavantage.co/support/#api-key" in result.stdout
    assert "Run `lychee setup wizard` for the interactive setup flow." in result.stdout


def test_setup_set_writes_provider_secret(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "set", "alpha_vantage", "demo-key"])

    assert result.exit_code == 0
    assert "Saved alpha_vantage" in result.stdout
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value == "demo-key"


def test_setup_set_rejects_unknown_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "set", "unknown_provider", "demo-key"])

    assert result.exit_code == 1
    assert "Unknown provider" in result.stdout


def test_setup_set_rejects_provider_without_user_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "set", "sec_edgar", "anything"])

    assert result.exit_code == 1
    assert "does not require" in result.stdout


def test_setup_wizard_can_skip_all_providers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "wizard"], input="n\n")

    assert result.exit_code == 0
    assert "Lychee AlphaDesk Setup Wizard" in result.stdout
    assert "Skipped provider key configuration" in result.stdout
    assert (tmp_path / "lychee-alphadesk" / "config.yaml").exists()


def test_setup_wizard_can_store_selected_provider_value(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "wizard"], input="y\nalpha_vantage\ndemo-key\nn\n")

    assert result.exit_code == 0
    assert "✅ Value received" in result.stdout
    assert "❌ No value entered" not in result.stdout
    assert "Saved Alpha Vantage" in result.stdout
    assert "Required value" not in result.stdout
    assert "alpha_vantage" not in result.stdout
    assert "api_key" not in result.stdout
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value == "demo-key"


def test_setup_wizard_reports_empty_provider_value(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "wizard"], input="y\nalpha_vantage\n\nn\n")

    assert result.exit_code == 0
    assert "❌ No value entered" in result.stdout
    assert "✅ Value received" not in result.stdout
    assert "Skipped Alpha Vantage" in result.stdout
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value is None


def test_arrow_menu_selection_wraps_between_providers() -> None:
    providers = _providers_requiring_values(default_config())

    assert _move_menu_selection(0, "up", len(providers)) == len(providers) - 1
    assert _move_menu_selection(len(providers) - 1, "down", len(providers)) == 0
    assert _move_menu_selection(1, "up", len(providers)) == 0
    assert _move_menu_selection(1, "down", len(providers)) == 2
    assert _move_menu_selection(0, "down", 0) == 0


def test_provider_menu_handles_empty_provider_list() -> None:
    assert _choose_provider_from_menu([]) is None


def test_provider_choice_fallback_still_accepts_number_and_id() -> None:
    providers = _providers_requiring_values(default_config())

    assert _resolve_provider_choice("2", providers) == providers[1]
    assert _resolve_provider_choice("alpha_vantage", providers).provider_id == "alpha_vantage"
    assert _resolve_provider_choice("not-real", providers) is None


def test_sec_edgar_is_not_part_of_provider_key_setup() -> None:
    providers = _providers_requiring_values(default_config())

    assert "SEC EDGAR" not in [provider.name for provider in providers]


def test_provider_config_status_masks_configured_values() -> None:
    provider = default_config().providers["alpha_vantage"].model_copy(
        update={"value": "demo-secret-key"}
    )

    assert cli_app._provider_config_status(provider) == "Configured: demo***-key"
    empty_provider = default_config().providers["alpha_vantage"]
    assert cli_app._provider_config_status(empty_provider) == "Not configured"


def test_arrow_menu_shows_display_name_and_masked_status_only(monkeypatch) -> None:
    buffer = StringIO()
    monkeypatch.setattr(
        cli_app,
        "console",
        Console(file=buffer, force_terminal=False, width=140, color_system=None),
    )
    provider = default_config().providers["alpha_vantage"].model_copy(
        update={"value": "demo-secret-key"}
    )

    cli_app._render_arrow_menu([provider], 0)

    output = buffer.getvalue()
    assert "Alpha Vantage" in output
    assert "Configured: demo***-key" in output
    assert "alpha_vantage" not in output
    assert "api_key" not in output
    assert "https://" not in output


def test_provider_detail_uses_user_facing_copy_without_internal_fields(monkeypatch) -> None:
    buffer = StringIO()
    monkeypatch.setattr(
        cli_app,
        "console",
        Console(file=buffer, force_terminal=False, width=140, color_system=None),
    )
    provider = default_config().providers["alpha_vantage"]

    cli_app._print_provider_detail(provider)

    output = buffer.getvalue()
    assert "Alpha Vantage" in output
    assert "Global prices, fundamentals, indicators, macro" in output
    assert "https://www.alphavantage.co/support/#api-key" in output
    assert "alpha_vantage" not in output
    assert "Required value" not in output
    assert "api_key" not in output
    assert "用途" not in output
    assert "申请方式" not in output
    assert "当前状态" not in output
