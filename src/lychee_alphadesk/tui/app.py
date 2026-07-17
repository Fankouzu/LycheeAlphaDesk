import asyncio
import json
import shlex
from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from lychee_alphadesk.core.action_queue import (
    ActionQueueExecution,
    ActionQueueItem,
    build_action_queue,
    execute_action_queue_item,
)
from lychee_alphadesk.core.data_engine import write_snapshot_json
from lychee_alphadesk.core.discovery import (
    DiscoveryDataRequiredError,
    DiscoveryLLMRequiredError,
    build_today_discovery_report,
    discovery_report_summary,
    write_discovery_report,
)
from lychee_alphadesk.core.forecast import (
    ForecastProviderError,
    generate_timesfm_forecasts,
)
from lychee_alphadesk.core.live_data import (
    FinancialSnapshotGuide,
    FundMetadataGuide,
    build_cached_data_snapshot,
    financial_snapshot_guide_symbol,
    parse_symbols,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    pull_sec_financials,
    pull_tushare_financials,
    run_cached_data_health,
    write_financial_snapshot_cache_from_file,
    write_financial_snapshot_guide,
    write_fund_metadata_cache_from_file,
    write_fund_metadata_guide,
    write_manual_filing_summary,
    write_manual_news_event,
)
from lychee_alphadesk.core.llm import LLMProviderError
from lychee_alphadesk.core.opportunity_radar import (
    OpportunityRadarReport,
    build_opportunity_radar,
)
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR, DEMO_ROOT
from lychee_alphadesk.core.portfolio import check_portfolio, write_portfolio_check_artifact
from lychee_alphadesk.core.research import ResearchPacket
from lychee_alphadesk.core.research_db import (
    ResearchEvidenceReviewRecord,
    ResearchMemoRecord,
    ResearchReviewRecord,
    list_research_evidence_reviews,
    list_research_memos,
    list_research_reviews,
    write_discovery_research_run,
)
from lychee_alphadesk.core.research_memo import (
    ResearchMemoResult,
    generate_research_memo,
)
from lychee_alphadesk.core.research_requests import (
    ProviderBacklogItem,
    ResearchDataRequest,
    ResearchDataRequestFulfillment,
    acknowledge_manual_research_data_request,
    fulfill_research_data_request,
    list_provider_backlog_items,
    list_research_data_requests,
    research_data_request_needs_manual_source,
)
from lychee_alphadesk.core.workbench import (
    RESEARCH_EVIDENCE_REVIEW_VERDICTS,
    RESEARCH_REVIEW_VERDICTS,
    CandidateCheck,
    PendingEvidenceReviewItem,
    ResearchEvidenceReviewResult,
    ResearchReviewResult,
    ResearchVerificationResult,
    WorkbenchCheckResult,
    list_pending_evidence_reviews,
    record_research_evidence_review,
    record_research_review,
    render_research_task_detail,
    research_action_name,
    research_action_result,
    research_action_symbols,
    research_detail_actions,
    research_evidence_change_detail_groups,
    research_filing_symbols,
    run_workbench_check,
    select_research_candidate_index,
    topic_news_query,
    verify_research_task,
)

ActionId = Literal[
    "today_discovery",
    "ipo_events",
    "opportunity_radar",
    "research_workbench",
    "next_actions",
    "pending_evidence",
    "research_reviews",
    "research_evidence_reviews",
    "research_memos",
    "research_data_requests",
    "provider_backlog",
    "pull_market",
    "pull_news",
    "pull_filings",
    "forecast",
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
        self.current_research_verification: ResearchVerificationResult | None = None
        self.pending_evidence_items: list[PendingEvidenceReviewItem] = []
        self.pending_evidence_review_items: list[str] = []
        self.last_pending_evidence_review: ResearchEvidenceReviewResult | None = None
        self.current_fund_metadata_guide_path: Path | None = None
        self.current_financials_guide_path: Path | None = None
        self.research_data_requests: list[ResearchDataRequest] = []
        self.action_queue_items: list[ActionQueueItem] = []
        self.manual_news_action: ActionQueueItem | None = None
        self.manual_filing_action: ActionQueueItem | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._dashboard_summary(), id="dashboard-summary")
        yield Static("操作", id="action-title")
        yield OptionList(
            Option("今日市场发现", id="today_discovery"),
            Option("研究工作台", id="research_workbench"),
            Option("机会雷达", id="opportunity_radar"),
            Option("下一步行动队列", id="next_actions"),
            Option("待判定证据队列", id="pending_evidence"),
            Option("研究复核历史", id="research_reviews"),
            Option("证据复核历史", id="research_evidence_reviews"),
            Option("研究备忘录历史", id="research_memos"),
            Option("研究数据请求", id="research_data_requests"),
            Option("数据源缺口队列", id="provider_backlog"),
            Option("IPO/打新资料", id="ipo_events"),
            Option("检查模拟组合", id="portfolio_check"),
            Option("手动查看行情", id="pull_market"),
            Option("手动查看新闻", id="pull_news"),
            Option("手动查看公司公告", id="pull_filings"),
            Option("运行 TimesFM 预测", id="forecast"),
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
        if self.manual_news_action is not None or self.manual_filing_action is not None:
            self.manual_news_action = None
            self.manual_filing_action = None
            await self._show_next_actions()
            return
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
        if isinstance(action_id, str) and action_id.startswith("research_evidence_review:"):
            await self._run_research_evidence_review_action(action_id)
            return
        if isinstance(action_id, str) and action_id.startswith("pending_evidence_item:"):
            await self._show_pending_evidence_detail(action_id)
            return
        if isinstance(action_id, str) and action_id.startswith("pending_evidence_review:"):
            await self._run_pending_evidence_review_action(action_id)
            return
        if isinstance(action_id, str) and action_id.startswith("research_data_request:"):
            await self._run_research_data_request_action(action_id)
            return
        if isinstance(action_id, str) and action_id.startswith("next_action_item:"):
            await self._run_next_action_item(action_id)
            return
        if action_id == "discovery_followup_workbench":
            await self._show_research_workbench()
            return
        if action_id == "discovery_followup_next_actions":
            await self._show_next_actions()
            return
        if action_id == "discovery_followup_data_requests":
            await self._show_research_data_requests()
            return
        if action_id == "manual_news_save":
            await self._save_manual_news_entry()
            return
        if action_id == "manual_news_cancel":
            self.manual_news_action = None
            await self._show_next_actions()
            return
        if isinstance(action_id, str) and action_id.startswith("manual_news_verify:"):
            await self._verify_manual_news_source(action_id)
            return
        if action_id == "manual_filing_save":
            await self._save_manual_filing_entry()
            return
        if action_id == "manual_filing_cancel":
            self.manual_filing_action = None
            await self._show_next_actions()
            return
        if isinstance(action_id, str) and action_id.startswith("manual_filing_verify:"):
            await self._verify_manual_filing_source(action_id)
            return
        if action_id == "pending_evidence_verify_last":
            await self._run_pending_evidence_verification()
            return
        if action_id == "today_discovery":
            await self._show_today_discovery()
        elif action_id == "ipo_events":
            await self._show_ipo_events()
        elif action_id == "opportunity_radar":
            await self._show_opportunity_radar()
        elif action_id == "research_workbench":
            await self._show_research_workbench()
        elif action_id == "next_actions":
            await self._show_next_actions()
        elif action_id == "pending_evidence":
            await self._show_pending_evidence_queue()
        elif action_id == "research_reviews":
            await self._show_research_review_history()
        elif action_id == "research_evidence_reviews":
            await self._show_research_evidence_review_history()
        elif action_id == "research_memos":
            await self._show_research_memo_history()
        elif action_id == "research_data_requests":
            await self._show_research_data_requests()
        elif action_id == "provider_backlog":
            await self._show_provider_backlog()
        elif action_id == "portfolio_check":
            await self._show_demo_portfolio_check()
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
                "输入美股、港股或 A 股公司代码，例如 AAPL,0700.HK,000001.SZ",
            )
        elif action_id == "forecast":
            await self._show_symbol_prompt(
                "forecast",
                "输入已有历史行情的证券代码，例如 QQQ,TSLA",
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
        if event.input.id in {
            "manual-news-headline",
            "manual-news-summary",
            "manual-news-source-url",
            "manual-filing-company",
            "manual-filing-form",
            "manual-filing-date",
            "manual-filing-summary",
            "manual-filing-source-url",
        }:
            self._set_status("请使用 Tab 移动到“保存已核验来源”，再按 Enter。")
            return
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
            f"预测: {snapshot.counts['forecasts']}",
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

    async def _show_opportunity_radar(self) -> None:
        await self._replace_action_panel(
            Static("正在扫描本地行情和新闻缓存，请稍候...", id="action-status")
        )
        try:
            report = await asyncio.to_thread(
                build_opportunity_radar,
                output_dir=self.output_dir,
                limit=8,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(_opportunity_radar_text(report), id="action-status")
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _show_next_actions(self) -> None:
        await self._replace_action_panel(
            Static("正在整理下一步行动队列，请稍候...", id="action-status")
        )
        try:
            items = await asyncio.to_thread(
                build_action_queue,
                output_dir=self.output_dir,
                limit=12,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        self.action_queue_items = items
        if not items:
            await self._replace_action_panel(
                Static(_action_queue_text(items), id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(_action_queue_text(items), id="action-status"),
            OptionList(
                *[
                    Option(
                        f"执行: {item.title}",
                        id=f"next_action_item:{index}",
                    )
                    for index, item in enumerate(items)
                ],
                id="next-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#next-action-menu", OptionList))

    async def _run_next_action_item(self, action_id: str) -> None:
        raw_index = action_id.removeprefix("next_action_item:")
        try:
            index = int(raw_index)
            item = self.action_queue_items[index]
        except (ValueError, IndexError):
            await self._replace_action_panel(
                Static("下一步行动不存在，请刷新行动队列。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        if _manual_news_source_symbol(item.command) is not None:
            await self._show_manual_news_entry(item)
            return
        if _manual_filing_source_details(item.command) is not None:
            await self._show_manual_filing_entry(item)
            return
        await self._replace_action_panel(
            Static(f"正在执行下一步行动: {item.title}，请稍候...", id="action-status")
        )
        try:
            result = await asyncio.to_thread(
                execute_action_queue_item,
                self.output_dir,
                action_index=index + 1,
                limit=max(len(self.action_queue_items), 12),
                force=False,
                queue_builder=lambda *args, **kwargs: self.action_queue_items,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(_action_queue_execution_text(result), id="action-status"),
            OptionList(
                Option("刷新下一步行动队列", id="next_actions"),
                Option("返回主菜单", id="refresh"),
                id="next-action-followup-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#next-action-followup-menu", OptionList))

    async def _show_manual_news_entry(self, item: ActionQueueItem) -> None:
        symbol = _manual_news_source_symbol(item.command)
        if symbol is None:
            await self._replace_action_panel(
                Static("人工新闻来源缺少有效证券代码，请刷新下一步行动队列。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        self.manual_news_action = item
        await self._replace_action_panel(
            Static(
                "\n".join(
                    [
                        "录入已核验新闻来源",
                        f"对象: {item.title.removeprefix('补充可审计来源: ')} ({symbol})",
                        "只录入已经核验的原文或官方披露。Tab 在字段间移动，"
                        "选择“保存已核验来源”后才会写入本地缓存。",
                    ]
                ),
                id="action-status",
            ),
            Input(placeholder="新闻标题", id="manual-news-headline"),
            Input(placeholder="与研究问题有关的关键事实", id="manual-news-summary"),
            Input(placeholder="https:// 原文或官方披露 URL", id="manual-news-source-url"),
            OptionList(
                Option("保存已核验来源", id="manual_news_save"),
                Option("取消并返回行动队列", id="manual_news_cancel"),
                id="manual-news-entry-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#manual-news-headline", Input))

    async def _save_manual_news_entry(self) -> None:
        item = self.manual_news_action
        if item is None:
            self._set_status("没有待保存的人工新闻来源。")
            return
        symbol = _manual_news_source_symbol(item.command)
        if symbol is None:
            self._set_status("人工新闻来源缺少有效证券代码。")
            return
        headline = self.query_one("#manual-news-headline", Input).value
        summary = self.query_one("#manual-news-summary", Input).value
        source_url = self.query_one("#manual-news-source-url", Input).value
        self._set_status("正在写入已核验来源，请稍候...")
        try:
            result = await asyncio.to_thread(
                write_manual_news_event,
                output_dir=self.output_dir,
                symbol=symbol,
                headline=headline,
                summary=summary,
                source_url=source_url,
            )
        except ValueError as error:
            self._set_status(f"无法保存: {error}")
            return
        acknowledgement = await asyncio.to_thread(
            acknowledge_manual_research_data_request,
            self.output_dir,
            action_type="manual_source",
            symbol=symbol,
        )
        self.manual_news_action = None
        self._refresh_dashboard()
        await self._replace_action_panel(
            Static(
                "\n".join(
                    [
                        f"已写入人工新闻证据: {symbol}",
                        f"缓存: {result.output_path}",
                        *(
                            ["对应研究数据请求已从待办队列移除。"]
                            if acknowledgement is not None
                            else []
                        ),
                        "来源已进入本地缓存；现在可重新下钻核验。",
                    ]
                ),
                id="action-status",
            ),
            OptionList(
                Option("重新下钻核验", id=f"manual_news_verify:{symbol}"),
                Option("查看下一步行动队列", id="next_actions"),
                Option("返回主菜单", id="refresh"),
                id="manual-news-followup-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#manual-news-followup-menu", OptionList))

    async def _verify_manual_news_source(self, action_id: str) -> None:
        symbol = action_id.removeprefix("manual_news_verify:").strip().upper()
        if not symbol:
            self._set_status("缺少重新核验所需的证券代码。")
            return
        await self._replace_action_panel(
            Static("正在重新下钻核验，请稍候...", id="action-status")
        )
        try:
            result = await asyncio.to_thread(
                verify_research_task,
                output_dir=self.output_dir,
                symbol=symbol,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._refresh_research_state()
        self.current_research_verification = result
        await self._replace_action_panel(
            Static(_research_verification_text(result), id="action-status"),
            OptionList(
                Option("查看下一步行动队列", id="next_actions"),
                Option("返回主菜单", id="refresh"),
                id="manual-news-verification-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#manual-news-verification-menu", OptionList))

    async def _show_manual_filing_entry(self, item: ActionQueueItem) -> None:
        details = _manual_filing_source_details(item.command)
        if details is None:
            await self._replace_action_panel(
                Static("人工文件证据缺少有效证券代码，请刷新下一步行动队列。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        symbol, company, form = details
        self.manual_filing_action = item
        await self._replace_action_panel(
            Static(
                "\n".join(
                    [
                        "录入已核验公告或表单摘要",
                        f"对象: {company} ({symbol})",
                        "只记录已经核验的关键事实和原始链接。Tab 在字段间移动，"
                        "选择“保存已核验文件”后才会写入本地缓存。",
                    ]
                ),
                id="action-status",
            ),
            Input(value=company, placeholder="公司名称", id="manual-filing-company"),
            Input(value=form, placeholder="表单类型，例如 4、8-K、10-Q", id="manual-filing-form"),
            Input(placeholder="公告日期，例如 2026-07-06", id="manual-filing-date"),
            Input(placeholder="已核验的关键事实", id="manual-filing-summary"),
            Input(placeholder="https:// 原文或官方披露 URL", id="manual-filing-source-url"),
            OptionList(
                Option("保存已核验文件", id="manual_filing_save"),
                Option("取消并返回行动队列", id="manual_filing_cancel"),
                id="manual-filing-entry-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#manual-filing-date", Input))

    async def _save_manual_filing_entry(self) -> None:
        item = self.manual_filing_action
        if item is None:
            self._set_status("没有待保存的人工文件证据。")
            return
        details = _manual_filing_source_details(item.command)
        if details is None:
            self._set_status("人工文件证据缺少有效证券代码。")
            return
        symbol, _, _ = details
        company = self.query_one("#manual-filing-company", Input).value
        form = self.query_one("#manual-filing-form", Input).value
        filing_date = self.query_one("#manual-filing-date", Input).value
        summary = self.query_one("#manual-filing-summary", Input).value
        source_url = self.query_one("#manual-filing-source-url", Input).value
        self._set_status("正在写入已核验文件，请稍候...")
        try:
            result = await asyncio.to_thread(
                write_manual_filing_summary,
                output_dir=self.output_dir,
                symbol=symbol,
                company=company,
                form=form,
                date=filing_date,
                summary=summary,
                source_url=source_url,
            )
        except ValueError as error:
            self._set_status(f"无法保存: {error}")
            return
        acknowledgement = await asyncio.to_thread(
            acknowledge_manual_research_data_request,
            self.output_dir,
            action_type="manual_filing",
            symbol=symbol,
            form=form,
        )
        self.manual_filing_action = None
        self._refresh_dashboard()
        await self._replace_action_panel(
            Static(
                "\n".join(
                    [
                        f"已写入人工文件证据: {symbol}",
                        f"缓存: {result.output_path}",
                        *(
                            ["对应研究数据请求已从待办队列移除。"]
                            if acknowledgement is not None
                            else []
                        ),
                        "文件摘要已进入本地缓存；现在可重新下钻核验。",
                    ]
                ),
                id="action-status",
            ),
            OptionList(
                Option("重新下钻核验", id=f"manual_filing_verify:{symbol}"),
                Option("查看下一步行动队列", id="next_actions"),
                Option("返回主菜单", id="refresh"),
                id="manual-filing-followup-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#manual-filing-followup-menu", OptionList))

    async def _verify_manual_filing_source(self, action_id: str) -> None:
        symbol = action_id.removeprefix("manual_filing_verify:").strip().upper()
        if not symbol:
            self._set_status("缺少重新核验所需的证券代码。")
            return
        await self._replace_action_panel(
            Static("正在重新下钻核验，请稍候...", id="action-status")
        )
        try:
            result = await asyncio.to_thread(
                verify_research_task,
                output_dir=self.output_dir,
                symbol=symbol,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._refresh_research_state()
        self.current_research_verification = result
        await self._replace_action_panel(
            Static(_research_verification_text(result), id="action-status"),
            OptionList(
                Option("查看下一步行动队列", id="next_actions"),
                Option("返回主菜单", id="refresh"),
                id="manual-filing-verification-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#manual-filing-verification-menu", OptionList))

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

    async def _show_pending_evidence_queue(self) -> None:
        items = await asyncio.to_thread(
            list_pending_evidence_reviews,
            output_dir=self.output_dir,
            limit=20,
        )
        self.pending_evidence_items = items
        await self._render_pending_evidence_queue(
            status_prefix=None,
        )

    async def _render_pending_evidence_queue(self, status_prefix: str | None) -> None:
        status_text = _pending_evidence_queue_text(
            self.pending_evidence_items,
            prefix=status_prefix,
        )
        if not self.pending_evidence_items:
            await self._replace_action_panel(Static(status_text, id="action-status"))
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(status_text, id="action-status"),
            OptionList(
                *[
                    Option(
                        _pending_evidence_item_label(item),
                        id=f"pending_evidence_item:{index}",
                    )
                    for index, item in enumerate(self.pending_evidence_items)
                ],
                id="pending-evidence-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#pending-evidence-menu", OptionList))

    async def _show_pending_evidence_detail(self, action_id: str) -> None:
        raw_index = action_id.removeprefix("pending_evidence_item:")
        try:
            index = int(raw_index)
            item = self.pending_evidence_items[index]
        except (ValueError, IndexError):
            await self._replace_action_panel(
                Static("待判定证据不存在，请刷新队列。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(_pending_evidence_detail_text(item), id="action-status"),
            OptionList(
                *[
                    Option(label, id=f"pending_evidence_review:{verdict}:{index}")
                    for verdict, label in _pending_evidence_review_menu_options(
                        item.suggested_verdict
                    )
                ],
                Option("返回待判定证据队列", id="pending_evidence"),
                id="pending-evidence-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#pending-evidence-action-menu", OptionList))

    async def _run_pending_evidence_review_action(self, action_id: str) -> None:
        payload = action_id.removeprefix("pending_evidence_review:")
        try:
            verdict, raw_index = payload.split(":", 1)
            item = self.pending_evidence_items[int(raw_index)]
        except (ValueError, IndexError):
            await self._replace_action_panel(
                Static("待判定证据复核动作不存在，请刷新队列。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        verdict_label = RESEARCH_EVIDENCE_REVIEW_VERDICTS.get(verdict)
        if verdict_label is None:
            await self._replace_action_panel(
                Static("未知证据复核方向。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(f"正在记录证据复核: {verdict_label}，请稍候...", id="action-status")
        )
        try:
            review_result = await asyncio.to_thread(
                record_research_evidence_review,
                output_dir=self.output_dir,
                symbol=item.symbol,
                name=None if item.symbol else item.display_name,
                evidence_text=item.evidence_text,
                verdict=verdict,
                note=f"TUI 待判定证据队列: {verdict_label}",
            )
            self.pending_evidence_items = await asyncio.to_thread(
                list_pending_evidence_reviews,
                output_dir=self.output_dir,
                limit=20,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        self.last_pending_evidence_review = review_result
        await self._render_pending_evidence_followup(
            status_prefix=_pending_evidence_review_recorded_summary(review_result),
        )

    async def _render_pending_evidence_followup(
        self,
        status_prefix: str,
    ) -> None:
        status_text = _pending_evidence_queue_text(
            self.pending_evidence_items,
            prefix=status_prefix,
        )
        await self._replace_action_panel(
            Static(status_text, id="action-status"),
            OptionList(
                Option("重新下钻核验", id="pending_evidence_verify_last"),
                *[
                    Option(
                        _pending_evidence_item_label(item),
                        id=f"pending_evidence_item:{index}",
                    )
                    for index, item in enumerate(self.pending_evidence_items)
                ],
                Option("返回待判定证据队列", id="pending_evidence"),
                id="pending-evidence-followup-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#pending-evidence-followup-menu", OptionList))

    async def _run_pending_evidence_verification(self) -> None:
        review = self.last_pending_evidence_review
        if review is None:
            await self._replace_action_panel(
                Static(
                    "还没有刚刚记录的证据复核，请先在待判定证据队列中复核一条证据。",
                    id="action-status",
                )
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        candidate = review.candidate
        symbol, name = _research_selection(candidate)
        await self._replace_action_panel(
            Static("正在重新下钻核验，请稍候...", id="action-status")
        )
        try:
            verification = await asyncio.to_thread(
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

        workbench = verification.workbench_result
        self.research_candidates = list(workbench.candidates)
        deepen_result = getattr(workbench, "deepen_result", None)
        self.research_packets = list(getattr(deepen_result, "packets", []))
        self.selected_research_index = select_research_candidate_index(
            workbench,
            symbol=symbol,
            name=name,
        )
        self.current_research_verification = verification
        self.pending_evidence_review_items = _pending_evidence_review_items(
            verification
        )
        await self._replace_action_panel(
            Static(_research_verification_text(verification), id="action-status"),
            OptionList(
                *[
                    Option(label, id=f"research_review:{verdict_id}")
                    for verdict_id, label in _research_review_menu_options(
                        verification.decision_board.suggested_verdict
                    )
                ],
                *[
                    Option(label, id=f"research_evidence_review:{detail_action_id}")
                    for detail_action_id, label in _research_evidence_review_menu_options(
                        verification
                    )
                ],
                Option("返回待判定证据队列", id="pending_evidence"),
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))

    async def _show_research_evidence_review_history(self) -> None:
        records = await asyncio.to_thread(
            list_research_evidence_reviews,
            output_dir=self.output_dir,
            limit=10,
        )
        await self._replace_action_panel(
            Static(_research_evidence_review_history_text(records), id="action-status")
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _show_research_memo_history(self) -> None:
        records = await asyncio.to_thread(
            list_research_memos,
            output_dir=self.output_dir,
            limit=10,
        )
        await self._replace_action_panel(
            Static(_research_memo_history_text(records), id="action-status")
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _show_research_data_requests(self) -> None:
        requests = await asyncio.to_thread(
            list_research_data_requests,
            output_dir=self.output_dir,
            limit=20,
        )
        self.research_data_requests = requests
        options = [
            Option(
                f"执行 {index}. {_short_request_label(item)}",
                id=f"research_data_request:{index - 1}",
            )
            for index, item in enumerate(requests, start=1)
        ]
        options.append(Option("返回主菜单", id="refresh"))
        if requests:
            await self._replace_action_panel(
                Static(_research_data_requests_text(requests), id="action-status"),
                OptionList(
                    *options,
                    id="research-data-request-menu",
                    markup=False,
                ),
            )
            self.set_focus(self.query_one("#research-data-request-menu", OptionList))
        else:
            await self._replace_action_panel(
                Static(_research_data_requests_text(requests), id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))

    async def _show_provider_backlog(self) -> None:
        items = await asyncio.to_thread(
            list_provider_backlog_items,
            output_dir=self.output_dir,
            limit=20,
        )
        await self._replace_action_panel(
            Static(_provider_backlog_text(items), id="action-status")
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _run_research_data_request_action(self, action_id: str) -> None:
        raw_index = action_id.removeprefix("research_data_request:")
        try:
            index = int(raw_index)
            request = self.research_data_requests[index]
        except (ValueError, IndexError):
            await self._replace_action_panel(
                Static("数据请求不存在，请重新打开研究数据请求。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static("正在执行研究数据请求，请稍候...", id="action-status")
        )
        try:
            result = await asyncio.to_thread(
                fulfill_research_data_request,
                self.output_dir,
                request_id=request.request_id,
            )
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._refresh_research_state()
        await self._replace_action_panel(
            Static(_research_data_request_fulfillment_text(result), id="action-status"),
            OptionList(
                Option("查看研究数据请求", id="research_data_requests"),
                Option("返回主菜单", id="refresh"),
                id="research-data-request-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-data-request-menu", OptionList))

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
        if action == "start_research":
            await self._run_research_verification(candidate)
            return
        if action == "fund_metadata_guide":
            await self._show_fund_metadata_guide(candidate)
            return
        if action == "import_fund_metadata":
            await self._import_fund_metadata_guide(candidate)
            return
        if action == "financials_guide":
            await self._show_financials_guide(candidate)
            return
        if action == "import_financials":
            await self._import_financials_guide(candidate)
            return
        if action in {
            "refresh_market",
            "refresh_news",
            "refresh_topic_news",
            "refresh_financials",
        } and not symbols:
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
            elif action == "refresh_topic_news":
                query = topic_news_query(candidate, packet)
                if not query:
                    await self._replace_action_panel(
                        Static(
                            "这个任务当前没有可用的主题关键词，请先刷新研究工作台。",
                            id="action-status",
                        )
                    )
                    self.set_focus(self.query_one("#action-menu", OptionList))
                    return
                result = await asyncio.to_thread(
                    pull_news_events,
                    symbols=symbols,
                    query=query,
                    output_dir=self.output_dir,
                    provider_id="auto",
                    force=True,
                )
            elif action == "refresh_filings":
                filing_symbols = research_filing_symbols(candidate, packet)
                if not filing_symbols:
                    await self._replace_action_panel(
                        Static(
                            "这个任务当前不适合自动拉取公司公告。"
                            "只有美股、港股或 A 股股票任务会启用该动作。",
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
            elif action == "refresh_financials":
                if candidate.market.upper() == "US":
                    result = await asyncio.to_thread(
                        pull_sec_financials,
                        symbols=symbols,
                        output_dir=self.output_dir,
                        force=True,
                    )
                elif candidate.market.upper() == "CN":
                    result = await asyncio.to_thread(
                        pull_tushare_financials,
                        symbols=symbols,
                        output_dir=self.output_dir,
                        force=True,
                    )
                else:
                    await self._replace_action_panel(
                        Static(
                            "港股数字财务请先使用财务资料向导，不能自动猜测数值。",
                            id="action-status",
                        )
                    )
                    self.set_focus(self.query_one("#action-menu", OptionList))
                    return
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

        refreshed = await self._refresh_research_state()
        selected_symbol, selected_name = _research_selection(candidate)
        refreshed_index = select_research_candidate_index(
            refreshed,
            symbol=selected_symbol,
            name=selected_name,
        )
        if refreshed_index is None:
            refreshed_index = selection
        self.selected_research_index = refreshed_index
        candidate = self.research_candidates[refreshed_index]
        packet = (
            self.research_packets[refreshed_index]
            if refreshed_index < len(self.research_packets)
            else None
        )
        actions = _post_refresh_research_actions(candidate, packet)
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

    async def _show_fund_metadata_guide(self, candidate: CandidateCheck) -> None:
        if not candidate.symbol:
            await self._replace_action_panel(
                Static(
                    "这个任务不是直接 ETF/基金代码，暂时不能生成单一基金资料向导。",
                    id="action-status",
                )
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static("正在生成基金资料补齐向导，请稍候...", id="action-status")
        )
        try:
            guide = await asyncio.to_thread(
                write_fund_metadata_guide,
                output_dir=self.output_dir,
                symbol=candidate.symbol,
                display_name=candidate.display_name,
                market=candidate.market,
            )
            self.current_fund_metadata_guide_path = guide.output_path
        except ValueError as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(_fund_metadata_guide_text(guide), id="action-status"),
            OptionList(
                Option("导入已填写模板", id="research_detail:import_fund_metadata"),
                Option("重新下钻核验", id="research_detail:verify_research"),
                Option("返回研究任务列表", id="research_detail:back_tasks"),
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))


    async def _show_financials_guide(self, candidate: CandidateCheck) -> None:
        if not candidate.symbol:
            await self._replace_action_panel(
                Static(
                    "这个任务没有直接证券代码，暂时不能生成财务资料向导。",
                    id="action-status",
                )
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static("正在生成财务资料补齐向导，请稍候...", id="action-status")
        )
        try:
            guide = await asyncio.to_thread(
                write_financial_snapshot_guide,
                output_dir=self.output_dir,
                symbol=candidate.symbol,
                display_name=candidate.display_name,
                market=candidate.market,
            )
            self.current_financials_guide_path = guide.output_path
        except ValueError as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static(_financials_guide_text(guide), id="action-status"),
            OptionList(
                Option("导入已填写模板", id="research_detail:import_financials"),
                Option("重新下钻核验", id="research_detail:verify_research"),
                Option("返回研究任务列表", id="research_detail:back_tasks"),
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))


    async def _import_financials_guide(self, candidate: CandidateCheck) -> None:
        guide_path = self.current_financials_guide_path
        if guide_path is None:
            await self._replace_action_panel(
                Static(
                    "还没有财务资料模板。请先生成财务资料补齐向导。",
                    id="action-status",
                )
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static("正在导入已填写财务资料模板，请稍候...", id="action-status")
        )
        try:
            result = await asyncio.to_thread(
                write_financial_snapshot_cache_from_file,
                output_dir=self.output_dir,
                guide_path=guide_path,
            )
            symbol = financial_snapshot_guide_symbol(guide_path)
            acknowledgement = await asyncio.to_thread(
                acknowledge_manual_research_data_request,
                self.output_dir,
                action_type="financials_hk_guide",
                symbol=symbol,
            )
        except ValueError as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._refresh_research_state()
        await self._replace_action_panel(
            Static(
                "\n".join(
                    [
                        f"人工财务快照已写入: {symbol}",
                        f"缓存: {result.output_path}",
                        *(
                            ["对应港股财务待办已标记为人工交接完成。"]
                            if acknowledgement is not None
                            else []
                        ),
                        "现在请重新下钻核验，确认数值是否支持当前研究问题。",
                    ]
                ),
                id="action-status",
            ),
            OptionList(
                Option("重新下钻核验", id="research_detail:verify_research"),
                Option("返回研究任务列表", id="research_detail:back_tasks"),
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))

    async def _import_fund_metadata_guide(self, candidate: CandidateCheck) -> None:
        guide_path = self.current_fund_metadata_guide_path
        if guide_path is None:
            await self._replace_action_panel(
                Static(
                    "还没有基金资料模板。请先生成基金资料补齐向导。",
                    id="action-status",
                )
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._replace_action_panel(
            Static("正在导入已填写基金资料模板，请稍候...", id="action-status")
        )
        try:
            result = await asyncio.to_thread(
                write_fund_metadata_cache_from_file,
                output_dir=self.output_dir,
                guide_path=guide_path,
            )
        except ValueError as error:
            await self._replace_action_panel(
                Static(f"操作失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        await self._refresh_research_state()
        await self._replace_action_panel(
            Static(
                _fund_metadata_imported_text(candidate, result.output_path),
                id="action-status",
            ),
            OptionList(
                Option("重新下钻核验", id="research_detail:verify_research"),
                Option("返回研究任务列表", id="research_detail:back_tasks"),
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
        symbol, name = _research_selection(candidate)
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
        self.current_research_verification = result
        self.pending_evidence_review_items = _pending_evidence_review_items(result)
        await self._replace_action_panel(
            Static(_research_verification_text(result), id="action-status"),
            OptionList(
                *[
                    Option(label, id=f"research_review:{verdict}")
                    for verdict, label in _research_review_menu_options(
                        result.decision_board.suggested_verdict
                    )
                ],
                *[
                    Option(label, id=f"research_evidence_review:{action_id}")
                    for action_id, label in _research_evidence_review_menu_options(
                        result
                    )
                ],
                Option("返回研究任务列表", id="research_detail:back_tasks"),
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))

    async def _run_research_evidence_review_action(self, action_id: str) -> None:
        payload = action_id.removeprefix("research_evidence_review:")
        try:
            verdict, raw_index = payload.split(":", 1)
            evidence_index = int(raw_index)
            evidence_text = self.pending_evidence_review_items[evidence_index]
        except (ValueError, IndexError):
            await self._replace_action_panel(
                Static("证据复核动作不存在，请重新运行下钻核验。", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        verdict_label = RESEARCH_EVIDENCE_REVIEW_VERDICTS.get(verdict)
        if verdict_label is None:
            await self._replace_action_panel(
                Static("未知证据复核方向。", id="action-status")
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
        symbol, name = _research_selection(candidate)
        note = f"TUI 证据复核: {verdict_label}"
        await self._replace_action_panel(
            Static(
                f"正在记录证据复核: {verdict_label}，请稍候...",
                id="action-status",
            )
        )
        try:
            review_result = await asyncio.to_thread(
                record_research_evidence_review,
                output_dir=self.output_dir,
                symbol=symbol,
                name=name,
                evidence_text=evidence_text,
                verdict=verdict,
                note=note,
            )
            verification = await asyncio.to_thread(
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
        self.current_research_verification = verification
        self.pending_evidence_review_items = _pending_evidence_review_items(verification)
        await self._replace_action_panel(
            Static(
                _research_evidence_review_recorded_text(review_result, verification),
                id="action-status",
            ),
            OptionList(
                *[
                    Option(label, id=f"research_review:{verdict_id}")
                    for verdict_id, label in _research_review_menu_options(
                        verification.decision_board.suggested_verdict
                    )
                ],
                *[
                    Option(label, id=f"research_evidence_review:{detail_action_id}")
                    for detail_action_id, label in _research_evidence_review_menu_options(
                        verification
                    )
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
        symbol, name = _research_selection(candidate)
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
                *[
                    Option(label, id=action_id)
                    for action_id, label in _research_memo_followup_actions(result)
                ],
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
        symbol, name = _research_selection(candidate)
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
                *[
                    Option(label, id=f"research_detail:{action}")
                    for action, label in _research_review_followup_actions(
                        result.verdict
                    )
                ],
                id="research-detail-action-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#research-detail-action-menu", OptionList))

    async def _refresh_research_state(self) -> WorkbenchCheckResult:
        result = await asyncio.to_thread(run_workbench_check, output_dir=self.output_dir)
        self.research_candidates = list(result.candidates)
        deepen_result = getattr(result, "deepen_result", None)
        self.research_packets = list(getattr(deepen_result, "packets", []))
        return result

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
        self._refresh_dashboard()
        self.pending_action = "today_discovery"
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
            ),
            OptionList(
                Option("进入研究工作台", id="discovery_followup_workbench"),
                Option("查看下一步行动", id="discovery_followup_next_actions"),
                Option("查看研究数据请求", id="discovery_followup_data_requests"),
                Option("返回主菜单", id="refresh"),
                id="discovery-followup-menu",
                markup=False,
            ),
        )
        self.set_focus(self.query_one("#discovery-followup-menu", OptionList))

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
            elif action == "forecast":
                forecast_result = await asyncio.to_thread(
                    generate_timesfm_forecasts,
                    output_dir=self.output_dir,
                    symbols=symbols,
                    horizon_days=20,
                )
                lines = [
                    "TimesFM 预测已完成",
                    f"缓存: {forecast_result.output_path}",
                    *[
                        (
                            f"{item.symbol}: 区间 {item.lower:.2f} - {item.upper:.2f}; "
                            f"中位读数 {item.midpoint:.2f}; horizon {item.horizon_days} 日"
                        )
                        for item in forecast_result.forecasts
                    ],
                    "边界: 预测区间只用于研究比较，不是买卖建议。",
                ]
                self.pending_action = None
                await self._replace_action_panel(
                    Static("\n".join(lines), id="action-status"),
                    OptionList(
                        Option("返回主菜单", id="refresh"),
                        id="forecast-followup-menu",
                        markup=False,
                    ),
                )
                self.set_focus(self.query_one("#forecast-followup-menu", OptionList))
                return
            else:
                self._set_status("这个操作不接收证券代码。")
                return
        except (ForecastProviderError, RuntimeError, ValueError) as error:
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

    async def _show_ipo_events(self) -> None:
        path = self.output_dir / "data" / "ipo-events.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            lines = [
                "IPO/打新资料",
                "暂无已导入的 IPO 事件。",
                '生成模板: lychee data guide ipo --market HK --name "公司名称"',
                "边界: 资料需要人工核验，不确认申购资格或收益。",
            ]
        else:
            lines = ["IPO/打新资料"]
            for row in rows[:10]:
                if not isinstance(row, dict):
                    continue
                lines.extend(
                    [
                        (
                            f"- {row.get('name', '未命名')} "
                            f"[{row.get('market', '-')} / {row.get('symbol', '-')} ]"
                        ),
                        (
                            f"  申购: {row.get('subscription_start', '-')} 至 "
                            f"{row.get('subscription_end', '-')} | "
                            f"上市: {row.get('listing_date', '-')}"
                        ),
                        (
                            f"  价格区间: {row.get('price_min', '-')} - "
                            f"{row.get('price_max', '-')} | 手数: {row.get('lot_size', '-')}"
                        ),
                        f"  资格说明: {row.get('account_eligibility_note') or '需人工核对'}",
                        f"  来源: {row.get('source_url', '-')}"
                    ]
                )
            lines.append("边界: 以上是已核验资料索引，不确认申购资格，不是收益或投资建议。")
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

    async def _show_demo_portfolio_check(self) -> None:
        try:
            result = await asyncio.to_thread(
                check_portfolio,
                portfolio_path=DEMO_ROOT / "portfolio.csv",
                policy_path=DEMO_ROOT / "policy.yaml",
                output_dir=self.output_dir,
            )
            artifact_path = write_portfolio_check_artifact(result, self.output_dir)
        except (OSError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"模拟组合检查失败: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        lines = [
            "模拟组合检查",
            f"状态: {result.status_label}",
            f"目标权重合计: {result.total_target_weight:.2%}",
            f"现金目标比例: {result.cash_target_weight:.2%}",
            f"实验性资产目标比例: {result.experimental_target_weight:.2%}",
            f"基础货币: {result.base_currency}",
            f"识别币种: {', '.join(result.currencies)}",
            *(
                [f"FX 缺口: {', '.join(result.missing_fx_currencies)}"]
                if result.missing_fx_currencies
                else []
            ),
            "",
            "目标项",
            *[
                f"- {target.symbol} {target.target_weight:.2%} {target.asset_type}"
                for target in result.targets
            ],
            *(
                [
                    "",
                    "当前只读估值快照",
                    *[
                        (
                            f"- {valuation.symbol}: {valuation.value_base:.2f} "
                            f"{result.base_currency} | 当前 {valuation.actual_weight:.2%} "
                            f"目标 {valuation.target_weight:.2%} | "
                            f"偏离 {valuation.drift:+.2%}"
                            + (
                                f" | 成本 {valuation.cost_basis_base:.2f} "
                                f"{result.base_currency} | 未实现差额 "
                                f"{valuation.unrealized_pnl_base:+.2f} "
                                f"{result.base_currency}"
                                if valuation.unrealized_pnl_base is not None
                                and valuation.cost_basis_base is not None
                                else ""
                            )
                        )
                        for valuation in result.valuations
                    ],
                ]
                if result.valuations
                else []
            ),
            *[f"⚠️ 估值缺口: {gap}" for gap in result.valuation_gaps],
            *[f"✅ 通过: {item}" for item in result.policy_result.passes],
            *[f"⚠️ 警告: {item}" for item in [*result.policy_result.warnings, *result.warnings]],
            *[f"❌ 需要修正: {item}" for item in [*result.policy_result.errors, *result.errors]],
            *(
                [
                    "成本基础和未实现差额仅按当前行情及当前 FX 缓存折算，"
                    "不是税务成本、券商结算价值或收益预测。"
                ]
                if any(
                    valuation.unrealized_pnl_base is not None
                    for valuation in result.valuations
                )
                else []
            ),
            f"检查记录: {artifact_path}",
            "边界: 这是组合练习和政策检查，不是估值、交易或投资建议。",
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


def _research_selection(candidate: CandidateCheck) -> tuple[str | None, str | None]:
    if candidate.symbol:
        return candidate.symbol, None
    return None, candidate.display_name


def _manual_news_source_symbol(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if parts[:4] != ["lychee", "data", "set", "news"]:
        return None
    try:
        symbol = parts[parts.index("--symbol") + 1].strip().upper()
    except (IndexError, ValueError):
        return None
    return symbol or None


def _manual_filing_source_details(command: str) -> tuple[str, str, str] | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if parts[:4] != ["lychee", "data", "set", "filing"]:
        return None
    try:
        symbol = parts[parts.index("--symbol") + 1].strip().upper()
        company = parts[parts.index("--company") + 1].strip()
        form = parts[parts.index("--form") + 1].strip().upper()
    except (IndexError, ValueError):
        return None
    if not symbol or not company or not form or form.startswith("<"):
        return None
    return symbol, company, form


def _research_workbench_intro(result: WorkbenchCheckResult) -> str:
    portfolio = getattr(result, "portfolio_context", None)
    portfolio_status = str(getattr(portfolio, "status", "未配置"))
    portfolio_count = int(getattr(portfolio, "valuation_count", 0))
    portfolio_currency = str(getattr(portfolio, "base_currency", ""))
    portfolio_action = str(
        getattr(
            portfolio,
            "next_action",
            "先运行只读组合检查，建立当前持仓审计上下文。",
        )
    )
    portfolio_drifts = getattr(portfolio, "drift_readings", [])
    portfolio_transactions = str(
        getattr(portfolio, "transaction_status", "未导入")
    )
    portfolio_transaction_gaps = getattr(portfolio, "transaction_gaps", [])
    lines = [
        "AlphaDesk 研究工作台",
        "选择一个研究任务，按 Enter 开始研究。Esc 返回主菜单。",
        (
            f"状态: {_display_workbench_status(result.status)} | "
            f"可执行 {result.ready_count} | 阻塞 {result.blocked_count} | "
            f"总任务 {len(result.candidates)}"
        ),
        "",
        "组合风险上下文",
        (
            f"- 审计状态: {portfolio_status} | 估值 {portfolio_count} 项"
            + (f" | 基础货币: {portfolio_currency}" if portfolio_currency else "")
        ),
        f"- 流水审计: {portfolio_transactions}",
        f"- 研究前动作: {portfolio_action}",
    ]
    if portfolio_drifts:
        lines.append("- 目标偏离读数: " + "；".join(portfolio_drifts[:3]))
    if portfolio_transaction_gaps:
        lines.append("- 流水缺口: " + "；".join(portfolio_transaction_gaps[:2]))
    if result.candidates:
        first = result.candidates[0]
        lines.extend(
            [
                "",
                "现在先做",
                f"- {first.display_name}: {first.next_step}",
            ]
        )
        if first.next_command:
            lines.append(f"  只需要执行: {first.next_command}")
    return "\n".join(lines)


def _research_task_label(candidate: CandidateCheck) -> str:
    ranking_text = (
        f" | 排序: {candidate.ranking_reason}"
        if candidate.ranking_reason
        else ""
    )
    return (
        f"{candidate.display_name} [{candidate.market}] | "
        f"入口: {candidate.observation_entry} | "
        f"优先级: {candidate.priority} | "
        f"{candidate.evidence_status}"
        f"{ranking_text}"
    )


def _research_review_history_text(records: list[ResearchReviewRecord]) -> str:
    if not records:
        return "\n".join(
            [
                "研究复核历史",
                "暂无研究复核记录。先在研究任务面板中运行下钻核验，再记录复核判断。",
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


def _pending_evidence_queue_text(
    items: list[PendingEvidenceReviewItem],
    *,
    prefix: str | None = None,
) -> str:
    lines: list[str] = []
    if prefix:
        lines.extend([prefix, ""])
    if not items:
        empty_hint = (
            "暂无待判定证据。可以重新下钻核验，查看证据板是否已经更新。"
            if prefix
            else "暂无待判定证据。先在研究工作台里运行下钻核验。"
        )
        lines.extend(
            (
                "待判定证据队列",
                empty_hint,
                "边界: 待判定证据队列不是买卖建议。",
            )
        )
        return "\n".join(lines)
    lines.append("待判定证据队列")
    for item in items:
        lines.extend(
            [
                (
                    f"- {item.display_name} ({item.symbol or '-'}) "
                    f"[{item.market}]"
                ),
                f"  要回答的问题: {item.primary_question}",
                f"  待判定证据: {item.evidence_text}",
                (
                    f"  系统建议: {item.suggested_verdict_label} | "
                    f"{item.suggested_reason}"
                ),
                f"  复核命令: {item.review_command}",
                f"  下钻核验: {item.artifact_path}",
            ]
        )
    lines.append("边界: 待判定证据队列不是买卖建议。")
    return "\n".join(lines)


def _pending_evidence_item_label(item: PendingEvidenceReviewItem) -> str:
    return (
        f"{item.display_name} ({item.symbol or '-'}) [{item.market}] | "
        f"{item.suggested_verdict_label} | {item.evidence_text}"
    )


def _pending_evidence_detail_text(item: PendingEvidenceReviewItem) -> str:
    return "\n".join(
        [
            "待判定证据详情",
            f"研究任务: {item.display_name} ({item.symbol or '-'}) [{item.market}]",
            f"要回答的问题: {item.primary_question}",
            f"待判定证据: {item.evidence_text}",
            f"系统建议: {item.suggested_verdict_label}",
            f"建议理由: {item.suggested_reason}",
            f"原始证据行: {item.raw_evidence}",
            f"下钻核验: {item.artifact_path}",
            "可直接按系统建议记录，也可以手动覆盖方向。",
            "边界: 待判定证据详情不是买卖建议。",
        ]
    )


def _pending_evidence_review_menu_options(
    suggested_verdict: str | None = None,
) -> list[tuple[str, str]]:
    options = [
        ("support", "标为支持证据"),
        ("reverse", "标为风险/反向待查"),
        ("irrelevant", "标为无关/排除"),
    ]
    if suggested_verdict is None:
        return options
    labels = dict(options)
    if suggested_verdict not in labels:
        return options
    return [
        (suggested_verdict, f"按系统建议记录: {labels[suggested_verdict]}")
    ] + [(verdict, label) for verdict, label in options if verdict != suggested_verdict]


def _pending_evidence_review_recorded_summary(
    result: ResearchEvidenceReviewResult,
) -> str:
    return "\n".join(
        [
            "证据复核已记录",
            f"复核任务: {result.candidate.display_name} [{result.candidate.market}]",
            f"证据文本: {result.evidence_text}",
            f"复核方向: {result.verdict_label}",
            f"记录: {result.artifact_path}",
            "边界: 单条证据复核不是买卖建议。",
        ]
    )


def _research_evidence_review_history_text(
    records: list[ResearchEvidenceReviewRecord],
) -> str:
    if not records:
        return "\n".join(
            [
                "证据复核历史",
                "暂无证据复核记录。先在下钻核验页逐条标记新闻证据方向。",
                "边界: 单条证据复核历史不是买卖建议。",
            ]
        )
    lines = ["证据复核历史"]
    for record in records:
        lines.extend(
            [
                (
                    f"- {record.display_name} ({record.symbol or '-'}) "
                    f"[{record.market}] {record.verdict_label}"
                ),
                f"  时间: {record.created_at}",
                f"  证据文本: {record.evidence_text}",
                f"  备注: {record.note}",
                f"  记录: {record.review_path}",
            ]
        )
    lines.append("边界: 单条证据复核历史不是买卖建议。")
    return "\n".join(lines)


def _research_memo_history_text(records: list[ResearchMemoRecord]) -> str:
    if not records:
        return "\n".join(
            [
                "研究备忘录历史",
                "暂无研究备忘录。先在研究任务面板中生成研究备忘录。",
                "边界: 研究备忘录历史不是买卖建议。",
            ]
        )
    lines = ["研究备忘录历史"]
    for record in records:
        lines.extend(
            [
                (
                    f"- {record.display_name} ({record.symbol or '-'}) "
                    f"[{record.market}] 置信度: {record.confidence}"
                ),
                f"  时间: {record.created_at}",
                f"  摘要: {record.summary}",
                (
                    f"  证据: 支持 {record.support_count} | "
                    f"反方 {record.skeptic_count} | "
                    f"待补 {record.missing_count} | 下一步 {record.next_step_count}"
                ),
                f"  记录: {record.memo_path}",
                f"  下钻核验: {record.verification_path}",
            ]
        )
    lines.append("边界: 研究备忘录历史不是买卖建议。")
    return "\n".join(lines)


def _research_data_requests_text(requests: list[ResearchDataRequest]) -> str:
    if not requests:
        return "\n".join(
            [
                "研究数据请求",
                "暂无研究数据请求。先在研究任务面板中运行下钻核验或生成研究备忘录。",
                "边界: 数据请求队列只用于补证据，不是买卖建议。",
            ]
        )
    lines = ["研究数据请求"]
    for index, item in enumerate(requests, start=1):
        has_manual_news_action = any(
            action.action_type == "manual_source" for action in item.suggested_actions
        )
        has_manual_filing_action = any(
            action.action_type == "manual_filing" for action in item.suggested_actions
        )
        has_official_news_action = any(
            action.action_type == "news_official"
            for action in item.suggested_actions
        )
        lines.extend(
            [
                (
                    f"{index}. {item.display_name} ({item.symbol or '-'}) "
                    f"[{item.market}] 置信度: {item.confidence}"
                ),
                f"  时间: {item.created_at}",
                f"  请求: {item.request_text}",
                *(
                    [
                        "  说明: 先刷新官方新闻；完成后仍需录入已核验的原文或官方披露，"
                        "然后重新核验。"
                    ]
                    if has_manual_news_action and has_official_news_action
                    else [
                        "  说明: 自动新闻已刷新但没有命中主题。请只录入已核验的原文或官方披露，"
                        "然后重新核验。"
                    ]
                    if has_manual_news_action
                    else [
                        "  说明: 这条请求需要核验公告或表单正文。请录入已核验的关键事实和原始链接，"
                        "然后重新核验。"
                    ]
                    if has_manual_filing_action
                    else ["  说明: 这类数据当前没有自动补数据命令，需要人工补来源或等待插件接入。"]
                    if has_manual_news_action or has_manual_filing_action
                    else []
                ),
                *(
                    ["  自动动作完成后仍需人工核验来源。"]
                    if (has_manual_news_action or has_manual_filing_action)
                    and not research_data_request_needs_manual_source(item)
                    else []
                ),
                "  建议命令:",
                *[f"  - {command}" for command in item.suggested_commands],
                *(
                    [f"  来源核验: {item.verification_path}"]
                    if item.source_type == "verification"
                    else [
                        f"  来源备忘录: {item.memo_path}",
                        f"  下钻核验: {item.verification_path}",
                    ]
                ),
            ]
        )
    lines.append("边界: 数据请求队列只用于补证据，不是买卖建议。")
    return "\n".join(lines)


def _action_queue_text(items: list[ActionQueueItem]) -> str:
    if not items:
        return "\n".join(
            [
                "下一步行动队列",
                "暂无下一步行动。请先运行“今日市场发现”或“研究工作台”。",
                "边界: 行动队列只推进研究流程，不是买卖建议。",
            ]
        )
    lines = ["下一步行动队列"]
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                f"{index}. [{item.area}] {item.title}",
                f"  为什么: {item.detail}",
                f"  执行: {item.command}",
                f"  来源: {item.source}",
            ]
        )
    lines.append("边界: 行动队列只推进研究流程，不是买卖建议。")
    return "\n".join(lines)


def _action_queue_execution_text(result: ActionQueueExecution) -> str:
    count_label = (
        "新闻行数"
        if result.item.command.startswith("lychee data pull news")
        else "处理数量"
    )
    lines = [
        "下一步行动执行结果",
        f"行动: [{result.item.area}] {result.item.title}",
        f"状态: {result.status}",
        f"结果: {result.message}",
        f"{count_label}: {result.count}",
    ]
    if result.output_path is not None:
        lines.append(f"输出: {result.output_path}")
    lines.extend(f"警告: {warning}" for warning in result.warnings)
    if result.next_command:
        lines.append(f"下一步核验: {result.next_command}")
    lines.append("边界: 自动行动只补研究证据，不是买卖建议。")
    return "\n".join(lines)


def _opportunity_radar_text(report: OpportunityRadarReport) -> str:
    lines = [
        "机会雷达",
        f"状态: {report.status}",
        report.disclaimer,
    ]
    lines.extend(f"警告: {warning}" for warning in report.warnings)
    if not report.signals:
        return "\n".join(lines)
    for index, signal in enumerate(report.signals, start=1):
        lines.extend(
            [
                f"{index}. {signal.symbol} [{signal.market}] {signal.theme}",
                (
                    f"  信号: 新闻 {signal.news_count} | 主题命中 {signal.theme_hits} "
                    f"| 成交量排名 {signal.volume_rank} | 分数 {signal.score}"
                ),
                f"  行情快照: {signal.price_snapshot}",
                f"  为什么值得研究: {signal.why_it_matters}",
                "  证据标题:",
                *[f"  - {headline}" for headline in signal.evidence],
                "  下一步验证:",
                *[f"  - {step}" for step in signal.next_steps],
            ]
        )
        if signal.drilldown_targets:
            lines.append("  可下钻目标:")
            for target in signal.drilldown_targets:
                lines.extend(
                    [
                        (
                            f"  - {target.display_name} ({target.symbol}) "
                            f"[{target.market}] {target.category}"
                        ),
                        f"    为什么: {target.reason}",
                        f"    证据缺口: {target.evidence_gap}",
                        *[f"    执行: {step}" for step in target.next_steps],
                    ]
                )
    return "\n".join(lines)


def _provider_backlog_text(items: list[ProviderBacklogItem]) -> str:
    if not items:
        return "\n".join(
            [
                "数据源缺口队列",
                "暂无数据源缺口。当前研究数据请求已有自动动作或暂无请求。",
                "边界: 数据源缺口队列只用于规划补数据能力，不是买卖建议。",
            ]
        )
    lines = ["数据源缺口队列"]
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                (
                    f"{index}. {item.display_name} ({item.symbol or '-'}) "
                    f"[{item.market}]"
                ),
                f"  时间: {item.created_at}",
                f"  研究请求: {item.request_text}",
                f"  数据领域: {item.data_domain}",
                f"  插件类型: {item.plugin_type}",
                f"  当前缺口: {item.coverage_gap}",
                "  候选来源形态:",
                *[f"  - {source}" for source in item.suggested_provider_examples],
                "  建议命令:",
                *[f"  - {command}" for command in item.suggested_commands],
                f"  下一步: {item.next_step}",
                *(
                    [f"  来源核验: {item.verification_path}"]
                    if not item.memo_path
                    else [f"  来源备忘录: {item.memo_path}"]
                ),
                f"  下钻核验: {item.verification_path}",
            ]
        )
    lines.append("边界: 数据源缺口队列只用于规划补数据能力，不是买卖建议。")
    return "\n".join(lines)


def _research_data_request_fulfillment_text(
    result: ResearchDataRequestFulfillment,
) -> str:
    request = result.request
    lines = [
        "研究数据请求执行结果",
        f"请求: {request.display_name} ({request.symbol or '-'}) [{request.market}]",
        f"内容: {request.request_text}",
        "",
        "执行明细",
    ]
    for execution in result.executions:
        lines.extend(
            [
                (
                    f"- {_display_data_request_action(execution.action_type)}: "
                    f"{_display_data_request_execution_status(execution.status)}"
                ),
                f"  行数: {execution.count}",
                f"  说明: {execution.message}",
                f"  输出: {execution.output_path or '-'}",
                *[f"  警告: {warning}" for warning in execution.warnings],
            ]
        )
    lines.append("边界: 数据请求执行只补证据，不是买卖建议。")
    return "\n".join(lines)


def _short_request_label(item: ResearchDataRequest) -> str:
    prefix = f"{item.display_name} ({item.symbol or '-'})"
    text = item.request_text.replace("\n", " ")
    if len(text) > 30:
        text = text[:30] + "..."
    return f"{prefix}: {text}"


def _display_data_request_action(action_type: str) -> str:
    return {
        "fund_metadata_guide": "基金资料模板",
        "fund_metadata_import": "基金资料导入",
        "market": "行情",
        "news": "新闻",
        "filings": "公司公告",
        "verify": "下钻核验",
    }.get(action_type, action_type)


def _display_data_request_execution_status(status: str) -> str:
    return {
        "completed": "已完成",
        "cached": "缓存命中",
        "no-data": "无数据",
        "failed": "失败",
        "skipped": "跳过",
        "manual_required": "需人工",
    }.get(status, status)


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
            *_evidence_board_lines(
                "离题/已过滤",
                result.evidence_board.get("off_topic", []),
            ),
            *_evidence_board_lines("待补证据", result.evidence_board["missing"]),
            "",
            "证据变化",
            f"状态: {result.evidence_change.status_label}",
            f"摘要: {result.evidence_change.summary}",
            *(
                [f"上一份核验: {result.evidence_change.previous_artifact_path}"]
                if result.evidence_change.previous_artifact_path
                else []
            ),
            *_evidence_change_detail_lines(result),
            "",
            result.analyst_readout.title,
            result.analyst_readout.signal,
            result.analyst_readout.pressure,
            result.analyst_readout.gap,
            result.analyst_readout.evidence_change,
            result.analyst_readout.next_action,
            *(
                [f"执行命令: {result.analyst_readout.next_command}"]
                if result.analyst_readout.next_command
                else []
            ),
            "",
            result.hypothesis_panel.title,
            result.hypothesis_panel.core_question,
            result.hypothesis_panel.working_hypothesis,
            *_evidence_board_lines("支持链", result.hypothesis_panel.support_chain),
            *_evidence_board_lines("反证链", result.hypothesis_panel.counter_chain),
            *_evidence_board_lines(
                "缺口优先级",
                result.hypothesis_panel.gap_priorities,
            ),
            *_evidence_board_lines(
                "下一批数据请求",
                result.hypothesis_panel.next_data_requests,
            ),
            "",
            "研究决策板",
            f"状态: {result.decision_board.workflow_label}",
            f"要回答的问题: {result.decision_board.primary_question}",
            f"判断规则: {result.decision_board.decision_rule}",
            (
                "建议记录: "
                f"{result.decision_board.suggested_verdict}"
                f"（{result.decision_board.suggested_verdict_label}）"
            ),
            "工作台下一步",
            *[f"- {step}" for step in result.decision_board.next_steps],
            *[f"- 执行命令: {command}" for command in result.decision_board.next_commands],
            "",
            result.conclusion,
            "下一步",
        ]
    )
    lines.extend(f"- {action}" for action in result.next_actions)
    lines.append("边界: 下钻核验不是买卖建议。")
    return "\n".join(lines)


def _evidence_change_detail_lines(result: ResearchVerificationResult) -> list[str]:
    lines: list[str] = []
    for title, rows in research_evidence_change_detail_groups(result.evidence_change):
        if not rows:
            continue
        if not lines:
            lines.extend(["", "证据变化明细"])
        lines.extend(_evidence_board_lines(title, rows[:5]))
    return lines


def _research_review_recorded_text(result: ResearchReviewResult) -> str:
    counts = result.evidence_counts
    lines = [
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
            f"离题/已过滤 {counts.get('off_topic', 0)} | "
            f"待补 {counts['missing']}"
        ),
    ]
    followups = _research_review_followup_actions(result.verdict)
    if followups:
        lines.extend(["", "工作台下一步"])
        lines.extend(f"- {label}" for _, label in followups)
    lines.append("边界: 研究复核不是买卖建议。")
    return "\n".join(lines)


def _fund_metadata_guide_text(guide: FundMetadataGuide) -> str:
    lines = [
        "基金资料补齐向导",
        f"标的: {guide.display_name} ({guide.symbol}) [{guide.market}]",
        f"模板已写入: {guide.output_path}",
        "",
        "先查这些资料",
        "- 跟踪指数或基准",
        "- 费用率或管理费说明",
        "- 成分或持仓摘要",
        "- 资料来源 URL",
        "",
        "建议来源",
        *[f"- {source}" for source in guide.suggested_sources],
        "",
        "填完模板后导入",
        guide.apply_command,
        "",
        "查完后写入",
        guide.write_command,
        "",
        "边界: 向导只生成模板，不会猜测基金资料，也不是投资建议。",
    ]
    return "\n".join(lines)


def _financials_guide_text(guide: FinancialSnapshotGuide) -> str:
    lines = [
        "港股财务资料补齐向导",
        f"标的: {guide.display_name} ({guide.symbol}) [{guide.market}]",
        f"模板已写入: {guide.output_path}",
        "",
        "请从港交所披露或公司年报中填写",
        "- 报告类型和报告期末日",
        "- 申报日和报告货币",
        "- 营业收入、净利润、经营活动现金流",
        "- 原始资料 URL",
        "",
        "建议来源",
        *[f"- {source}" for source in guide.suggested_sources],
        "",
        "填写并保存模板后，在本页面选择导入已填写模板。",
        "本页的“导入已填写模板”会自动完成写入，不需要记命令。",
        "",
        "边界: 向导只保存可审计的人工财务资料，不会猜测数值，也不是投资建议。",
    ]
    return "\n".join(lines)


def _fund_metadata_imported_text(candidate: CandidateCheck, output_path: Path) -> str:
    return "\n".join(
        [
            "基金资料已导入",
            f"标的: {candidate.display_name} ({candidate.symbol}) [{candidate.market}]",
            f"缓存: {output_path}",
            "下一步: 重新下钻核验，确认基金资料是否解除阻塞。",
            "边界: 基金资料导入不是买卖建议。",
        ]
    )


def _research_evidence_review_recorded_text(
    result: ResearchEvidenceReviewResult,
    verification: ResearchVerificationResult,
) -> str:
    return "\n".join(
        [
            "证据复核已记录",
            f"记录: {result.artifact_path}",
            f"研究库: {result.db_path}",
            f"证据文本: {result.evidence_text}",
            f"复核方向: {result.verdict_label}",
            f"备注: {result.note}",
            "边界: 单条证据复核不是买卖建议。",
            "",
            "更新后的下钻核验",
            _research_verification_text(verification),
        ]
    )


def _research_review_followup_actions(verdict: str) -> list[tuple[str, str]]:
    if verdict == "needs_more_evidence":
        return [
            ("refresh_topic_news", "刷新主题新闻"),
            ("verify_research", "重新下钻核验"),
            ("back_tasks", "返回研究任务列表"),
        ]
    if verdict == "continue_research":
        return [
            ("generate_memo", "生成研究备忘录"),
            ("verify_research", "重新下钻核验"),
            ("back_tasks", "返回研究任务列表"),
        ]
    return [("back_tasks", "返回研究任务列表")]


def _post_refresh_research_actions(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[tuple[str, str]]:
    priority_actions = [
        ("verify_research", "重新下钻核验"),
        ("generate_memo", "生成研究备忘录"),
        ("back_tasks", "返回研究任务列表"),
    ]
    priority_ids = {action for action, _ in priority_actions}
    remaining_actions = [
        (action, label)
        for action, label in research_detail_actions(candidate, packet)
        if action not in priority_ids
    ]
    return [*priority_actions, *remaining_actions]


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
            "工作假设",
            memo.working_hypothesis,
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
            "反证检查",
            *_text_list_lines(memo.falsification_checks),
            "",
            "待补证据",
            *_text_list_lines(memo.missing_evidence),
            "",
            "下一批数据请求",
            *_text_list_lines(memo.next_data_requests),
            "",
            "下一步研究动作",
            *_text_list_lines(memo.next_research_steps),
            "边界: 研究备忘录不是买卖建议。",
        ]
    )


def _research_memo_followup_actions(
    result: ResearchMemoResult,
) -> list[tuple[str, str]]:
    suggested_verdict = result.verification.decision_board.suggested_verdict
    verdict = (
        suggested_verdict
        if suggested_verdict in RESEARCH_REVIEW_VERDICTS
        else "continue_research"
    )
    verdict_label = RESEARCH_REVIEW_VERDICTS[verdict]
    return [
        (f"research_review:{verdict}", f"记录研究复核: {verdict_label}"),
        ("research_detail:verify_research", "重新下钻核验"),
        ("research_data_requests", "查看研究数据请求"),
        ("research_memos", "查看研究备忘录历史"),
        ("research_detail:back_tasks", "返回研究任务列表"),
    ]


def _research_review_menu_options(
    suggested_verdict: str | None = None,
) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    if suggested_verdict in RESEARCH_REVIEW_VERDICTS:
        options.append(
            (
                suggested_verdict,
                f"按工作台建议记录: {RESEARCH_REVIEW_VERDICTS[suggested_verdict]}",
            )
        )
    options.extend(
        (verdict, f"记录: {label}")
        for verdict, label in RESEARCH_REVIEW_VERDICTS.items()
        if verdict != suggested_verdict
    )
    return options


def _pending_evidence_review_items(result: ResearchVerificationResult) -> list[str]:
    items: list[str] = []
    prefix = "新闻待判定: "
    suffix = " 命中主题但方向未明。"
    for row in result.evidence_board["risk"]:
        if not row.startswith(prefix):
            continue
        evidence_text = row.removeprefix(prefix)
        if evidence_text.endswith(suffix):
            evidence_text = evidence_text.removesuffix(suffix)
        if evidence_text and evidence_text not in items:
            items.append(evidence_text)
    return items


def _research_evidence_review_menu_options(
    result: ResearchVerificationResult,
) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for index, evidence_text in enumerate(_pending_evidence_review_items(result)[:3]):
        options.extend(
            [
                (f"support:{index}", f"标为支持证据: {evidence_text}"),
                (f"reverse:{index}", f"标为风险/反向待查: {evidence_text}"),
                (f"irrelevant:{index}", f"标为无关/排除: {evidence_text}"),
            ]
        )
    return options


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
