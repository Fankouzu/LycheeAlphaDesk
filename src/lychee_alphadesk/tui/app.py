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
    DiscoveryDataRequiredError,
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
from lychee_alphadesk.core.research import ResearchPacket
from lychee_alphadesk.core.research_db import write_discovery_research_run
from lychee_alphadesk.core.workbench import (
    CandidateCheck,
    WorkbenchCheckResult,
    run_workbench_check,
)

ActionId = Literal[
    "today_discovery",
    "research_workbench",
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
        self.research_candidates: list[CandidateCheck] = []
        self.research_packets: list[ResearchPacket] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._dashboard_summary(), id="dashboard-summary")
        yield Static("操作", id="action-title")
        yield OptionList(
            Option("今日市场发现", id="today_discovery"),
            Option("研究工作台", id="research_workbench"),
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
        if isinstance(action_id, str) and action_id.startswith("research_task:"):
            await self._show_research_task_detail(action_id)
            return
        if action_id == "today_discovery":
            await self._show_today_discovery()
        elif action_id == "research_workbench":
            await self._show_research_workbench()
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

    async def _show_research_workbench(self) -> None:
        await self._replace_action_panel(
            Static(
                "正在运行工作台自检，并整理研究任务，请稍候...",
                id="action-status",
            )
        )
        try:
            result = await asyncio.to_thread(
                run_workbench_check,
                output_dir=self.output_dir,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        self.research_candidates = list(result.candidates)
        deepen_result = getattr(result, "deepen_result", None)
        self.research_packets = list(getattr(deepen_result, "packets", []))
        if not self.research_candidates:
            await self._replace_action_panel(
                Static(
                    "AlphaDesk 研究工作台\n暂无研究任务。请先运行“今日市场发现”。",
                    id="action-status",
                )
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        self.pending_action = "research_workbench"
        await self._replace_action_panel(
            Static(
                _research_workbench_intro(result),
                id="action-status",
            ),
            OptionList(
                *[
                    Option(_research_task_label(candidate), id=f"research_task:{index}")
                    for index, candidate in enumerate(self.research_candidates)
                ],
                id="research-task-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-task-menu", OptionList))

    async def _show_research_task_detail(self, task_id: str) -> None:
        raw_index = task_id.removeprefix("research_task:")
        try:
            index = int(raw_index)
            candidate = self.research_candidates[index]
        except (ValueError, IndexError):
            await self._replace_action_panel(
                Static("研究任务不存在，请重新打开研究工作台。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        packet = self.research_packets[index] if index < len(self.research_packets) else None
        await self._replace_action_panel(
            Static(_research_task_detail(candidate, packet), id="action-status")
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

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
                "正在准备市场级新闻，并调用 LLM 分析美股、港股和 A 股市场，请稍候...",
                id="action-status",
            )
        )
        try:
            report = await asyncio.to_thread(
                build_today_discovery_report,
                output_dir=self.output_dir,
            )
        except (
            DiscoveryDataRequiredError,
            DiscoveryLLMRequiredError,
            LLMProviderError,
        ) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        output_path = write_discovery_report(report, self.output_dir)
        db_path = write_discovery_research_run(report, self.output_dir, output_path)
        await self._replace_action_panel(
            Static(
                "\n".join(
                    [
                        discovery_report_summary(
                            report,
                            output_path,
                            output_dir=self.output_dir,
                        ),
                        "",
                        f"研究库已更新: {db_path}",
                    ]
                ),
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


def _research_workbench_intro(result: WorkbenchCheckResult) -> str:
    return "\n".join(
        [
            "AlphaDesk 研究工作台",
            "选择一个研究任务，按 Enter 开始研究。Esc 返回主菜单。",
            (
                f"状态: {_display_workbench_status(result.status)} | "
                f"可执行 {result.ready_count} | 阻塞 {result.blocked_count} | "
                f"总任务 {len(result.candidates)}"
            ),
        ]
    )


def _research_task_label(candidate: CandidateCheck) -> str:
    return (
        f"{candidate.display_name} [{candidate.market}] | "
        f"入口: {candidate.observation_entry} | "
        f"优先级: {candidate.priority} | "
        f"{candidate.evidence_status}"
    )


def _research_task_detail(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> str:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    price = _dict_value(local_data.get("price"))
    evidence = _dict_list(packet_payload.get("evidence"))
    related_news = _dict_list(local_data.get("related_news"))
    filings = _dict_list(local_data.get("filings"))
    data_gaps = _text_list(packet_payload.get("data_gaps")) or candidate.data_gaps
    lines = [
        "研究结果",
        f"任务: {candidate.display_name} [{candidate.market}]",
        f"入口: {candidate.observation_entry}",
        f"优先级: {candidate.priority}",
        f"证据状态: {candidate.evidence_status}",
        f"研究问题: {candidate.beginner_question}",
        "",
        f"当前研究结论: {candidate.what_to_check}",
        _price_line(price),
        "",
        "已采集证据",
        *_headline_lines(evidence, empty="暂无 discovery 证据。"),
        "",
        "相关新闻",
        *_headline_lines(related_news, empty="暂无匹配新闻。"),
        "",
        "公告/财报线索",
        *_filing_lines(filings),
        "",
        f"数据缺口: {_gap_summary(data_gaps)}",
        f"下一步动作: {candidate.next_step}",
    ]
    if candidate.proxy_symbols:
        lines.append("代理核验: 核对成分、费用、流动性和是否可交易。")
    lines.append("")
    lines.append("边界: 这是研究结果快照，不是买卖建议。")
    return "\n".join(lines)


def _display_workbench_status(status: str) -> str:
    return {
        "ready": "可执行研究",
        "blocked": "存在阻塞",
    }.get(status, status)


def _price_line(price: dict[str, object]) -> str:
    if not price:
        return "行情: 暂无本地行情。"
    symbol = _string_value(price.get("symbol"), default="-")
    close = _number_value(price.get("close"))
    currency = _string_value(price.get("currency"), default="")
    date = _string_value(price.get("date"), default="")
    volume = price.get("volume")
    parts = [f"行情: {symbol} {close} {currency}".strip()]
    if date:
        parts.append(date)
    if volume is not None:
        parts.append(f"成交量 {volume}")
    return " | ".join(parts)


def _headline_lines(rows: list[dict[str, object]], *, empty: str) -> list[str]:
    if not rows:
        return [f"- {empty}"]
    lines: list[str] = []
    for row in rows[:3]:
        headline = _string_value(row.get("headline"), default="未命名证据")
        source_url = _string_value(row.get("source_url"), default="")
        if source_url:
            lines.append(f"- {headline} ({source_url})")
        else:
            lines.append(f"- {headline}")
    return lines


def _filing_lines(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return ["- 暂无匹配公告或财报线索。"]
    lines: list[str] = []
    for row in rows[:3]:
        form = _string_value(row.get("form"), default="公告")
        date = _string_value(row.get("date"), default="")
        summary = _string_value(row.get("summary"), default="")
        line = f"- {form}"
        if date:
            line += f" {date}"
        if summary:
            line += f": {summary}"
        lines.append(line)
    return lines


def _gap_summary(data_gaps: list[str]) -> str:
    if not data_gaps:
        return "无"
    return "；".join(gap.rstrip("。；;,.， ") for gap in data_gaps if gap.strip())


def _dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _string_value(value: object, *, default: str) -> str:
    return value if isinstance(value, str) else default


def _number_value(value: object) -> object:
    if isinstance(value, float):
        return f"{value:.2f}"
    return value
