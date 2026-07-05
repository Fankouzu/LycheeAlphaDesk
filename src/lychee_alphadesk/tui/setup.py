from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from lychee_alphadesk.core.config import (
    ProviderSetupInfo,
    ensure_config_file,
    load_config,
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

SetupView = Literal[
    "main",
    "providers",
    "provider_detail",
    "llm_base_url",
    "llm_api_key",
    "llm_model_select",
    "llm_manual_model",
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
            return

        if event.option_list.id == "provider-menu":
            provider = load_config(self.config_path).providers[option_id]
            await self.show_provider_detail(provider)
            return

        if event.option_list.id == "model-menu":
            await self._save_llm_model(option_id)

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


def run_setup_tui(path: Path) -> None:
    SetupApp(path).run()
