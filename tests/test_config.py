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


def _feed_keyboard(monkeypatch, keys: list[str]) -> None:
    key_iter = iter(keys)
    monkeypatch.setattr(cli_app, "_keyboard_navigation_available", lambda: True)
    monkeypatch.setattr(cli_app, "_read_key", lambda: next(key_iter))


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


def test_setup_command_requires_keyboard_navigation_in_non_tty(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 2
    assert "requires an interactive terminal" in result.stdout
    assert "lychee setup set" in result.stdout
    assert "lychee setup llm set" in result.stdout


def test_setup_command_opens_keyboard_configuration_center(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _feed_keyboard(monkeypatch, ["escape"])

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert str(tmp_path / "lychee-alphadesk" / "config.yaml") in result.stdout
    assert "Lychee AlphaDesk Configuration Center" in result.stdout
    assert "Use ↑/↓/←/→/Tab to move" in result.stdout
    assert "Data providers" in result.stdout
    assert "LLM provider" in result.stdout
    assert "Choose setup area" not in result.stdout
    assert "Alpha Vantage" not in result.stdout
    assert "https://www.alphavantage.co/support/#api-key" not in result.stdout
    assert "lychee setup wizard" not in result.stdout
    assert "lychee setup llm wizard" not in result.stdout


def test_setup_center_can_configure_data_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _feed_keyboard(monkeypatch, ["enter", "enter", "escape", "escape"])

    result = runner.invoke(
        app,
        ["setup"],
        input="demo-key\n",
    )

    assert result.exit_code == 0
    assert "Lychee AlphaDesk Configuration Center" in result.stdout
    assert "Saved Alpha Vantage" in result.stdout
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value == "demo-key"


def test_setup_center_can_configure_llm_provider(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        cli_app,
        "_fetch_openai_compatible_models",
        lambda base_url, api_key: ["gpt-demo-a", "gpt-demo-b"],
        raising=False,
    )
    _feed_keyboard(monkeypatch, ["down", "enter", "down", "enter", "escape"])

    result = runner.invoke(
        app,
        ["setup"],
        input="https://llm.example.com/v1\nsk-demo-secret\n",
    )

    assert result.exit_code == 0
    assert "OpenAI-compatible custom endpoint" in result.stdout
    assert "Selected model: gpt-demo-b" in result.stdout
    config = load_config(config_file_path())
    assert config.llm.openai_compatible.base_url == "https://llm.example.com/v1"
    assert config.llm.openai_compatible.api_key == "sk-demo-secret"
    assert config.llm.openai_compatible.model == "gpt-demo-b"


def test_split_setup_subcommands_are_not_available(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    for removed_args in (
        ["setup", "wizard"],
        ["setup", "providers"],
        ["setup", "llm"],
        ["setup", "llm", "wizard"],
    ):
        result = runner.invoke(app, removed_args)
        assert result.exit_code != 0


def test_setup_set_still_writes_single_provider_secret(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "set", "alpha_vantage", "demo-key"])

    assert result.exit_code == 0
    assert "Saved alpha_vantage" in result.stdout
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value == "demo-key"


def test_setup_set_still_rejects_unknown_provider(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "set", "unknown_provider", "demo-key"])

    assert result.exit_code == 1
    assert "Unknown provider" in result.stdout


def test_setup_llm_set_still_writes_single_llm_config(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "setup",
            "llm",
            "set",
            "https://llm.example.com/v1",
            "sk-demo-secret",
            "gpt-demo",
        ],
    )

    assert result.exit_code == 0
    assert "Saved OpenAI-compatible LLM provider" in result.stdout
    config = load_config(config_file_path())
    assert config.llm.openai_compatible.base_url == "https://llm.example.com/v1"
    assert config.llm.openai_compatible.api_key == "sk-demo-secret"
    assert config.llm.openai_compatible.model == "gpt-demo"


def test_default_llm_config_is_unconfigured() -> None:
    config = default_config()

    assert config.llm.openai_compatible.base_url is None
    assert config.llm.openai_compatible.api_key is None
    assert config.llm.openai_compatible.model is None


def test_setup_center_falls_back_to_manual_llm_model_name(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        cli_app,
        "_fetch_openai_compatible_models",
        lambda base_url, api_key: [],
        raising=False,
    )
    _feed_keyboard(monkeypatch, ["down", "enter", "escape"])

    result = runner.invoke(
        app,
        ["setup"],
        input="https://llm.example.com/v1\nsk-demo-secret\nmanual-model\n",
    )

    assert result.exit_code == 0
    assert "Could not read models from /v1/models" in result.stdout
    config = load_config(config_file_path())
    assert config.llm.openai_compatible.model == "manual-model"


def test_setup_center_reports_empty_provider_value(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _feed_keyboard(monkeypatch, ["enter", "enter", "escape", "escape"])

    result = runner.invoke(app, ["setup"], input="\n")

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
    assert _move_menu_selection(1, "left", len(providers)) == 0
    assert _move_menu_selection(1, "right", len(providers)) == 2
    assert _move_menu_selection(1, "tab", len(providers)) == 2
    assert _move_menu_selection(0, "down", 0) == 0


def test_provider_menu_handles_empty_provider_list() -> None:
    assert _choose_provider_from_menu([]) is None


def test_sec_edgar_is_not_part_of_provider_key_setup() -> None:
    providers = _providers_requiring_values(default_config())

    assert "SEC EDGAR" not in [provider.name for provider in providers]


def test_fmp_is_not_part_of_default_provider_key_setup() -> None:
    providers = _providers_requiring_values(default_config())

    assert "Financial Modeling Prep" not in [provider.name for provider in providers]
    assert default_config().providers["fmp"].requires_value


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
    assert "Use ↑/↓/←/→/Tab" in output
    assert "alpha_vantage" not in output
    assert "api_key" not in output
    assert "q to" not in output
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
