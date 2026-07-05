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
        self.selected_research_index: int | None = None

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
        if isinstance(action_id, str) and action_id.startswith("research_detail:"):
            await self._run_research_detail_action(action_id)
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
        self.selected_research_index = index
        packet = self.research_packets[index] if index < len(self.research_packets) else None
        actions = _research_detail_actions(candidate, packet)
        await self._replace_action_panel(
            Static(_research_task_detail(candidate, packet), id="action-status"),
            OptionList(
                *[
                    Option(label, id=f"research_detail:{action_id}")
                    for action_id, label in actions
                ],
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))

    async def _run_research_detail_action(self, action_id: str) -> None:
        action = action_id.removeprefix("research_detail:")
        if action == "back_tasks":
            await self._show_research_workbench()
            return
        selection = self.selected_research_index
        if selection is None or selection >= len(self.research_candidates):
            await self._replace_action_panel(
                Static("研究任务不存在，请重新打开研究工作台。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        candidate = self.research_candidates[selection]
        packet = (
            self.research_packets[selection]
            if selection < len(self.research_packets)
            else None
        )
        symbols = _research_action_symbols(candidate)
        if action in {"refresh_market", "refresh_news"} and not symbols:
            await self._replace_action_panel(
                Static(
                    "这个任务还没有可刷新的证券代码或代理标的，请先完成入口映射。",
                    id="action-status",
                )
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(
                f"正在执行: {_research_action_name(action)}，请稍候...",
                id="action-status",
            )
        )
        try:
            if action == "refresh_market":
                result = await asyncio.to_thread(
                    pull_market_prices,
                    symbols=symbols,
                    output_dir=self.output_dir,
                    provider_id="auto",
                    force=True,
                )
            elif action == "refresh_news":
                result = await asyncio.to_thread(
                    pull_news_events,
                    symbols=symbols,
                    output_dir=self.output_dir,
                    provider_id="auto",
                    force=True,
                )
            elif action == "refresh_filings":
                filing_symbols = _filing_symbols(candidate, packet)
                if not filing_symbols:
                    await self._replace_action_panel(
                        Static(
                            "这个任务当前不适合自动拉取 SEC 公告。只有美股股票任务会启用该动作。",
                            id="action-status",
                        )
                    )
                    self.set_focus(self.query_one("#action-menu", OptionList))
                    return
                result = await asyncio.to_thread(
                    pull_sec_filings,
                    symbols=filing_symbols,
                    output_dir=self.output_dir,
                )
            else:
                await self._replace_action_panel(
                    Static("未知研究动作。", id="action-status")
                )
                self.set_focus(self.query_one("#action-menu", OptionList))
                return
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return

        await self._refresh_research_state()
        candidate = self.research_candidates[selection]
        packet = (
            self.research_packets[selection]
            if selection < len(self.research_packets)
            else None
        )
        actions = _research_detail_actions(candidate, packet)
        status = _research_action_result(action, result.count, result.warnings)
        await self._replace_action_panel(
            Static(
                _research_task_detail(candidate, packet, action_status=status),
                id="action-status",
            ),
            OptionList(
                *[
                    Option(label, id=f"research_detail:{detail_action_id}")
                    for detail_action_id, label in actions
                ],
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))

    async def _refresh_research_state(self) -> None:
        result = await asyncio.to_thread(run_workbench_check, output_dir=self.output_dir)
        self.research_candidates = list(result.candidates)
        deepen_result = getattr(result, "deepen_result", None)
        self.research_packets = list(getattr(deepen_result, "packets", []))

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
    *,
    action_status: str = "",
) -> str:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    price = _dict_value(local_data.get("price"))
    evidence = _dict_list(packet_payload.get("evidence"))
    related_news = _dict_list(local_data.get("related_news"))
    filings = _dict_list(local_data.get("filings"))
    data_gaps = _text_list(packet_payload.get("data_gaps")) or candidate.data_gaps
    commands = _research_action_commands(candidate, packet)
    lines = [
        "研究结果",
        f"任务: {candidate.display_name} [{candidate.market}]",
        f"入口: {candidate.observation_entry}",
        f"优先级: {candidate.priority}",
        f"证据状态: {candidate.evidence_status}",
        "信号读数: "
        + _signal_reading(candidate, price, evidence, related_news, filings, data_gaps),
        f"研究问题: {candidate.beginner_question}",
        "",
        f"当前研究结论: {candidate.what_to_check}",
        _price_line(price),
        "",
        "证据矩阵",
        *_evidence_matrix_lines(
            candidate=candidate,
            packet=packet,
            price=price,
            evidence=evidence,
            related_news=related_news,
            filings=filings,
            data_gaps=data_gaps,
        ),
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
    lines.extend(["", "可执行动作"])
    if action_status:
        lines.append(action_status)
    lines.extend(f"- {command}" for command in commands)
    lines.append("")
    lines.append("边界: 这是研究工作台快照，不是买卖建议。")
    return "\n".join(lines)


def _signal_reading(
    candidate: CandidateCheck,
    price: dict[str, object],
    evidence: list[dict[str, object]],
    related_news: list[dict[str, object]],
    filings: list[dict[str, object]],
    data_gaps: list[str],
) -> str:
    if data_gaps:
        return f"阻塞: 还有 {_gap_summary(data_gaps)}。先补数据，再判断线索。"
    if not price and not candidate.proxy_symbols:
        return "证据不足: 缺少可观察行情，暂时只能保留在线索池。"
    if not evidence and not related_news and not filings:
        return "只具备行情: 还没有新闻、公告或财报佐证。"
    if price and (evidence or related_news):
        return "初步可研究: 已有行情和消息证据，下一步检查它们是否同向。"
    if candidate.proxy_symbols:
        return "代理观察: 先确认代理标的是否真的覆盖主题，再下钻。"
    return "待增强证据: 还需要补齐更多可核验材料。"


def _evidence_matrix_lines(
    *,
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    price: dict[str, object],
    evidence: list[dict[str, object]],
    related_news: list[dict[str, object]],
    filings: list[dict[str, object]],
    data_gaps: list[str],
) -> list[str]:
    asset_type = _asset_type(packet)
    filing_status = "不适用"
    if candidate.market.upper() == "US" and asset_type == "stock":
        filing_status = f"{len(filings)} 条" if filings else "缺失"
    elif filings:
        filing_status = f"{len(filings)} 条"
    proxy_status = _display_values(candidate.proxy_symbols)
    return [
        f"- 行情: {'已采集' if price else '缺失'}",
        f"- Discovery 证据: {len(evidence)} 条",
        f"- 相关新闻: {len(related_news)} 条",
        f"- 公告/财报: {filing_status}",
        f"- 代理标的: {proxy_status}",
        f"- 数据缺口: {_gap_summary(data_gaps)}",
    ]


def _research_detail_actions(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[tuple[str, str]]:
    actions = [
        ("refresh_market", "刷新行情"),
        ("refresh_news", "刷新新闻"),
    ]
    if _filing_symbols(candidate, packet):
        actions.append(("refresh_filings", "刷新美股公告/财报"))
    actions.append(("back_tasks", "返回研究任务列表"))
    return actions


def _research_action_commands(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    symbols = _research_action_symbols(candidate)
    commands: list[str] = []
    if symbols:
        symbol_text = ",".join(symbols)
        commands.append(
            f"刷新行情: lychee data pull market --symbols {symbol_text} "
            "--provider auto --force"
        )
        commands.append(
            f"刷新新闻: lychee data pull news --symbols {symbol_text} "
            "--provider auto --force"
        )
    else:
        commands.append("刷新行情/新闻: 需要先完成可观察入口映射。")
    filing_symbols = _filing_symbols(candidate, packet)
    if filing_symbols:
        commands.append(
            f"刷新美股公告/财报: lychee data pull filings --symbols "
            f"{','.join(filing_symbols)}"
        )
    return commands


def _research_action_symbols(candidate: CandidateCheck) -> list[str]:
    if candidate.symbol:
        return [candidate.symbol]
    return candidate.proxy_symbols


def _filing_symbols(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    if candidate.market.upper() != "US" or not candidate.symbol:
        return []
    if _asset_type(packet) != "stock":
        return []
    return [candidate.symbol]


def _asset_type(packet: ResearchPacket | None) -> str:
    if packet is None:
        return ""
    candidate = _dict_value(packet.packet.get("candidate"))
    return _string_value(candidate.get("asset_type"), default="").lower()


def _research_action_name(action: str) -> str:
    return {
        "refresh_market": "刷新行情",
        "refresh_news": "刷新新闻",
        "refresh_filings": "刷新美股公告/财报",
    }.get(action, action)


def _research_action_result(
    action: str,
    count: int,
    warnings: list[str],
) -> str:
    lines = [
        f"已执行: {_research_action_name(action)}",
        f"返回行数: {count}",
    ]
    if warnings:
        lines.append("警告: " + "；".join(warnings[:3]))
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


def _display_values(values: list[str]) -> str:
    if not values:
        return "-"
    return ", ".join(values)


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
