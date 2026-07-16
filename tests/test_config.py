import asyncio
import tomllib
from io import StringIO
from pathlib import Path

from rich.console import Console
from textual.widgets import OptionList
from typer.testing import CliRunner

import lychee_alphadesk.cli.app as cli_app
from lychee_alphadesk.cli.app import (
    _providers_requiring_values,
    app,
)
from lychee_alphadesk.core.config import (
    config_file_path,
    default_config,
    ensure_config_file,
    load_config,
    save_config,
    set_news_provider_plugin_value,
)
from lychee_alphadesk.providers.news_plugins import (
    NewsProviderMetadata,
    NewsProviderRegistry,
    NewsProviderSetting,
)
from lychee_alphadesk.tui.setup import SetupApp

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


def test_setup_command_requires_keyboard_navigation_in_non_tty(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 2
    assert "交互式配置需要可用的终端键盘导航" in result.stdout
    assert "lychee setup set" in result.stdout
    assert "lychee setup llm set" in result.stdout
    assert "lychee setup plugin set" in result.stdout


def test_setup_command_opens_keyboard_configuration_center(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    launched: list[Path] = []
    monkeypatch.setattr(cli_app, "_keyboard_navigation_available", lambda: True)
    monkeypatch.setattr(cli_app, "run_setup_tui", lambda path: launched.append(path))

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert launched == [tmp_path / "lychee-alphadesk" / "config.yaml"]


def test_textual_setup_menu_uses_option_list_keyboard_navigation(tmp_path: Path) -> None:
    async def run_case() -> None:
        app = SetupApp(tmp_path / "config.yaml")
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one("#setup-menu", OptionList)
            assert menu.highlighted == 0

            await pilot.press("down")
            await pilot.pause()

            assert menu.highlighted == 1

    asyncio.run(run_case())


def test_setup_center_lists_installed_news_plugins_with_keyboard_navigation(
    monkeypatch, tmp_path: Path
) -> None:
    plugin = type(
        "AuditedEntityNewsPlugin",
        (),
        {
            "metadata": NewsProviderMetadata(
                provider_id="audited_entity",
                display_name="审计实体新闻",
                description="按公司实体提供可核验新闻。",
                capabilities=frozenset({"entity_news"}),
                settings=(
                    NewsProviderSetting(
                        key="api_key",
                        label="API Key",
                        description="新闻源访问凭据。",
                    ),
                ),
            ),
            "pull_news": lambda self, request: [],
        },
    )()
    monkeypatch.setattr(
        "lychee_alphadesk.tui.setup.discover_news_provider_plugins",
        lambda: NewsProviderRegistry(
            providers={"audited_entity": plugin},
            diagnostics=(),
        ),
        raising=False,
    )

    async def run_case() -> None:
        app = SetupApp(tmp_path / "config.yaml")
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "enter")
            await pilot.pause()

            menu = app.query_one("#plugin-menu", OptionList)
            assert menu.highlighted == 0

    asyncio.run(run_case())


def test_setup_center_can_store_selected_news_plugin_setting(
    monkeypatch, tmp_path: Path
) -> None:
    plugin = type(
        "AuditedEntityNewsPlugin",
        (),
        {
            "metadata": NewsProviderMetadata(
                provider_id="audited_entity",
                display_name="审计实体新闻",
                description="按公司实体提供可核验新闻。",
                capabilities=frozenset({"entity_news"}),
                settings=(
                    NewsProviderSetting(
                        key="api_key",
                        label="API Key",
                        description="新闻源访问凭据。",
                    ),
                ),
            ),
            "pull_news": lambda self, request: [],
        },
    )()
    monkeypatch.setattr(
        "lychee_alphadesk.tui.setup.discover_news_provider_plugins",
        lambda: NewsProviderRegistry(
            providers={"audited_entity": plugin},
            diagnostics=(),
        ),
        raising=False,
    )
    config_path = tmp_path / "config.yaml"

    async def run_case() -> None:
        app = SetupApp(config_path)
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "enter", "enter", "enter")
            await pilot.press(*"plugin-secret")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run_case())

    config = load_config(config_path)
    assert config.provider_plugins["audited_entity"].settings["api_key"] == "plugin-secret"


def test_textual_setup_disables_text_selection_to_avoid_mouse_selection_crash() -> None:
    assert SetupApp.ALLOW_SELECT is False


def test_textual_setup_app_can_store_selected_provider_value(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_path = tmp_path / "lychee-alphadesk" / "config.yaml"

    async def run_case() -> None:
        setup_app = SetupApp(config_path)
        async with setup_app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.press("d", "e", "m", "o", "-", "k", "e", "y")
            await pilot.press("enter")
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run_case())
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value == "demo-key"


def test_setup_center_can_configure_llm_provider(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        "lychee_alphadesk.tui.setup.fetch_openai_compatible_models",
        lambda base_url, api_key: ["gpt-demo-a", "gpt-demo-b"],
        raising=False,
    )
    config_path = tmp_path / "lychee-alphadesk" / "config.yaml"

    async def run_case() -> None:
        setup_app = SetupApp(config_path)
        async with setup_app.run_test() as pilot:
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.press(*"https://llm.example.com/v1")
            await pilot.press("enter")
            await pilot.press(*"sk-demo-secret")
            await pilot.press("enter")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run_case())
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
    assert "已保存 Alpha Vantage" in result.stdout
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value == "demo-key"


def test_news_provider_plugin_settings_persist_in_user_config(tmp_path: Path) -> None:
    path = set_news_provider_plugin_value(
        "audited_entity",
        "api_key",
        "plugin-secret",
        tmp_path / "config.yaml",
    )

    config = load_config(path)

    assert config.provider_plugins["audited_entity"].enabled is True
    assert config.provider_plugins["audited_entity"].settings == {
        "api_key": "plugin-secret"
    }


def test_setup_plugin_set_writes_single_plugin_setting(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(
        app,
        ["setup", "plugin", "set", "audited_entity", "api_key", "plugin-secret"],
    )

    assert result.exit_code == 0
    assert "已保存新闻插件配置" in result.stdout
    config = load_config(config_file_path())
    assert config.provider_plugins["audited_entity"].settings["api_key"] == "plugin-secret"


def test_setup_set_still_rejects_unknown_provider(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "set", "unknown_provider", "demo-key"])

    assert result.exit_code == 1
    assert "未知数据源" in result.stdout


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
    assert "已保存 OpenAI 兼容 LLM" in result.stdout
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
        "lychee_alphadesk.tui.setup.fetch_openai_compatible_models",
        lambda base_url, api_key: [],
        raising=False,
    )
    config_path = tmp_path / "lychee-alphadesk" / "config.yaml"

    async def run_case() -> None:
        setup_app = SetupApp(config_path)
        async with setup_app.run_test() as pilot:
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.press(*"https://llm.example.com/v1")
            await pilot.press("enter")
            await pilot.press(*"sk-demo-secret")
            await pilot.press("enter")
            await pilot.press(*"manual-model")
            await pilot.press("enter")
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run_case())
    config = load_config(config_file_path())
    assert config.llm.openai_compatible.model == "manual-model"


def test_setup_center_reports_empty_provider_value(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_path = tmp_path / "lychee-alphadesk" / "config.yaml"

    async def run_case() -> None:
        setup_app = SetupApp(config_path)
        async with setup_app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run_case())
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value is None


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

    assert cli_app._provider_config_status(provider) == "已配置: demo***-key"
    empty_provider = default_config().providers["alpha_vantage"]
    assert cli_app._provider_config_status(empty_provider) == "未配置"


def test_provider_menu_text_shows_display_name_and_masked_status_only(monkeypatch) -> None:
    buffer = StringIO()
    provider = default_config().providers["alpha_vantage"].model_copy(
        update={"value": "demo-secret-key"}
    )

    Console(file=buffer, force_terminal=False, width=140, color_system=None).print(
        cli_app._provider_menu_label(provider)
    )

    output = buffer.getvalue()
    assert "Alpha Vantage" in output
    assert "已配置: demo***-key" in output
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
    assert "全球行情、基本面、技术指标和宏观数据" in output
    assert "https://www.alphavantage.co/support/#api-key" in output
    assert "alpha_vantage" not in output
    assert "Required value" not in output
    assert "api_key" not in output
    assert "用途" in output
    assert "申请地址" in output
    assert "当前状态" in output
