import asyncio
from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from lychee_alphadesk.core.data_engine import write_snapshot_json
from lychee_alphadesk.core.discovery import (
    DiscoveryLLMRequiredError,
    build_today_discovery_report,
    discovery_report_summary,
    write_discovery_report,
)
from lychee_alphadesk.core.live_data import (
    build_cached_data_snapshot,
    parse_symbols,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    run_cached_data_health,
)
from lychee_alphadesk.core.llm import LLMProviderError
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR

ActionId = Literal[
    "today_discovery",
    "pull_market",
    "pull_news",
    "pull_filings",
    "data_health",
    "write_snapshot",
    "refresh",
    "setup_help",
    "quit",
]


class AlphaDeskApp(App[None]):
    TITLE = "Lychee AlphaDesk"
    SUB_TITLE = "本地投资研究工作台"
    ENABLE_COMMAND_PALETTE = False
    ALLOW_SELECT = False
    BINDINGS = [
        Binding("escape", "back", "返回", show=True),
        Binding("q", "quit", "退出", show=True),
        Binding("ctrl+c", "quit", "退出", show=False),
    ]

    def __init__(self, output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
        super().__init__()
        self.output_dir = output_dir
        self.pending_action: ActionId | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._dashboard_summary(), id="dashboard-summary")
        yield Static("操作", id="action-title")
        yield OptionList(
            Option("今日市场发现", id="today_discovery"),
            Option("手动查看行情", id="pull_market"),
            Option("手动查看新闻", id="pull_news"),
            Option("手动查看美股公告", id="pull_filings"),
            Option("检查数据健康", id="data_health"),
            Option("写入实时快照", id="write_snapshot"),
            Option("刷新面板", id="refresh"),
            Option("配置指引", id="setup_help"),
            Option("退出", id="quit"),
            id="action-menu",
            markup=False,
        )
        yield Container(
            Static(
                "使用 ↑/↓/Tab 移动，Enter 选择，Esc 返回。",
                id="action-status",
            ),
            id="action-panel",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def action_back(self) -> None:
        if self.pending_action is None:
            self.exit()
            return
        self.pending_action = None
        await self._replace_action_panel(
            Static(
                "使用 ↑/↓/Tab 移动，Enter 选择，Esc 返回。",
                id="action-status",
            )
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        event.stop()
        action_id = event.option.id
        if action_id == "today_discovery":
            await self._show_today_discovery()
        elif action_id == "pull_market":
            await self._show_symbol_prompt(
                "pull_market",
                "输入行情证券代码，例如 AAPL,TSLA,0700.HK",
            )
        elif action_id == "pull_news":
            await self._show_symbol_prompt("pull_news", "输入新闻证券代码，例如 AAPL,TSLA")
        elif action_id == "pull_filings":
            await self._show_symbol_prompt(
                "pull_filings",
                "输入美股公告证券代码，例如 AAPL,TSLA",
            )
        elif action_id == "data_health":
            await self._show_data_health()
        elif action_id == "write_snapshot":
            await self._write_live_snapshot()
        elif action_id == "refresh":
            self._refresh_dashboard()
            self._set_status("面板已从本地缓存刷新。")
        elif action_id == "setup_help":
            self._set_status(
                "请在终端运行 `lychee setup` 配置数据源和 LLM。"
            )
        elif action_id == "quit":
            self.exit()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        symbols = parse_symbols(event.value)
        if not symbols:
            self._set_status("未输入证券代码。")
            return
        await self._run_symbol_action(symbols)

    def _dashboard_summary(self) -> str:
        snapshot = build_cached_data_snapshot(self.output_dir)
        lines = [
            "本地数据缓存",
            f"行情: {snapshot.counts['prices']}",
            f"新闻: {snapshot.counts['news_events']}",
            f"公告: {snapshot.counts['filings']}",
        ]
        if snapshot.provider_names:
            lines.append(f"数据源: {', '.join(snapshot.provider_names)}")
        if snapshot.prices:
            lines.append("")
            lines.append("最新价格")
            for price in snapshot.prices[:8]:
                lines.append(
                    f"{price.symbol:<10} {price.close:.2f} {price.currency} "
                    f"{price.date}"
                )
        else:
            lines.append("")
            lines.append("暂无实时缓存。请先运行:")
            lines.append("  lychee discover today")
        return "\n".join(lines)

    async def _show_symbol_prompt(self, action: ActionId, placeholder: str) -> None:
        self.pending_action = action
        await self._replace_action_panel(
            Static(
                "输入英文逗号分隔的证券代码，然后按 Enter。",
                id="action-status",
            ),
            Input(placeholder=placeholder, id="symbols-input"),
        )
        self.set_focus(self.query_one("#symbols-input", Input))

    async def _show_today_discovery(self) -> None:
        await self._replace_action_panel(
            Static(
                "正在调用 LLM 分析美股、港股和 A 股市场，请稍候...",
                id="action-status",
            )
        )
        try:
            report = await asyncio.to_thread(
                build_today_discovery_report,
                output_dir=self.output_dir,
            )
        except (DiscoveryLLMRequiredError, LLMProviderError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        output_path = write_discovery_report(report, self.output_dir)
        await self._replace_action_panel(
            Static(
                discovery_report_summary(report, output_path),
                id="action-status",
            )
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _run_symbol_action(self, symbols: list[str]) -> None:
        action = self.pending_action
        if action is None:
            self._set_status("当前没有正在执行的操作。")
            return

        try:
            if action == "pull_market":
                result = pull_market_prices(symbols=symbols, output_dir=self.output_dir)
                label = "行情"
            elif action == "pull_news":
                result = pull_news_events(symbols=symbols, output_dir=self.output_dir)
                label = "新闻"
            elif action == "pull_filings":
                result = pull_sec_filings(symbols=symbols, output_dir=self.output_dir)
                label = "公告"
            else:
                self._set_status("这个操作不接收证券代码。")
                return
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return

        lines = [
            f"已拉取{label}: {result.count}",
            f"数据源: {result.provider}",
            f"缓存: {result.output_path}",
        ]
        for warning in result.warnings:
            lines.append(f"警告: {warning}")
        self.pending_action = None
        self._refresh_dashboard()
        await self._replace_action_panel(Static("\n".join(lines), id="action-status"))
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _show_data_health(self) -> None:
        checks = run_cached_data_health(self.output_dir)
        lines = [
            "数据健康",
            *[
                f"{_display_status(check.status):<4} {check.name} - {check.message}"
                for check in checks
            ],
        ]
        await self._replace_action_panel(Static("\n".join(lines), id="action-status"))
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _write_live_snapshot(self) -> None:
        snapshot = build_cached_data_snapshot(self.output_dir)
        output_path = write_snapshot_json(snapshot, self.output_dir)
        self._refresh_dashboard()
        await self._replace_action_panel(
            Static(f"实时快照已写入: {output_path}", id="action-status")
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    def _refresh_dashboard(self) -> None:
        self.query_one("#dashboard-summary", Static).update(self._dashboard_summary())

    async def _replace_action_panel(self, *widgets: Widget) -> None:
        panel = self.query_one("#action-panel", Container)
        await panel.remove_children()
        await panel.mount(*widgets)

    def _set_status(self, message: str) -> None:
        status = self.query_one("#action-status", Static)
        status.update(message)


def run_tui() -> None:
    AlphaDeskApp().run()


def _display_status(status: str) -> str:
    return {"pass": "通过", "warning": "警告", "error": "错误"}.get(status, status)
