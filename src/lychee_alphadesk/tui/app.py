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
from lychee_alphadesk.core.research_db import (
    ResearchReviewRecord,
    list_research_reviews,
    write_discovery_research_run,
)
from lychee_alphadesk.core.research_memo import (
    ResearchMemoResult,
    generate_research_memo,
)
from lychee_alphadesk.core.workbench import (
    RESEARCH_REVIEW_VERDICTS,
    CandidateCheck,
    ResearchReviewResult,
    ResearchVerificationResult,
    WorkbenchCheckResult,
    record_research_review,
    render_research_task_detail,
    research_action_name,
    research_action_result,
    research_action_symbols,
    research_detail_actions,
    research_filing_symbols,
    run_workbench_check,
    verify_research_task,
)

ActionId = Literal[
    "today_discovery",
    "research_workbench",
    "research_reviews",
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
            Option("研究复核历史", id="research_reviews"),
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
        if isinstance(action_id, str) and action_id.startswith("research_review:"):
            await self._run_research_review_action(action_id)
            return
        if action_id == "today_discovery":
            await self._show_today_discovery()
        elif action_id == "research_workbench":
            await self._show_research_workbench()
        elif action_id == "research_reviews":
            await self._show_research_review_history()
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

    async def _show_research_review_history(self) -> None:
        records = await asyncio.to_thread(
            list_research_reviews,
            output_dir=self.output_dir,
            limit=10,
        )
        await self._replace_action_panel(
            Static(_research_review_history_text(records), id="action-status")
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

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
        actions = research_detail_actions(candidate, packet)
        await self._replace_action_panel(
            Static(render_research_task_detail(candidate, packet), id="action-status"),
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
        symbols = research_action_symbols(candidate)
        if action in {"refresh_market", "refresh_news"} and not symbols:
            await self._replace_action_panel(
                Static(
                    "这个任务还没有可刷新的证券代码或代理标的，请先完成入口映射。",
                    id="action-status",
                )
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        if action == "verify_research":
            await self._run_research_verification(candidate)
            return
        if action == "generate_memo":
            await self._run_research_memo(candidate)
            return
        await self._replace_action_panel(
            Static(
                f"正在执行: {research_action_name(action)}，请稍候...",
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
                filing_symbols = research_filing_symbols(candidate, packet)
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
        actions = research_detail_actions(candidate, packet)
        status = research_action_result(action, result.count, result.warnings)
        await self._replace_action_panel(
            Static(
                render_research_task_detail(
                    candidate,
                    packet,
                    action_status=status,
                ),
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

    async def _run_research_verification(self, candidate: CandidateCheck) -> None:
        await self._replace_action_panel(
            Static(
                "正在执行: 下钻核验，请稍候...",
                id="action-status",
            )
        )
        symbols = research_action_symbols(candidate)
        symbol = symbols[0] if symbols else None
        name = None if symbol else candidate.display_name
        try:
            result = await asyncio.to_thread(
                verify_research_task,
                output_dir=self.output_dir,
                symbol=symbol,
                name=name,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._refresh_research_state()
        await self._replace_action_panel(
            Static(_research_verification_text(result), id="action-status"),
            OptionList(
                *[
                    Option(label, id=f"research_review:{verdict}")
                    for verdict, label in _research_review_menu_options()
                ],
                Option("返回研究任务列表", id="research_detail:back_tasks"),
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))

    async def _run_research_memo(self, candidate: CandidateCheck) -> None:
        await self._replace_action_panel(
            Static(
                "正在调用 LLM 生成研究备忘录，请稍候...",
                id="action-status",
            )
        )
        symbols = research_action_symbols(candidate)
        symbol = symbols[0] if symbols else None
        name = None if symbol else candidate.display_name
        try:
            result = await asyncio.to_thread(
                generate_research_memo,
                output_dir=self.output_dir,
                symbol=symbol,
                name=name,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._refresh_research_state()
        await self._replace_action_panel(
            Static(_research_memo_text(result), id="action-status"),
            OptionList(
                Option("返回研究任务列表", id="research_detail:back_tasks"),
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))

    async def _run_research_review_action(self, action_id: str) -> None:
        verdict = action_id.removeprefix("research_review:")
        verdict_label = RESEARCH_REVIEW_VERDICTS.get(verdict)
        if verdict_label is None:
            await self._replace_action_panel(
                Static("未知复核判断。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        selection = self.selected_research_index
        if selection is None or selection >= len(self.research_candidates):
            await self._replace_action_panel(
                Static("研究任务不存在，请重新打开研究工作台。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        candidate = self.research_candidates[selection]
        symbols = research_action_symbols(candidate)
        symbol = symbols[0] if symbols else None
        name = None if symbol else candidate.display_name
        note = f"TUI 快速复核: {verdict_label}"
        await self._replace_action_panel(
            Static(
                f"正在记录复核判断: {verdict_label}，请稍候...",
                id="action-status",
            )
        )
        try:
            result = await asyncio.to_thread(
                record_research_review,
                output_dir=self.output_dir,
                symbol=symbol,
                name=name,
                verdict=verdict,
                note=note,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._refresh_research_state()
        await self._replace_action_panel(
            Static(_research_review_recorded_text(result), id="action-status"),
            OptionList(
                Option("返回研究任务列表", id="research_detail:back_tasks"),
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


def _research_review_history_text(records: list[ResearchReviewRecord]) -> str:
    if not records:
        return "\n".join(
            [
                "研究复核历史",
                "暂无研究复核记录。先在研究结果中运行下钻核验，再记录复核判断。",
                "边界: 研究复核历史不是买卖建议。",
            ]
        )
    lines = ["研究复核历史"]
    for record in records:
        lines.extend(
            [
                (
                    f"- {record.display_name} ({record.symbol or '-'}) "
                    f"[{record.market}] {record.verdict_label}"
                ),
                f"  时间: {record.created_at}",
                f"  备注: {record.note}",
                (
                    f"  证据: 支持 {record.support_count} | "
                    f"风险 {record.risk_count} | 待补 {record.missing_count}"
                ),
                f"  记录: {record.review_path}",
                f"  下钻核验: {record.verification_path}",
            ]
        )
    lines.append("边界: 研究复核历史不是买卖建议。")
    return "\n".join(lines)


def _display_workbench_status(status: str) -> str:
    return {
        "ready": "可执行研究",
        "blocked": "存在阻塞",
    }.get(status, status)


def _research_verification_text(result: ResearchVerificationResult) -> str:
    lines = [
        "下钻核验结果",
        f"记录: {result.artifact_path}",
        f"任务: {result.candidate.display_name} [{result.candidate.market}]",
        f"一致性结论: {result.status_label}",
        "",
        "核验项",
    ]
    for check in result.checks:
        lines.append(
            f"- {check.name}: {_display_verification_status(check.status)} - "
            f"{check.detail}"
        )
    lines.extend(
        [
            "",
            "证据板",
            *_evidence_board_lines("支持证据", result.evidence_board["support"]),
            *_evidence_board_lines("风险/反向待查", result.evidence_board["risk"]),
            *_evidence_board_lines("待补证据", result.evidence_board["missing"]),
            "",
            result.conclusion,
            "下一步",
        ]
    )
    lines.extend(f"- {action}" for action in result.next_actions)
    lines.append("边界: 下钻核验不是买卖建议。")
    return "\n".join(lines)


def _research_review_recorded_text(result: ResearchReviewResult) -> str:
    counts = result.evidence_counts
    return "\n".join(
        [
            "研究复核已记录",
            f"记录: {result.artifact_path}",
            f"研究库: {result.db_path}",
            f"任务: {result.verification.candidate.display_name} "
            f"[{result.verification.candidate.market}]",
            f"复核判断: {result.verdict_label}",
            f"备注: {result.note}",
            (
                "证据数量: "
                f"支持 {counts['support']} | "
                f"风险/反向待查 {counts['risk']} | "
                f"待补 {counts['missing']}"
            ),
            "边界: 研究复核不是买卖建议。",
        ]
    )


def _research_memo_text(result: ResearchMemoResult) -> str:
    memo = result.memo
    return "\n".join(
        [
            "研究备忘录",
            f"记录: {result.artifact_path}",
            f"任务: {result.candidate.display_name} [{result.candidate.market}]",
            f"置信度: {memo.confidence}",
            "",
            "摘要",
            memo.summary,
            "",
            "证据读数",
            memo.evidence_reading,
            "",
            "支持证据",
            *_text_list_lines(memo.support_points),
            "",
            "反方审查",
            *_text_list_lines(memo.skeptic_review),
            "",
            "待补证据",
            *_text_list_lines(memo.missing_evidence),
            "",
            "下一步研究动作",
            *_text_list_lines(memo.next_research_steps),
            "边界: 研究备忘录不是买卖建议。",
        ]
    )


def _research_review_menu_options() -> list[tuple[str, str]]:
    return [
        (verdict, f"记录: {label}")
        for verdict, label in RESEARCH_REVIEW_VERDICTS.items()
    ]


def _evidence_board_lines(title: str, rows: list[str]) -> list[str]:
    lines = [title]
    if not rows:
        lines.append("- 无")
    else:
        lines.extend(f"- {row}" for row in rows)
    return lines


def _text_list_lines(rows: list[str]) -> list[str]:
    if not rows:
        return ["- 无"]
    return [f"- {row}" for row in rows]


def _display_verification_status(status: str) -> str:
    return {
        "pass": "通过",
        "warn": "待核验",
        "fail": "阻塞",
        "na": "不适用",
    }.get(status, status)
