from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from lychee_alphadesk.core.config import (
    AlphaDeskConfig,
    ProviderSetupInfo,
    ensure_config_file,
    load_config,
    set_news_provider_plugin_value,
    set_openai_compatible_llm,
    set_provider_value,
)
from lychee_alphadesk.core.setup import (
    data_provider_status,
    fetch_openai_compatible_models,
    llm_provider_status,
    provider_detail_text,
    provider_menu_label,
    provider_value_prompt,
    providers_requiring_values,
)
from lychee_alphadesk.providers.news_plugins import (
    NewsProviderPlugin,
    NewsProviderSetting,
    discover_news_provider_plugins,
    missing_required_settings,
)

SetupView = Literal[
    "main",
    "providers",
    "provider_detail",
    "llm_base_url",
    "llm_api_key",
    "llm_model_select",
    "llm_manual_model",
    "plugins",
    "plugin_detail",
    "plugin_setting_input",
]


class SetupApp(App[None]):
    TITLE = "Lychee AlphaDesk 配置中心"
    SUB_TITLE = "仅使用键盘导航"
    ALLOW_SELECT = False
    BINDINGS = [
        Binding("escape", "back", "返回 / 完成", show=True),
        Binding("ctrl+c", "quit", "退出", show=False),
    ]
    CSS = """
    #setup-body {
        padding: 1 2;
    }

    #setup-help,
    #setup-message {
        padding: 0 2;
    }

    Input {
        margin-top: 1;
    }
    """

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self.view: SetupView = "main"
        self.selected_provider_id: str | None = None
        self.selected_plugin_id: str | None = None
        self.selected_plugin_setting: NewsProviderSetting | None = None
        self.pending_llm_base_url: str | None = None
        self.pending_llm_api_key: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "使用 ↑/↓/←/→/Tab 移动，Enter 选择，Esc 返回或完成。",
            id="setup-help",
        )
        yield Container(id="setup-body")
        yield Static("", id="setup-message")
        yield Footer()

    async def on_mount(self) -> None:
        ensure_config_file(self.config_path)
        await self.show_main_menu()

    async def action_back(self) -> None:
        if self.view == "main":
            self.exit()
            return
        if self.view == "provider_detail":
            await self.show_provider_menu()
            return
        if self.view == "plugin_setting_input":
            plugin = self._selected_news_plugin()
            if plugin is not None:
                await self.show_plugin_detail(plugin)
                return
        if self.view == "plugin_detail":
            await self.show_plugin_menu()
            return
        if self.view == "plugins":
            await self.show_main_menu()
            return
        await self.show_main_menu()

    async def show_main_menu(self, message: str = "") -> None:
        config = load_config(self.config_path)
        self.view = "main"
        await self._replace_body(
            OptionList(
                Option(
                    f"{'数据源':<30} {data_provider_status(config)}",
                    id="data",
                ),
                Option(
                    f"{'LLM 服务':<30} {llm_provider_status(config)}",
                    id="llm",
                ),
                Option(
                    f"{'新闻插件':<30} {_news_plugin_summary(config)}",
                    id="plugins",
                ),
                id="setup-menu",
                markup=False,
            )
        )
        self._set_message(message)
        self.set_focus(self.query_one("#setup-menu", OptionList))

    async def show_provider_menu(self, message: str = "") -> None:
        config = load_config(self.config_path)
        providers = providers_requiring_values(config)
        self.view = "providers"
        if not providers:
            await self.show_main_menu("没有需要配置密钥的数据源")
            return
        await self._replace_body(
            Static("数据源密钥"),
            OptionList(
                *[
                    Option(provider_menu_label(provider), id=provider.provider_id)
                    for provider in providers
                ],
                id="provider-menu",
                markup=False,
            ),
        )
        self._set_message(message)
        self.set_focus(self.query_one("#provider-menu", OptionList))

    async def show_provider_detail(self, provider: ProviderSetupInfo) -> None:
        self.view = "provider_detail"
        self.selected_provider_id = provider.provider_id
        value_input = Input(
            placeholder=provider_value_prompt(provider),
            password=True,
            id="provider-value",
        )
        await self._replace_body(
            Static(provider_detail_text(provider)),
            value_input,
        )
        self._set_message("")
        self.set_focus(value_input)

    async def show_plugin_menu(self, message: str = "") -> None:
        config = load_config(self.config_path)
        plugins = sorted(
            discover_news_provider_plugins().providers.values(),
            key=lambda plugin: plugin.metadata.display_name,
        )
        self.view = "plugins"
        if not plugins:
            await self._replace_body(
                Static("新闻插件\n尚未发现已安装的新闻插件。"),
            )
            self._set_message("安装插件后重新打开此页面即可配置。")
            return
        await self._replace_body(
            Static("新闻插件"),
            OptionList(
                *[
                    Option(
                        f"{plugin.metadata.display_name:<30} {_news_plugin_status(plugin, config)}",
                        id=plugin.metadata.provider_id,
                    )
                    for plugin in plugins
                ],
                id="plugin-menu",
                markup=False,
            ),
        )
        self._set_message(message)
        self.set_focus(self.query_one("#plugin-menu", OptionList))

    async def show_plugin_detail(self, plugin: NewsProviderPlugin) -> None:
        self.view = "plugin_detail"
        self.selected_plugin_id = plugin.metadata.provider_id
        detail = (
            f"{plugin.metadata.display_name}\n"
            f"{plugin.metadata.description}\n"
            f"支持范围: {_plugin_capability_text(plugin)}"
        )
        if not plugin.metadata.settings:
            await self._replace_body(Static(detail + "\n此插件无需额外配置。"))
            self._set_message("")
            return
        config = load_config(self.config_path)
        plugin_config = config.provider_plugins.get(plugin.metadata.provider_id)
        settings = plugin_config.settings if plugin_config else {}
        await self._replace_body(
            Static(detail),
            OptionList(
                *[
                    Option(
                        f"{setting.label:<30} {_plugin_setting_status(setting, settings)}",
                        id=setting.key,
                    )
                    for setting in plugin.metadata.settings
                ],
                id="plugin-setting-menu",
                markup=False,
            ),
        )
        self._set_message("")
        self.set_focus(self.query_one("#plugin-setting-menu", OptionList))

    async def show_plugin_setting_input(self, setting: NewsProviderSetting) -> None:
        self.view = "plugin_setting_input"
        self.selected_plugin_setting = setting
        value_input = Input(
            placeholder=setting.description,
            password=setting.secret,
            id="plugin-setting-value",
        )
        await self._replace_body(Static(setting.label), value_input)
        self._set_message("")
        self.set_focus(value_input)

    async def show_llm_base_url(self) -> None:
        self.view = "llm_base_url"
        base_url_input = Input(
            placeholder="OpenAI 兼容接口 Base URL，例如 https://api.example.com/v1",
            id="llm-base-url",
        )
        await self._replace_body(
            Static(
                "OpenAI 兼容自定义端点\n"
                f"配置文件: {self.config_path}\n"
                "可用于 OpenAI 兼容网关、自托管接口或模型路由服务。"
            ),
            base_url_input,
        )
        self._set_message("")
        self.set_focus(base_url_input)

    async def show_llm_api_key(self) -> None:
        self.view = "llm_api_key"
        api_key_input = Input(
            placeholder="粘贴 OpenAI 兼容 API Key",
            password=True,
            id="llm-api-key",
        )
        await self._replace_body(
            Static("OpenAI 兼容自定义端点"),
            api_key_input,
        )
        self._set_message("")
        self.set_focus(api_key_input)

    async def show_llm_model_menu(self, models: list[str]) -> None:
        self.view = "llm_model_select"
        await self._replace_body(
            Static("可用模型"),
            OptionList(
                *[Option(model, id=model) for model in models],
                id="model-menu",
                markup=False,
            ),
        )
        self._set_message("")
        self.set_focus(self.query_one("#model-menu", OptionList))

    async def show_manual_llm_model(self) -> None:
        self.view = "llm_manual_model"
        model_input = Input(
            placeholder="模型名称，例如 gpt-4.1-mini",
            id="llm-model-name",
        )
        await self._replace_body(
            Static("无法从 /v1/models 读取模型列表，请手动输入模型名称。"),
            model_input,
        )
        self._set_message("")
        self.set_focus(model_input)

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        event.stop()
        option_id = event.option.id
        if not option_id:
            return

        if event.option_list.id == "setup-menu":
            if option_id == "data":
                await self.show_provider_menu()
            elif option_id == "llm":
                await self.show_llm_base_url()
            elif option_id == "plugins":
                await self.show_plugin_menu()
            return

        if event.option_list.id == "provider-menu":
            provider = load_config(self.config_path).providers[option_id]
            await self.show_provider_detail(provider)
            return

        if event.option_list.id == "model-menu":
            await self._save_llm_model(option_id)
            return

        if event.option_list.id == "plugin-menu":
            plugin = discover_news_provider_plugins().providers.get(option_id)
            if plugin is None:
                await self.show_plugin_menu("❌ 新闻插件已不可用，请重新打开配置中心")
                return
            await self.show_plugin_detail(plugin)
            return

        if event.option_list.id == "plugin-setting-menu":
            plugin = self._selected_news_plugin()
            if plugin is None:
                await self.show_plugin_menu("❌ 新闻插件已不可用，请重新选择")
                return
            setting = next(
                (item for item in plugin.metadata.settings if item.key == option_id),
                None,
            )
            if setting is not None:
                await self.show_plugin_setting_input(setting)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value.strip()
        input_id = event.input.id

        if input_id == "provider-value":
            await self._save_provider_value(value)
            return

        if input_id == "llm-base-url":
            await self._capture_llm_base_url(value)
            return

        if input_id == "llm-api-key":
            await self._capture_llm_api_key(value)
            return

        if input_id == "llm-model-name":
            await self._save_llm_model(value)
            return

        if input_id == "plugin-setting-value":
            await self._save_plugin_setting(value)

    async def _save_provider_value(self, value: str) -> None:
        if not self.selected_provider_id:
            await self.show_provider_menu("❌ 尚未选择数据源")
            return
        if not value:
            await self.show_provider_menu("❌ 未输入配置值")
            return
        provider = load_config(self.config_path).providers[self.selected_provider_id]
        set_provider_value(provider.provider_id, value, self.config_path)
        await self.show_provider_menu(f"✅ 已保存 {provider.name}")

    async def _save_plugin_setting(self, value: str) -> None:
        plugin = self._selected_news_plugin()
        setting = self.selected_plugin_setting
        if plugin is None or setting is None:
            await self.show_plugin_menu("❌ 尚未选择新闻插件设置项")
            return
        if not value:
            await self.show_plugin_detail(plugin)
            self._set_message("❌ 未输入配置值")
            return
        try:
            set_news_provider_plugin_value(
                plugin.metadata.provider_id,
                setting.key,
                value,
                self.config_path,
            )
        except ValueError as error:
            await self.show_plugin_detail(plugin)
            self._set_message(f"❌ {error}")
            return
        await self.show_plugin_detail(plugin)
        self._set_message(f"✅ 已保存 {plugin.metadata.display_name}：{setting.label}")

    async def _capture_llm_base_url(self, value: str) -> None:
        if not value:
            await self.show_main_menu("❌ Base URL 不能为空")
            return
        self.pending_llm_base_url = value
        await self.show_llm_api_key()

    async def _capture_llm_api_key(self, value: str) -> None:
        if not value:
            await self.show_main_menu("❌ 未输入 API Key")
            return
        if not self.pending_llm_base_url:
            await self.show_main_menu("❌ Base URL 不能为空")
            return

        self.pending_llm_api_key = value
        models = fetch_openai_compatible_models(self.pending_llm_base_url, value)
        if models:
            await self.show_llm_model_menu(models)
            return
        await self.show_manual_llm_model()

    async def _save_llm_model(self, model: str) -> None:
        if not model:
            await self.show_main_menu("❌ 未输入模型名称")
            return
        if not self.pending_llm_base_url or not self.pending_llm_api_key:
            await self.show_main_menu("❌ LLM 端点配置不完整")
            return

        try:
            set_openai_compatible_llm(
                self.pending_llm_base_url,
                self.pending_llm_api_key,
                model,
                self.config_path,
            )
        except ValueError as error:
            await self.show_main_menu(f"❌ {error}")
            return

        await self.show_main_menu(f"✅ 已保存 OpenAI 兼容 LLM: {model}")

    async def _replace_body(self, *widgets: Widget) -> None:
        body = self.query_one("#setup-body", Container)
        await body.remove_children()
        await body.mount(*widgets)

    def _set_message(self, message: str) -> None:
        self.query_one("#setup-message", Static).update(message)

    def _selected_news_plugin(self) -> NewsProviderPlugin | None:
        if not self.selected_plugin_id:
            return None
        return discover_news_provider_plugins().providers.get(self.selected_plugin_id)


def _news_plugin_summary(config: AlphaDeskConfig) -> str:
    plugins = discover_news_provider_plugins().providers
    if not plugins:
        return "未安装"
    configured = sum(
        1
        for plugin in plugins.values()
        if _news_plugin_status(plugin, config) == "已配置"
    )
    return f"{configured}/{len(plugins)} 已配置"


def _news_plugin_status(plugin: NewsProviderPlugin, config: AlphaDeskConfig) -> str:
    plugin_config = config.provider_plugins.get(plugin.metadata.provider_id)
    if plugin_config is None or not plugin_config.enabled:
        return "未配置"
    if missing_required_settings(plugin, plugin_config.settings):
        return "待配置"
    return "已配置"


def _plugin_setting_status(setting: NewsProviderSetting, settings: dict[str, str]) -> str:
    return "已配置" if settings.get(setting.key, "").strip() else "未配置"


def _plugin_capability_text(plugin: NewsProviderPlugin) -> str:
    labels = {
        "entity_news": "公司与证券关联新闻",
        "topic_news": "主题新闻",
        "market_news": "市场级新闻",
    }
    return "、".join(labels[capability] for capability in sorted(plugin.metadata.capabilities))


def run_setup_tui(path: Path) -> None:
    SetupApp(path).run()
