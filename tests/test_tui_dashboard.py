import asyncio
import json
from pathlib import Path

from textual.widgets import Input, OptionList, Static

import lychee_alphadesk.tui.app as tui_app
from lychee_alphadesk.core.action_queue import ActionQueueExecution, ActionQueueItem
from lychee_alphadesk.core.config import set_openai_compatible_llm
from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.live_data import PullResult
from lychee_alphadesk.core.opportunity_radar import (
    OpportunityDrilldownTarget,
    OpportunityRadarReport,
    OpportunitySignal,
)
from lychee_alphadesk.core.research import ResearchDeepenResult, ResearchPacket
from lychee_alphadesk.core.research_db import (
    ResearchEvidenceReviewRecord,
    ResearchMemoRecord,
    ResearchReviewRecord,
    list_research_queue,
)
from lychee_alphadesk.core.research_memo import ResearchMemo, ResearchMemoResult
from lychee_alphadesk.core.research_requests import (
    ProviderBacklogItem,
    ResearchDataRequest,
    ResearchDataRequestExecution,
    ResearchDataRequestFulfillment,
)
from lychee_alphadesk.core.workbench import (
    CandidateCheck,
    PendingEvidenceReviewItem,
    ResearchAnalystReadout,
    ResearchDecisionBoard,
    ResearchEvidenceChange,
    ResearchEvidenceReviewResult,
    ResearchHypothesisPanel,
    ResearchReviewResult,
    ResearchVerificationCheck,
    ResearchVerificationResult,
)
from lychee_alphadesk.tui.app import AlphaDeskApp


def _option_index(menu: OptionList, label: str) -> int:
    labels = [
        str(menu.get_option_at_index(index).prompt)
        for index in range(menu.option_count)
    ]
    return labels.index(label)


def _fake_ready_decision_board() -> ResearchDecisionBoard:
    return ResearchDecisionBoard(
        workflow_state="ready_for_review",
        workflow_label="可进入人工一致性复核",
        primary_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
        decision_rule="支持证据存在，暂无阻塞、反向证据或待补项，证据可以进入人工一致性复核。",
        suggested_verdict="continue_research",
        suggested_verdict_label="继续研究",
        next_steps=["记录继续研究，并进入人工一致性复核。"],
    )


def _fake_needs_more_evidence_decision_board() -> ResearchDecisionBoard:
    return ResearchDecisionBoard(
        workflow_state="direction_review",
        workflow_label="拆分证据方向",
        primary_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
        decision_rule="主题相关性已通过，但部分新闻方向待判定，暂时不能归入支持证据。",
        suggested_verdict="needs_more_evidence",
        suggested_verdict_label="需要补证据",
        next_steps=["把新闻待判定逐条归类为支持、反向或无关。"],
    )


def test_research_verification_text_shows_evidence_change(tmp_path: Path) -> None:
    result = ResearchVerificationResult(
        created_at="2026-07-05T10:00:00+00:00",
        status="pending_review",
        status_label="待人工核验",
        candidate=CandidateCheck(
            display_name="纳斯达克100ETF观察",
            market="US",
            symbol="QQQ",
            proxy_symbols=[],
            evidence_count=2,
            gap_count=0,
            data_gaps=[],
            status="ready",
            explanation="证据已补齐。",
            beginner_question="科技反弹是否独立于大盘？",
            why_it_matters="需要区分主题强弱和大盘 beta。",
            observation_entry="QQQ",
            what_to_check="行情、成交量、相关新闻是否同向。",
            next_step="进入下钻核验。",
            priority="high",
            evidence_status="ready",
            ranking_reason="证据已补齐。",
            evidence_quality="support",
        ),
        packet=None,
        checks=[],
        evidence_board={
            "support": ["行情: QQQ 530.26 USD"],
            "risk": [],
            "missing": [],
        },
        decision_board=_fake_ready_decision_board(),
        evidence_change=ResearchEvidenceChange(
            status="improved",
            status_label="证据增强",
            summary="支持证据增加 1；风险/反向待查无变化；待补证据减少 1。",
            support_delta=1,
            risk_delta=0,
            missing_delta=-1,
            added={"support": ["行情: QQQ 530.26 USD"], "risk": [], "missing": []},
            removed={
                "support": [],
                "risk": [],
                "missing": ["旧核验缺少行情。"],
            },
            previous_artifact_path=str(
                tmp_path / "research" / "research-verification-old.json"
            ),
            previous_created_at="2026-07-04T10:00:00+00:00",
        ),
        analyst_readout=ResearchAnalystReadout(
            title="分析师读数",
            signal="当前信号: 支持证据 1 条，先判断它是否回答同一个研究问题。",
            pressure="反向压力: 当前证据板暂无反向证据或离题噪音。",
            gap="证据缺口: 暂无待补证据，下一步只做一致性复核，不生成买卖结论。",
            evidence_change="证据变化: 证据增强；支持证据增加 1。",
            next_action="工作台动作: 可进入人工一致性复核；记录继续研究。",
            next_command="lychee research memo --symbol QQQ",
        ),
        hypothesis_panel=ResearchHypothesisPanel(
            title="研究假设面板",
            core_question="核心问题: 科技反弹是否独立于大盘？",
            working_hypothesis="工作假设: 如果 QQQ 线索值得继续研究，支持链应持续回答核心问题。",
            support_chain=["行情: QQQ 530.26 USD"],
            counter_chain=["暂无明确反证；继续监控风险栏。"],
            gap_priorities=["暂无待补证据；优先做人工一致性复核。"],
            next_data_requests=["补充 QQQ 与 SPY 的相对强弱和成交量数据。"],
        ),
        conclusion="一致性结论: 待人工核验。",
        next_actions=["记录支持证据、反向证据和仍需补充的数据。"],
        artifact_path=tmp_path / "research" / "research-verification-test.json",
        workbench_result=object(),
    )

    text = tui_app._research_verification_text(result)

    assert "证据变化" in text
    assert "状态: 证据增强" in text
    assert "支持证据增加 1" in text
    assert "证据变化明细" in text
    assert "新增支持证据" in text
    assert "已补掉待补证据" in text
    assert "上一份核验:" in text
    assert "分析师读数" in text
    assert "当前信号: 支持证据 1 条" in text
    assert "工作台动作: 可进入人工一致性复核" in text
    assert "执行命令: lychee research memo --symbol QQQ" in text
    assert "研究假设面板" in text
    assert "核心问题: 科技反弹是否独立于大盘？" in text
    assert "工作假设: 如果 QQQ 线索值得继续研究" in text
    assert "支持链" in text
    assert "反证链" in text
    assert "缺口优先级" in text
    assert "下一批数据请求" in text


def test_research_review_followup_actions_continue_research_offer_memo() -> None:
    assert tui_app._research_review_followup_actions("continue_research") == [
        ("generate_memo", "生成研究备忘录"),
        ("verify_research", "重新下钻核验"),
        ("back_tasks", "返回研究任务列表"),
    ]


def test_dashboard_shows_cached_live_data_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "alpha_vantage",
                "rows": [
                    {
                        "symbol": "AAPL",
                        "date": "2026-07-02",
                        "close": 214.33,
                        "volume": 51230000,
                        "currency": "USD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            dashboard = app.query_one("#dashboard-summary", Static)
            text = str(dashboard.content)
            assert "本地数据缓存" in text
            assert "AAPL" in text
            assert "214.33" in text

    asyncio.run(run_case())


def test_dashboard_has_keyboard_action_menu(tmp_path: Path) -> None:
    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one("#action-menu", OptionList)
            assert menu.highlighted == 0
            assert "今日市场发现" in str(menu.get_option_at_index(0).prompt)
            assert "研究工作台" in str(menu.get_option_at_index(1).prompt)
            assert "机会雷达" in str(menu.get_option_at_index(2).prompt)

            await pilot.press("down")
            await pilot.pause()

            assert menu.highlighted == 1

    asyncio.run(run_case())


def test_dashboard_opportunity_radar_action_shows_discovery_signals(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_build_opportunity_radar(**kwargs: object) -> OpportunityRadarReport:
        assert kwargs["output_dir"] == tmp_path
        return OpportunityRadarReport(
            created_at="2026-07-08T00:00:00+00:00",
            status="ready",
            signals=[
                OpportunitySignal(
                    symbol="STX",
                    market="US",
                    theme="AI 基础设施扩散",
                    score=18,
                    news_count=2,
                    theme_hits=5,
                    volume_rank=2,
                    price_snapshot="130.00 USD | 2026-07-07",
                    why_it_matters="新闻热度和主题命中同时出现。",
                    evidence=["AI storage demand lifts hard-drive suppliers"],
                    next_steps=[
                        'lychee data pull news --symbols STX --query "AI storage" --force'
                    ],
                    drilldown_targets=[
                        OpportunityDrilldownTarget(
                            symbol="NVDA",
                            market="US",
                            display_name="NVIDIA",
                            category="算力芯片锚点",
                            reason="用算力芯片龙头校验 AI 主题是否扩散。",
                            evidence_gap="缺少该标的的主题新闻缓存，需补新闻验证。",
                            next_steps=[
                                (
                                    "lychee data pull news --symbols NVDA "
                                    '--query "AI chip data center" --force'
                                )
                            ],
                        )
                    ],
                )
            ],
            warnings=[],
            disclaimer="非投资建议。机会雷达只用于决定下一步研究什么。",
        )

    monkeypatch.setattr(tui_app, "build_opportunity_radar", fake_build_opportunity_radar)

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            radar_index = _option_index(menu, "机会雷达")
            await pilot.press(*(["down"] * radar_index))
            await pilot.press("enter")
            await pilot.pause()

            text = str(app.query_one("#action-status", Static).content)
            assert "机会雷达" in text
            assert "STX" in text
            assert "AI 基础设施扩散" in text
            assert "新闻热度和主题命中" in text
            assert "lychee data pull news --symbols STX" in text
            assert "可下钻目标" in text
            assert "NVIDIA (NVDA)" in text
            assert "缺少该标的的主题新闻缓存" in text

    asyncio.run(run_case())


def test_dashboard_research_workbench_action_runs_check(
    monkeypatch, tmp_path: Path
) -> None:
    observed_status: list[str] = []
    calls: list[Path] = []
    app_holder: list[AlphaDeskApp] = []

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="纳斯达克100ETF观察",
                market="US",
                symbol="QQQ",
                proxy_symbols=[],
                evidence_count=1,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                why_it_matters="",
                observation_entry="QQQ",
                what_to_check="对比 QQQ 与 SPY。",
                next_step="检查成交量是否配合反弹",
                priority="P2 待增强证据",
                evidence_status="证据 1 条；缺口 0 个",
                ranking_reason="有直接代码且当前没有数据缺口，但证据还需要增强。",
            )
        ]
        beginner_brief = "\n".join(
            [
                "AlphaDesk 研究工作台",
                "状态: 可执行研究 | 可执行 1 | 阻塞 0 | 总任务 1",
                "",
                "今日研究任务",
                "- 纳斯达克100ETF观察 [US] | 入口: QQQ | "
                "优先级: P2 待增强证据 | 证据状态: 证据 1 条；缺口 0 个",
                "  研究问题: 美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                "  关键核验: 对比 QQQ 与 SPY。",
                "  下一步: 检查成交量是否配合反弹",
                "",
                "下一步队列",
                "- 纳斯达克100ETF观察: 检查成交量是否配合反弹",
                "",
                "阻塞任务",
                "- 无。",
            ]
        )

    def fake_run_workbench_check(**kwargs: object) -> FakeWorkbenchResult:
        app = app_holder[0]
        status = app.query_one("#action-status", Static)
        observed_status.append(str(status.content))
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        calls.append(output_dir)
        return FakeWorkbenchResult()

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        fake_run_workbench_check,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        app_holder.append(app)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            text = str(status.content)
            assert calls == [tmp_path]
            assert any("正在运行工作台自检" in item for item in observed_status)
            assert "AlphaDesk 研究工作台" in text
            assert "选择一个研究任务，按 Enter 开始研究" in text
            assert "现在先做" in text
            assert "纳斯达克100ETF观察: 检查成交量是否配合反弹" in text
            assert "给新手的读法" not in text
            task_menu = app.query_one("#research-task-menu", OptionList)
            task_label = str(task_menu.get_option_at_index(0).prompt)
            assert "纳斯达克100ETF观察" in task_label
            assert "QQQ" in task_label
            assert "排序: 有直接代码且当前没有数据缺口" in task_label
            assert not app.query(Input)

    asyncio.run(run_case())


def test_dashboard_portfolio_check_action_is_read_only(tmp_path: Path) -> None:
    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            portfolio_index = _option_index(menu, "检查模拟组合")
            await pilot.press(*(["down"] * portfolio_index))
            await pilot.press("enter")
            await pilot.pause()

            text = str(app.query_one("#action-status", Static).content)
            assert "模拟组合检查" in text
            assert "政策通过，等待行情" in text
            assert "不是估值、交易或投资建议" in text
            assert list((tmp_path / "research").glob("portfolio-check-*.json"))

    asyncio.run(run_case())


def test_dashboard_research_review_history_action_lists_records(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_list_research_reviews(**kwargs: object) -> list[ResearchReviewRecord]:
        assert kwargs["output_dir"] == tmp_path
        return [
            ResearchReviewRecord(
                review_id="research-review:2026-07-05T10:00:00+00:00",
                created_at="2026-07-05T10:00:00+00:00",
                display_name="Seagate",
                symbol="STX",
                market="US",
                verdict="pause_watch",
                verdict_label="暂停观察",
                note="等待更多订单和财报证据。",
                support_count=4,
                risk_count=1,
                missing_count=0,
                review_path=str(tmp_path / "research" / "research-review-test.json"),
                verification_path=str(
                    tmp_path / "research" / "research-verification-test.json"
                ),
                payload={},
            )
        ]

    monkeypatch.setattr(
        tui_app,
        "list_research_reviews",
        fake_list_research_reviews,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            history_index = _option_index(menu, "研究复核历史")
            await pilot.press(*(["down"] * history_index))
            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            text = str(status.content)
            assert "研究复核历史" in text
            assert "Seagate (STX) [US]" in text
            assert "暂停观察" in text
            assert "等待更多订单和财报证据。" in text
            assert "证据: 支持 4 | 风险 1 | 待补 0" in text
            assert "research-review-test.json" in text
            assert "不是买卖建议" in text

    asyncio.run(run_case())


def test_dashboard_research_memo_history_action_lists_records(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_list_research_memos(**kwargs: object) -> list[ResearchMemoRecord]:
        assert kwargs["output_dir"] == tmp_path
        return [
            ResearchMemoRecord(
                memo_id="research-memo:2026-07-05T10:02:00+00:00",
                created_at="2026-07-05T10:02:00+00:00",
                display_name="Seagate",
                symbol="STX",
                market="US",
                confidence="medium",
                summary="AI 存储需求线索需要继续核验订单和利润证据。",
                support_count=1,
                skeptic_count=1,
                missing_count=1,
                next_step_count=1,
                memo_path=str(tmp_path / "research" / "research-memo-test.json"),
                verification_path=str(
                    tmp_path / "research" / "research-verification-test.json"
                ),
                payload={},
            )
        ]

    monkeypatch.setattr(
        tui_app,
        "list_research_memos",
        fake_list_research_memos,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            history_index = _option_index(menu, "研究备忘录历史")
            await pilot.press(*(["down"] * history_index))
            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            text = str(status.content)
            assert "研究备忘录历史" in text
            assert "Seagate" in text
            assert "STX" in text
            assert "AI 存储需求线索" in text
            assert "支持 1 | 反方 1 | 待补 1 | 下一步 1" in text
            assert "research-memo-test.json" in text
            assert "研究备忘录历史不是买卖建议" in text

    asyncio.run(run_case())


def test_dashboard_research_data_requests_action_lists_actionable_requests(
    monkeypatch, tmp_path: Path
) -> None:
    request = ResearchDataRequest(
        request_id="research-memo:test:data-request:1",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="low",
        request_text="请补齐 QQQ 的基金资料：跟踪指数、费用率、成分摘要和来源 URL。",
        suggested_commands=[
            "lychee data guide fund --symbol QQQ --name 'Invesco QQQ Trust' --market US",
            "lychee research verify --symbol QQQ",
        ],
        memo_path=str(tmp_path / "research" / "research-memo-test.json"),
        verification_path=str(tmp_path / "research" / "research-verification-test.json"),
    )

    def fake_list_research_data_requests(**kwargs: object) -> list[ResearchDataRequest]:
        assert kwargs["output_dir"] == tmp_path
        return [request]

    fulfill_calls: list[dict[str, object]] = []

    def fake_fulfill_research_data_request(
        output_dir: Path,
        **kwargs: object,
    ) -> ResearchDataRequestFulfillment:
        fulfill_calls.append({"output_dir": output_dir, **kwargs})
        return ResearchDataRequestFulfillment(
            request=request,
            executions=[
                ResearchDataRequestExecution(
                    action_type="fund_metadata_guide",
                    status="completed",
                    command="lychee data guide fund --symbol QQQ",
                    count=1,
                    output_path=tmp_path / "data" / "fund-metadata-guide-QQQ.json",
                    message="已生成基金资料模板；填写并导入后再重新核验。",
                ),
                ResearchDataRequestExecution(
                    action_type="verify",
                    status="skipped",
                    command="lychee research verify --symbol QQQ",
                    count=0,
                    output_path=None,
                    message="等待人工补来源或填写模板后再重新核验。",
                ),
            ],
        )

    monkeypatch.setattr(
        tui_app,
        "list_research_data_requests",
        fake_list_research_data_requests,
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "fulfill_research_data_request",
        fake_fulfill_research_data_request,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            request_index = _option_index(menu, "研究数据请求")
            await pilot.press(*(["down"] * request_index))
            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            text = str(status.content)
            assert "研究数据请求" in text
            assert "Invesco QQQ Trust" in text
            assert "请补齐 QQQ 的基金资料" in text
            assert "lychee data guide fund --symbol QQQ" in text
            assert "lychee research verify --symbol QQQ" in text
            assert "数据请求队列只用于补证据，不是买卖建议" in text

            request_menu = app.query_one("#research-data-request-menu", OptionList)
            assert "执行 1." in str(request_menu.get_option_at_index(0).prompt)
            await pilot.press("enter")
            await pilot.pause()

            assert fulfill_calls == [
                {
                    "output_dir": tmp_path,
                    "request_id": "research-memo:test:data-request:1",
                }
            ]
            result_text = str(app.query_one("#action-status", Static).content)
            assert "研究数据请求执行结果" in result_text
            assert "基金资料模板: 已完成" in result_text
            assert "下钻核验: 跳过" in result_text
            assert "数据请求执行只补证据，不是买卖建议" in result_text

    asyncio.run(run_case())


def test_dashboard_next_actions_action_lists_unified_queue(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_build_action_queue(**kwargs: object) -> list[ActionQueueItem]:
        assert kwargs["output_dir"] == tmp_path
        return [
            ActionQueueItem(
                priority=1,
                area="待判定证据",
                title="复核 Seagate 的待判定新闻",
                detail="判断这条新闻是支持、反向还是无关。",
                command="lychee research evidence-review --symbol STX --verdict support",
                source="research-verification-test.json",
            ),
            ActionQueueItem(
                priority=2,
                area="机会雷达",
                title="下钻 NVIDIA: AI 基础设施扩散",
                detail="来自 QQQ 雷达信号；缺少该标的的主题新闻缓存。",
                command=(
                    "lychee data pull news --symbols NVDA "
                    '--query "AI chip data center" --force'
                ),
                source="opportunity-radar",
            )
        ]

    monkeypatch.setattr(
        tui_app,
        "build_action_queue",
        fake_build_action_queue,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            next_index = _option_index(menu, "下一步行动队列")
            await pilot.press(*(["down"] * next_index))
            await pilot.press("enter")
            await pilot.pause()

            text = str(app.query_one("#action-status", Static).content)
            assert "下一步行动队列" in text
            assert "待判定证据" in text
            assert "复核 Seagate 的待判定新闻" in text
            assert "lychee research evidence-review --symbol STX" in text
            assert "机会雷达" in text
            assert "下钻 NVIDIA" in text
            assert "lychee data pull news --symbols NVDA" in text
            assert "行动队列只推进研究流程，不是买卖建议" in text
            queue_menu = app.query_one("#next-action-menu", OptionList)
            assert "执行: 复核 Seagate 的待判定新闻" in str(
                queue_menu.get_option_at_index(0).prompt
            )
            assert "执行: 下钻 NVIDIA: AI 基础设施扩散" in str(
                queue_menu.get_option_at_index(1).prompt
            )

    asyncio.run(run_case())


def test_dashboard_next_actions_can_execute_whitelisted_action(
    monkeypatch, tmp_path: Path
) -> None:
    item = ActionQueueItem(
        priority=2,
        area="机会雷达",
        title="下钻 NVIDIA: AI 基础设施扩散",
        detail="来自 QQQ 雷达信号；缺少该标的的主题新闻缓存。",
        command='lychee data pull news --symbols NVDA --query "AI chip data center" --force',
        source="opportunity-radar",
    )
    calls: list[dict[str, object]] = []

    def fake_build_action_queue(**kwargs: object) -> list[ActionQueueItem]:
        return [item]

    def fake_execute_action_queue_item(
        output_dir: Path,
        **kwargs: object,
    ) -> ActionQueueExecution:
        calls.append({"output_dir": output_dir, **kwargs})
        return ActionQueueExecution(
            item=item,
            status="completed",
            message="已执行机会雷达补新闻动作。",
            count=3,
            output_path=tmp_path / "data" / "news-events.json",
            next_command="lychee research run --symbol NVDA --force",
            warnings=[],
        )

    monkeypatch.setattr(tui_app, "build_action_queue", fake_build_action_queue)
    monkeypatch.setattr(
        tui_app,
        "execute_action_queue_item",
        fake_execute_action_queue_item,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            next_index = _option_index(menu, "下一步行动队列")
            await pilot.press(*(["down"] * next_index))
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()

            assert calls
            assert calls[0]["output_dir"] == tmp_path
            assert calls[0]["action_index"] == 1
            assert calls[0]["limit"] == 12
            assert calls[0]["force"] is False
            result_text = str(app.query_one("#action-status", Static).content)
            assert "下一步行动执行结果" in result_text
            assert "下钻 NVIDIA: AI 基础设施扩散" in result_text
            assert "状态: completed" in result_text
            assert "新闻行数: 3" in result_text
            assert "lychee research run --symbol NVDA --force" in result_text
            assert "自动行动只补研究证据，不是买卖建议" in result_text

    asyncio.run(run_case())


def test_dashboard_next_actions_can_execute_pending_evidence_review(
    monkeypatch, tmp_path: Path
) -> None:
    item = ActionQueueItem(
        priority=1,
        area="待判定证据",
        title="复核 NVIDIA 的待判定证据",
        detail="AI 算力需求是否扩散？ | 系统建议: 无关/排除",
        command=(
            "lychee research evidence-review --symbol NVDA "
            '--text "Perplexity says it plans to use Nvidia" '
            "--verdict irrelevant"
        ),
        source="research-verification-test.json",
    )

    def fake_build_action_queue(**kwargs: object) -> list[ActionQueueItem]:
        return [item]

    def fake_execute_action_queue_item(
        output_dir: Path,
        **kwargs: object,
    ) -> ActionQueueExecution:
        return ActionQueueExecution(
            item=item,
            status="completed",
            message="已记录证据复核: 无关/排除。",
            count=1,
            output_path=tmp_path / "research" / "research-evidence-review-nvda.json",
            next_command="lychee research verify --symbol NVDA",
            warnings=[],
        )

    monkeypatch.setattr(tui_app, "build_action_queue", fake_build_action_queue)
    monkeypatch.setattr(
        tui_app,
        "execute_action_queue_item",
        fake_execute_action_queue_item,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            next_index = _option_index(menu, "下一步行动队列")
            await pilot.press(*(["down"] * next_index))
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()

            result_text = str(app.query_one("#action-status", Static).content)
            assert "下一步行动执行结果" in result_text
            assert "复核 NVIDIA 的待判定证据" in result_text
            assert "已记录证据复核: 无关/排除" in result_text
            assert "处理数量: 1" in result_text
            assert "lychee research verify --symbol NVDA" in result_text

    asyncio.run(run_case())


def test_dashboard_next_actions_no_data_does_not_show_verification_followup(
    monkeypatch, tmp_path: Path
) -> None:
    item = ActionQueueItem(
        priority=2,
        area="机会雷达",
        title="下钻 Alibaba: AI 基础设施扩散",
        detail="来自 NVDA 雷达信号；缺少该标的的主题新闻缓存。",
        command=(
            "lychee data pull news --symbols BABA "
            '--query "AI cloud revenue Alibaba data center" --force'
        ),
        source="opportunity-radar",
    )

    def fake_build_action_queue(**kwargs: object) -> list[ActionQueueItem]:
        return [item]

    def fake_execute_action_queue_item(
        output_dir: Path,
        **kwargs: object,
    ) -> ActionQueueExecution:
        return ActionQueueExecution(
            item=item,
            status="no-data",
            message="没有获取到匹配新闻，暂不能进入研究核验。",
            count=0,
            output_path=tmp_path / "data" / "news-events.json",
            next_command="",
            warnings=["新闻缓存记录为空且仍在保质期内，跳过重复刷新。"],
        )

    monkeypatch.setattr(tui_app, "build_action_queue", fake_build_action_queue)
    monkeypatch.setattr(
        tui_app,
        "execute_action_queue_item",
        fake_execute_action_queue_item,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            next_index = _option_index(menu, "下一步行动队列")
            await pilot.press(*(["down"] * next_index))
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()

            result_text = str(app.query_one("#action-status", Static).content)
            assert "状态: no-data" in result_text
            assert "没有获取到匹配新闻" in result_text
            assert "下一步核验" not in result_text
            assert "自动行动只补研究证据，不是买卖建议" in result_text

    asyncio.run(run_case())


def test_dashboard_provider_backlog_action_lists_provider_gaps(
    monkeypatch, tmp_path: Path
) -> None:
    backlog_item = ProviderBacklogItem(
        request_id="research-memo:test:data-request:1",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="low",
        request_text="请补充纳斯达克 100 成分股上涨家数和等权指数对比。",
        data_domain="市场广度",
        plugin_type="market_breadth",
        coverage_gap="当前 provider 只能补行情、新闻、公告和基金资料，缺少市场广度数据。",
        suggested_provider_examples=[
            "指数成分数据源",
            "等权指数或市场广度数据源",
        ],
        suggested_commands=[
            "lychee data set metric --symbol QQQ --domain market_breadth "
            '--name "<填入指标名称>" --value "<填入核验后的读数>" '
            '--as-of YYYY-MM-DD --source-url "<资料来源URL>"'
        ],
        next_step="接入可审计的市场广度 provider 后，再重新运行研究数据请求。",
        memo_path=str(tmp_path / "research" / "research-memo-test.json"),
        verification_path=str(tmp_path / "research" / "research-verification-test.json"),
    )

    def fake_list_provider_backlog_items(**kwargs: object) -> list[ProviderBacklogItem]:
        assert kwargs["output_dir"] == tmp_path
        return [backlog_item]

    monkeypatch.setattr(
        tui_app,
        "list_provider_backlog_items",
        fake_list_provider_backlog_items,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            backlog_index = _option_index(menu, "数据源缺口队列")
            await pilot.press(*(["down"] * backlog_index))
            await pilot.press("enter")
            await pilot.pause()

            text = str(app.query_one("#action-status", Static).content)
            assert "数据源缺口队列" in text
            assert "Invesco QQQ Trust" in text
            assert "市场广度" in text
            assert "market_breadth" in text
            assert "指数成分数据源" in text
            assert "lychee data set metric --symbol QQQ --domain market_breadth" in text
            assert "接入可审计的市场广度 provider" in text
            assert "数据源缺口队列只用于规划补数据能力，不是买卖建议" in text

    asyncio.run(run_case())


def test_dashboard_research_evidence_review_history_action_lists_records(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_list_research_evidence_reviews(
        **kwargs: object,
    ) -> list[ResearchEvidenceReviewRecord]:
        assert kwargs["output_dir"] == tmp_path
        return [
            ResearchEvidenceReviewRecord(
                review_id="research-evidence-review:2026-07-05T10:03:00+00:00",
                created_at="2026-07-05T10:03:00+00:00",
                display_name="Seagate",
                symbol="STX",
                market="US",
                evidence_text="STX hard drive demand update",
                verdict="reverse",
                verdict_label="风险/反向待查",
                note="这条新闻更像需求放缓风险，需要排除乐观解释。",
                review_path=str(
                    tmp_path / "research" / "research-evidence-review-test.json"
                ),
                payload={},
            )
        ]

    monkeypatch.setattr(
        tui_app,
        "list_research_evidence_reviews",
        fake_list_research_evidence_reviews,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            history_index = _option_index(menu, "证据复核历史")
            await pilot.press(*(["down"] * history_index))
            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            text = str(status.content)
            assert "证据复核历史" in text
            assert "Seagate (STX) [US]" in text
            assert "STX hard drive demand update" in text
            assert "风险/反向待查" in text
            assert "这条新闻更像需求放缓风险" in text
            assert "research-evidence-review-test.json" in text
            assert "单条证据复核历史不是买卖建议" in text

    asyncio.run(run_case())


def test_dashboard_pending_evidence_action_lists_review_queue(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_list_pending_evidence_reviews(**kwargs: object) -> list[PendingEvidenceReviewItem]:
        assert kwargs["output_dir"] == tmp_path
        return [
            PendingEvidenceReviewItem(
                created_at="2026-07-06T10:00:00+00:00",
                display_name="Invesco QQQ Trust",
                symbol="QQQ",
                market="US",
                primary_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                evidence_text="QQQ tech rebound headline",
                raw_evidence="新闻待判定: QQQ tech rebound headline 命中主题但方向未明。",
                suggested_verdict="support",
                suggested_verdict_label="支持证据",
                suggested_reason="系统检测到反弹语义，建议先按支持证据处理。",
                artifact_path=str(
                    tmp_path / "research" / "research-verification-test.json"
                ),
                review_command=(
                    'lychee research evidence-review --symbol QQQ --text '
                    '"QQQ tech rebound headline" '
                    '--verdict support --note "系统检测到反弹语义，建议先按支持证据处理。"'
                ),
            )
        ]

    monkeypatch.setattr(
        tui_app,
        "list_pending_evidence_reviews",
        fake_list_pending_evidence_reviews,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            queue_index = _option_index(menu, "待判定证据队列")
            await pilot.press(*(["down"] * queue_index))
            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            text = str(status.content)
            assert "待判定证据队列" in text
            assert "Invesco QQQ Trust (QQQ) [US]" in text
            assert "美股科技股现在是独立主线" in text
            assert "QQQ tech rebound headline" in text
            assert "系统建议: 支持证据" in text
            assert "lychee research evidence-review --symbol QQQ" in text
            assert "--verdict support" in text
            assert "待判定证据队列不是买卖建议" in text

    asyncio.run(run_case())


def test_dashboard_pending_evidence_queue_can_record_direction(
    monkeypatch, tmp_path: Path
) -> None:
    evidence_review_calls: list[dict[str, object]] = []

    def pending_item() -> PendingEvidenceReviewItem:
        return PendingEvidenceReviewItem(
            created_at="2026-07-06T10:00:00+00:00",
            display_name="Invesco QQQ Trust",
            symbol="QQQ",
            market="US",
            primary_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
            evidence_text="QQQ tech rebound headline",
            raw_evidence="新闻待判定: QQQ tech rebound headline 命中主题但方向未明。",
            suggested_verdict="support",
            suggested_verdict_label="支持证据",
            suggested_reason="系统检测到反弹语义，建议先按支持证据处理。",
            artifact_path=str(tmp_path / "research" / "research-verification-test.json"),
            review_command=(
                'lychee research evidence-review --symbol QQQ --text '
                '"QQQ tech rebound headline" '
                '--verdict support --note "系统检测到反弹语义，建议先按支持证据处理。"'
            ),
        )

    def fake_list_pending_evidence_reviews(
        **kwargs: object,
    ) -> list[PendingEvidenceReviewItem]:
        assert kwargs["output_dir"] == tmp_path
        return [] if evidence_review_calls else [pending_item()]

    def fake_record_research_evidence_review(
        **kwargs: object,
    ) -> ResearchEvidenceReviewResult:
        evidence_review_calls.append(kwargs)
        candidate = CandidateCheck(
            display_name="Invesco QQQ Trust",
            market="US",
            symbol="QQQ",
            proxy_symbols=[],
            evidence_count=1,
            gap_count=0,
            data_gaps=[],
            status="ready",
            explanation="",
            beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
            why_it_matters="",
            observation_entry="QQQ",
            what_to_check="对比 QQQ 与 SPY。",
            next_step="复核证据方向",
            priority="P2",
            evidence_status="待判定 1",
            evidence_quality="needs_review",
        )
        return ResearchEvidenceReviewResult(
            created_at="2026-07-06T10:01:00+00:00",
            verdict=str(kwargs["verdict"]),
            verdict_label="支持证据",
            evidence_text=str(kwargs["evidence_text"]),
            note=str(kwargs["note"]),
            candidate=candidate,
            artifact_path=tmp_path / "research" / "research-evidence-review-test.json",
            db_path=tmp_path / "research.sqlite3",
        )

    monkeypatch.setattr(
        tui_app,
        "list_pending_evidence_reviews",
        fake_list_pending_evidence_reviews,
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "record_research_evidence_review",
        fake_record_research_evidence_review,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            queue_index = _option_index(menu, "待判定证据队列")
            await pilot.press(*(["down"] * queue_index))
            await pilot.press("enter")
            await pilot.pause()

            queue_menu = app.query_one("#pending-evidence-menu", OptionList)
            assert "QQQ tech rebound headline" in str(
                queue_menu.get_option_at_index(0).prompt
            )
            await pilot.press("enter")
            await pilot.pause()

            detail_text = str(app.query_one("#action-status", Static).content)
            assert "待判定证据详情" in detail_text
            assert "QQQ tech rebound headline" in detail_text
            assert "系统建议: 支持证据" in detail_text

            action_menu = app.query_one("#pending-evidence-action-menu", OptionList)
            support_index = _option_index(
                action_menu,
                "按系统建议记录: 标为支持证据",
            )
            await pilot.press(*(["down"] * support_index))
            await pilot.press("enter")
            await pilot.pause()

            assert evidence_review_calls == [
                {
                    "output_dir": tmp_path,
                    "symbol": "QQQ",
                    "name": None,
                    "evidence_text": "QQQ tech rebound headline",
                    "verdict": "support",
                    "note": "TUI 待判定证据队列: 支持证据",
                }
            ]
            refreshed_text = str(app.query_one("#action-status", Static).content)
            assert "证据复核已记录" in refreshed_text
            assert "暂无待判定证据" in refreshed_text

    asyncio.run(run_case())


def test_dashboard_pending_evidence_queue_can_rerun_verification_after_recording(
    monkeypatch, tmp_path: Path
) -> None:
    evidence_review_calls: list[dict[str, object]] = []
    verify_calls: list[dict[str, object]] = []

    candidate = CandidateCheck(
        display_name="Invesco QQQ Trust",
        market="US",
        symbol="QQQ",
        proxy_symbols=[],
        evidence_count=1,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="",
        beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
        why_it_matters="",
        observation_entry="QQQ",
        what_to_check="对比 QQQ 与 SPY。",
        next_step="复核证据方向",
        priority="P2",
        evidence_status="待判定 1",
        evidence_quality="needs_review",
    )

    def pending_item() -> PendingEvidenceReviewItem:
        return PendingEvidenceReviewItem(
            created_at="2026-07-06T10:00:00+00:00",
            display_name="Invesco QQQ Trust",
            symbol="QQQ",
            market="US",
            primary_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
            evidence_text="QQQ tech rebound headline",
            raw_evidence="新闻待判定: QQQ tech rebound headline 命中主题但方向未明。",
            suggested_verdict="support",
            suggested_verdict_label="支持证据",
            suggested_reason="系统检测到反弹语义，建议先按支持证据处理。",
            artifact_path=str(tmp_path / "research" / "research-verification-test.json"),
            review_command=(
                'lychee research evidence-review --symbol QQQ --text '
                '"QQQ tech rebound headline" '
                '--verdict support --note "系统检测到反弹语义，建议先按支持证据处理。"'
            ),
        )

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [candidate]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-06T10:02:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:pending-evidence",
                    candidate_id=1,
                    created_at="2026-07-06T10:02:00+00:00",
                    display_name="Invesco QQQ Trust",
                    symbol="QQQ",
                    market="US",
                    packet={
                        "candidate": {"asset_type": "ETF"},
                        "evidence": [],
                        "local_data": {
                            "price": {},
                            "related_news": [],
                            "filings": [],
                            "symbol_mapping": [],
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    def fake_list_pending_evidence_reviews(
        **kwargs: object,
    ) -> list[PendingEvidenceReviewItem]:
        assert kwargs["output_dir"] == tmp_path
        return [] if evidence_review_calls else [pending_item()]

    def fake_record_research_evidence_review(
        **kwargs: object,
    ) -> ResearchEvidenceReviewResult:
        evidence_review_calls.append(kwargs)
        return ResearchEvidenceReviewResult(
            created_at="2026-07-06T10:01:00+00:00",
            verdict=str(kwargs["verdict"]),
            verdict_label="支持证据",
            evidence_text=str(kwargs["evidence_text"]),
            note=str(kwargs["note"]),
            candidate=candidate,
            artifact_path=tmp_path / "research" / "research-evidence-review-test.json",
            db_path=tmp_path / "research.sqlite3",
        )

    def fake_verify_research_task(**kwargs: object) -> ResearchVerificationResult:
        verify_calls.append(kwargs)
        packet = FakeWorkbenchResult.deepen_result.packets[0]
        return ResearchVerificationResult(
            created_at="2026-07-06T10:02:00+00:00",
            status="pending_review",
            status_label="待人工核验",
            candidate=candidate,
            packet=packet,
            checks=[
                ResearchVerificationCheck(
                    name="证据方向核验",
                    status="pass",
                    detail="相关新闻方向初步支持研究问题: 支持 1 条；反向 0 条；方向待判定 0 条。",
                )
            ],
            evidence_board={
                "support": [
                    "行情: QQQ 530.26 USD",
                    "新闻: QQQ tech rebound headline",
                ],
                "risk": [],
                "missing": [],
            },
            decision_board=_fake_ready_decision_board(),
            conclusion="一致性结论: 待人工核验。",
            next_actions=["记录支持证据、反向证据和仍需补充的数据。"],
            artifact_path=tmp_path / "research" / "research-verification-test.json",
            workbench_result=FakeWorkbenchResult(),
        )

    monkeypatch.setattr(
        tui_app,
        "list_pending_evidence_reviews",
        fake_list_pending_evidence_reviews,
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "record_research_evidence_review",
        fake_record_research_evidence_review,
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "verify_research_task",
        fake_verify_research_task,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            queue_index = _option_index(menu, "待判定证据队列")
            await pilot.press(*(["down"] * queue_index))
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()

            action_menu = app.query_one("#pending-evidence-action-menu", OptionList)
            support_index = _option_index(
                action_menu,
                "按系统建议记录: 标为支持证据",
            )
            await pilot.press(*(["down"] * support_index))
            await pilot.press("enter")
            await pilot.pause()

            post_review_text = str(app.query_one("#action-status", Static).content)
            assert "证据复核已记录" in post_review_text
            assert "暂无待判定证据" in post_review_text

            followup_menu = app.query_one("#pending-evidence-followup-menu", OptionList)
            rerun_index = _option_index(followup_menu, "重新下钻核验")
            await pilot.press(*(["down"] * rerun_index))
            await pilot.press("enter")
            await pilot.pause()

            assert verify_calls == [
                {
                    "output_dir": tmp_path,
                    "symbol": "QQQ",
                    "name": None,
                }
            ]
            rerun_text = str(app.query_one("#action-status", Static).content)
            assert "下钻核验结果" in rerun_text
            assert "证据板" in rerun_text
            assert "新闻: QQQ tech rebound headline" in rerun_text
            current_risk_section = rerun_text.split("风险/反向待查", 1)[1].split(
                "待补证据", 1
            )[0]
            assert "新闻待判定: QQQ tech rebound headline" not in current_risk_section

    asyncio.run(run_case())


def test_dashboard_research_task_selection_opens_research_result_workbench(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="纳斯达克100ETF观察",
                market="US",
                symbol="QQQ",
                proxy_symbols=[],
                evidence_count=1,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                why_it_matters="",
                observation_entry="QQQ",
                what_to_check="对比 QQQ 与 SPY。",
                next_step="检查成交量是否配合反弹",
                priority="P2 待增强证据",
                evidence_status="证据 1 条；缺口 0 个",
            )
        ]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:1",
                    candidate_id=1,
                    created_at="2026-07-05T10:00:00+00:00",
                    display_name="纳斯达克100ETF观察",
                    symbol="QQQ",
                    market="US",
                    packet={
                        "evidence": [
                            {
                                "headline": "Tech shares rebound with QQQ volume rising",
                                "provider": "newsapi",
                                "timestamp": "2026-07-05T09:00:00Z",
                                "source_url": "https://example.com/qqq-news",
                            }
                        ],
                        "local_data": {
                            "price": {
                                "symbol": "QQQ",
                                "date": "2026-07-05",
                                "close": 530.2578125,
                                "volume": 42000000,
                                "currency": "USD",
                            },
                            "related_news": [
                                {
                                    "headline": "Nasdaq outperforms S&P 500",
                                    "summary": "Technology shares led the session.",
                                    "source_url": "https://example.com/nasdaq",
                                }
                            ],
                            "filings": [],
                            "symbol_mapping": [],
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        lambda **kwargs: FakeWorkbenchResult(),
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            detail = app.query_one("#action-status", Static)
            text = str(detail.content)
            assert "研究任务面板" in text
            assert "纳斯达克100ETF观察" in text
            assert "入口: QQQ" in text
            assert "本次研究要解决的问题" in text
            assert "研究启动" in text
            assert "第一步: lychee research verify --symbol QQQ" in text
            assert "看证据板: 支持证据 / 风险或反向待查 / 待补证据" in text
            assert "信号读数:" in text
            assert "证据矩阵" in text
            assert "可执行动作" in text
            assert "当前研究结论:" not in text
            assert "已采集证据" in text
            assert "Tech shares rebound with QQQ volume rising" in text
            assert "行情: QQQ 530.26 USD" in text
            assert "相关新闻" in text
            assert "Nasdaq outperforms S&P 500" in text
            assert "数据完整性: 无" in text
            assert "研究缺口: 无" in text
            assert "下一步动作:" in text
            assert "对比 QQQ 与 SPY。" in text
            action_menu = app.query_one("#research-detail-action-menu", OptionList)
            first_action = str(action_menu.get_option_at_index(0).prompt)
            second_action = str(action_menu.get_option_at_index(1).prompt)
            assert "开始/继续研究" in first_action
            assert "刷新行情" in second_action
            assert "lychee data pull market --symbols QQQ --provider auto --force" in text
            assert not app.query("#research-task-menu")

    asyncio.run(run_case())


def test_dashboard_research_task_action_refreshes_market_data(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    class FakePullResult:
        domain = "market"
        provider = "auto"
        count = 1
        output_path = tmp_path / "data" / "market-prices.json"
        warnings: list[str] = []

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="纳斯达克100ETF观察",
                market="US",
                symbol="QQQ",
                proxy_symbols=[],
                evidence_count=1,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                why_it_matters="",
                observation_entry="QQQ",
                what_to_check="对比 QQQ 与 SPY。",
                next_step="检查成交量是否配合反弹",
                priority="P2 待增强证据",
                evidence_status="证据 1 条；缺口 0 个",
            )
        ]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:1",
                    candidate_id=1,
                    created_at="2026-07-05T10:00:00+00:00",
                    display_name="纳斯达克100ETF观察",
                    symbol="QQQ",
                    market="US",
                    packet={
                        "candidate": {"asset_type": "ETF"},
                        "evidence": [],
                        "local_data": {
                            "price": {},
                            "related_news": [],
                            "filings": [],
                            "symbol_mapping": [],
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    def fake_pull_market_prices(**kwargs: object) -> FakePullResult:
        calls.append(kwargs)
        return FakePullResult()

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        lambda **kwargs: FakeWorkbenchResult(),
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "pull_market_prices",
        fake_pull_market_prices,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert calls == [
                {
                    "symbols": ["QQQ"],
                    "output_dir": tmp_path,
                    "provider_id": "auto",
                    "force": True,
                }
            ]
            detail = app.query_one("#action-status", Static)
            text = str(detail.content)
            assert "已执行: 刷新行情" in text
            assert "返回行数: 1" in text

    asyncio.run(run_case())


def test_dashboard_research_task_action_runs_drilldown_verification(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="纳斯达克100ETF观察",
                market="US",
                symbol="QQQ",
                proxy_symbols=[],
                evidence_count=1,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                why_it_matters="",
                observation_entry="QQQ",
                what_to_check="对比 QQQ 与 SPY。",
                next_step="检查成交量是否配合反弹",
                priority="P2 待增强证据",
                evidence_status="证据 1 条；缺口 0 个",
            )
        ]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:1",
                    candidate_id=1,
                    created_at="2026-07-05T10:00:00+00:00",
                    display_name="纳斯达克100ETF观察",
                    symbol="QQQ",
                    market="US",
                    packet={
                        "candidate": {"asset_type": "ETF"},
                        "evidence": [],
                        "local_data": {
                            "price": {},
                            "related_news": [],
                            "filings": [],
                            "symbol_mapping": [],
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    def fake_verify_research_task(**kwargs: object) -> ResearchVerificationResult:
        calls.append(kwargs)
        candidate = FakeWorkbenchResult.candidates[0]
        packet = FakeWorkbenchResult.deepen_result.packets[0]
        return ResearchVerificationResult(
            created_at="2026-07-05T10:00:00+00:00",
            status="pending_review",
            status_label="待人工核验",
            candidate=candidate,
            packet=packet,
            checks=[
                ResearchVerificationCheck(
                    name="行情核验",
                    status="pass",
                    detail="已有 QQQ 行情。",
                )
            ],
            evidence_board={
                "support": ["行情: QQQ 530.26 USD"],
                "risk": ["一致性核验: 待人工核验行情、成交量、新闻是否同向。"],
                "missing": [],
            },
            decision_board=_fake_ready_decision_board(),
            conclusion="一致性结论: 待人工核验。",
            next_actions=["记录支持证据、反向证据和仍需补充的数据。"],
            artifact_path=tmp_path / "research" / "research-verification-test.json",
            workbench_result=FakeWorkbenchResult(),
        )

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        lambda **kwargs: FakeWorkbenchResult(),
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "verify_research_task",
        fake_verify_research_task,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            action_menu = app.query_one("#research-detail-action-menu", OptionList)
            start_action = str(action_menu.get_option_at_index(0).prompt)
            assert "开始/继续研究" in start_action
            await pilot.press("enter")
            await pilot.pause()

            assert calls == [
                {
                    "output_dir": tmp_path,
                    "symbol": "QQQ",
                    "name": None,
                }
            ]
            detail = app.query_one("#action-status", Static)
            text = str(detail.content)
            assert "下钻核验结果" in text
            assert "证据板" in text
            assert "支持证据" in text
            assert "风险/反向待查" in text
            assert "待补证据" in text
            assert "研究决策板" in text
            assert "建议记录: continue_research" in text

    asyncio.run(run_case())


def test_dashboard_research_task_can_open_fund_metadata_guide(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="E Fund HKEX Tech 100 ETF",
                market="HK",
                symbol="3456.HK",
                proxy_symbols=[],
                evidence_count=1,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="港股科技 ETF 是否能代表这条研究线索？",
                why_it_matters="",
                observation_entry="3456.HK",
                what_to_check="先核对 ETF 成分、跟踪指数、费用和来源。",
                next_step="先生成 ETF/基金资料补齐向导；补齐后重新运行下钻核验。",
                priority="P2 待补基金资料",
                evidence_status="证据 1 条；缺口 0 个",
                next_command=(
                    'lychee data guide fund --symbol 3456.HK '
                    '--name "E Fund HKEX Tech 100 ETF" --market HK'
                ),
            )
        ]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:fund-guide",
                    candidate_id=1,
                    created_at="2026-07-05T10:00:00+00:00",
                    display_name="E Fund HKEX Tech 100 ETF",
                    symbol="3456.HK",
                    market="HK",
                    packet={
                        "candidate": {"asset_type": "ETF"},
                        "evidence": [],
                        "local_data": {
                            "price": {},
                            "related_news": [],
                            "filings": [],
                            "symbol_mapping": [],
                            "fund_metadata": None,
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        lambda **kwargs: FakeWorkbenchResult(),
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            action_menu = app.query_one("#research-detail-action-menu", OptionList)
            guide_index = _option_index(action_menu, "补基金资料向导")
            assert guide_index > 0

            for _ in range(guide_index):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            guide_path = tmp_path / "data" / "fund-metadata-guide-3456.HK.json"
            assert guide_path.exists()
            text = str(app.query_one("#action-status", Static).content)
            assert "基金资料补齐向导" in text
            assert "E Fund HKEX Tech 100 ETF (3456.HK) [HK]" in text
            assert "先查这些资料" in text
            assert "香港交易所 ETF 页面" in text
            assert "lychee data set fund --symbol 3456.HK" in text
            followup_menu = app.query_one("#research-detail-action-menu", OptionList)
            import_index = _option_index(followup_menu, "导入已填写模板")
            guide = json.loads(guide_path.read_text("utf-8"))
            guide["template"].update(
                {
                    "tracking_index": "HKEX Tech 100 Index",
                    "expense_ratio": "0.99%",
                    "holdings_summary": "前十大成分覆盖港股科技龙头",
                    "source_url": "https://example.com/3456",
                    "as_of": "2026-07-07",
                }
            )
            guide_path.write_text(json.dumps(guide, ensure_ascii=False), encoding="utf-8")

            for _ in range(import_index):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            cache_path = tmp_path / "data" / "fund-metadata.json"
            assert cache_path.exists()
            cache = json.loads(cache_path.read_text("utf-8"))
            assert cache["rows"][0]["symbol"] == "3456.HK"
            assert cache["rows"][0]["tracking_index"] == "HKEX Tech 100 Index"
            imported_text = str(app.query_one("#action-status", Static).content)
            assert "基金资料已导入" in imported_text
            assert "3456.HK" in imported_text

    asyncio.run(run_case())


def test_dashboard_research_start_keeps_proxy_theme_selection(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="恒生指数压力观察",
                market="HK",
                symbol=None,
                proxy_symbols=["2800.HK", "3033.HK"],
                evidence_count=2,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="港股变化是整个市场的问题，还是只集中在某个板块？",
                why_it_matters="",
                observation_entry="2800.HK, 3033.HK",
                what_to_check="代理 ETF/指数方向、成交量和相关新闻要互相支持。",
                next_step="先下钻核验代理证据板。",
                priority="P2 代理核验",
                evidence_status="证据 2 条；缺口 0 个；代理已映射",
            )
        ]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:proxy",
                    candidate_id=1,
                    created_at="2026-07-05T10:00:00+00:00",
                    display_name="恒生指数压力观察",
                    symbol=None,
                    market="HK",
                    packet={
                        "candidate": {"asset_type": "theme"},
                        "evidence": [],
                        "local_data": {
                            "price": {},
                            "related_news": [],
                            "filings": [],
                            "symbol_mapping": [
                                {
                                    "symbol": "2800.HK",
                                    "name": "盈富基金",
                                    "market": "HK",
                                    "reason": "恒生指数压力主题代理。",
                                }
                            ],
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    def fake_verify_research_task(**kwargs: object) -> ResearchVerificationResult:
        calls.append(kwargs)
        candidate = FakeWorkbenchResult.candidates[0]
        packet = FakeWorkbenchResult.deepen_result.packets[0]
        return ResearchVerificationResult(
            created_at="2026-07-05T10:00:00+00:00",
            status="pending_review",
            status_label="待人工核验",
            candidate=candidate,
            packet=packet,
            checks=[
                ResearchVerificationCheck(
                    name="代理标的核验",
                    status="warning",
                    detail="代理标的需要人工核验。",
                )
            ],
            evidence_board={
                "support": ["代理映射: 2800.HK 盈富基金"],
                "risk": ["一致性核验: 待人工核验代理是否覆盖主题。"],
                "missing": [],
            },
            decision_board=_fake_needs_more_evidence_decision_board(),
            conclusion="一致性结论: 待人工核验。",
            next_actions=["核对代理成分、费用和流动性。"],
            artifact_path=tmp_path / "research" / "research-verification-proxy.json",
            workbench_result=FakeWorkbenchResult(),
        )

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        lambda **kwargs: FakeWorkbenchResult(),
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "verify_research_task",
        fake_verify_research_task,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert calls == [
                {
                    "output_dir": tmp_path,
                    "symbol": None,
                    "name": "恒生指数压力观察",
                }
            ]
            detail = app.query_one("#action-status", Static)
            assert "下钻核验结果" in str(detail.content)

    asyncio.run(run_case())


def test_dashboard_research_verification_can_review_pending_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    verify_calls: list[dict[str, object]] = []
    evidence_review_calls: list[dict[str, object]] = []

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="纳斯达克100ETF观察",
                market="US",
                symbol="QQQ",
                proxy_symbols=[],
                evidence_count=1,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                why_it_matters="",
                observation_entry="QQQ",
                what_to_check="对比 QQQ 与 SPY。",
                next_step="检查成交量是否配合反弹",
                priority="P2 先复核证据",
                evidence_status="证据 1 条；缺口 0 个",
                evidence_quality="needs_review",
            )
        ]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:1",
                    candidate_id=1,
                    created_at="2026-07-05T10:00:00+00:00",
                    display_name="纳斯达克100ETF观察",
                    symbol="QQQ",
                    market="US",
                    packet={
                        "candidate": {"asset_type": "ETF"},
                        "evidence": [],
                        "local_data": {
                            "price": {},
                            "related_news": [],
                            "filings": [],
                            "symbol_mapping": [],
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    def verification_result(*, reviewed: bool) -> ResearchVerificationResult:
        candidate = FakeWorkbenchResult.candidates[0]
        packet = FakeWorkbenchResult.deepen_result.packets[0]
        return ResearchVerificationResult(
            created_at="2026-07-05T10:00:00+00:00",
            status="pending_review",
            status_label="待人工核验",
            candidate=candidate,
            packet=packet,
            checks=[
                ResearchVerificationCheck(
                    name="证据方向核验",
                    status="pass" if reviewed else "warn",
                    detail=(
                        "相关新闻方向初步支持研究问题: 支持 1 条；反向 0 条；方向待判定 0 条。"
                        if reviewed
                        else (
                            "部分相关新闻方向未明，需要人工核验: "
                            "支持 0 条；反向 0 条；方向待判定 1 条。"
                        )
                    ),
                )
            ],
            evidence_board={
                "support": [
                    "行情: QQQ 530.26 USD",
                    *(["新闻: QQQ tech rebound headline"] if reviewed else []),
                ],
                "risk": (
                    []
                    if reviewed
                    else [
                        "新闻待判定: QQQ tech rebound headline 命中主题但方向未明。"
                    ]
                ),
                "missing": [],
            },
            decision_board=(
                _fake_ready_decision_board()
                if reviewed
                else _fake_needs_more_evidence_decision_board()
            ),
            conclusion="一致性结论: 待人工核验。",
            next_actions=["记录支持证据、反向证据和仍需补充的数据。"],
            artifact_path=tmp_path / "research" / "research-verification-test.json",
            workbench_result=FakeWorkbenchResult(),
        )

    def fake_verify_research_task(**kwargs: object) -> ResearchVerificationResult:
        verify_calls.append(kwargs)
        return verification_result(reviewed=bool(evidence_review_calls))

    def fake_record_research_evidence_review(
        **kwargs: object,
    ) -> ResearchEvidenceReviewResult:
        evidence_review_calls.append(kwargs)
        candidate = FakeWorkbenchResult.candidates[0]
        return ResearchEvidenceReviewResult(
            created_at="2026-07-05T10:01:00+00:00",
            verdict=str(kwargs["verdict"]),
            verdict_label="支持证据",
            evidence_text=str(kwargs["evidence_text"]),
            note=str(kwargs["note"]),
            candidate=candidate,
            artifact_path=tmp_path / "research" / "research-evidence-review-test.json",
            db_path=tmp_path / "research.sqlite3",
        )

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        lambda **kwargs: FakeWorkbenchResult(),
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "verify_research_task",
        fake_verify_research_task,
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "record_research_evidence_review",
        fake_record_research_evidence_review,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            action_menu = app.query_one("#research-detail-action-menu", OptionList)
            verify_index = _option_index(action_menu, "下钻核验")
            await pilot.press(*(["down"] * verify_index))
            await pilot.press("enter")
            await pilot.pause()

            review_menu = app.query_one("#research-detail-action-menu", OptionList)
            review_labels = [
                str(review_menu.get_option_at_index(index).prompt)
                for index in range(review_menu.option_count)
            ]
            support_index = review_labels.index(
                "标为支持证据: QQQ tech rebound headline"
            )
            await pilot.press(*(["down"] * support_index))
            await pilot.press("enter")
            await pilot.pause()

            assert evidence_review_calls == [
                {
                    "output_dir": tmp_path,
                    "symbol": "QQQ",
                    "name": None,
                    "evidence_text": "QQQ tech rebound headline",
                    "verdict": "support",
                    "note": "TUI 证据复核: 支持证据",
                }
            ]
            assert len(verify_calls) == 2
            detail = app.query_one("#action-status", Static)
            text = str(detail.content)
            assert "证据复核已记录" in text
            assert "更新后的下钻核验" in text
            assert "新闻: QQQ tech rebound headline" in text
            current_risk_section = text.split("风险/反向待查", 1)[1].split(
                "待补证据",
                1,
            )[0]
            assert "新闻待判定: QQQ tech rebound headline" not in current_risk_section

    asyncio.run(run_case())


def test_dashboard_research_task_action_generates_llm_memo(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    observed_status: list[str] = []
    app_holder: list[AlphaDeskApp] = []

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="纳斯达克100ETF观察",
                market="US",
                symbol="QQQ",
                proxy_symbols=[],
                evidence_count=1,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                why_it_matters="",
                observation_entry="QQQ",
                what_to_check="对比 QQQ 与 SPY。",
                next_step="检查成交量是否配合反弹",
                priority="P2 待增强证据",
                evidence_status="证据 1 条；缺口 0 个",
            )
        ]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:1",
                    candidate_id=1,
                    created_at="2026-07-05T10:00:00+00:00",
                    display_name="纳斯达克100ETF观察",
                    symbol="QQQ",
                    market="US",
                    packet={
                        "candidate": {"asset_type": "ETF"},
                        "evidence": [],
                        "local_data": {
                            "price": {},
                            "related_news": [],
                            "filings": [],
                            "symbol_mapping": [],
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    def fake_verification() -> ResearchVerificationResult:
        candidate = FakeWorkbenchResult.candidates[0]
        packet = FakeWorkbenchResult.deepen_result.packets[0]
        return ResearchVerificationResult(
            created_at="2026-07-05T10:00:00+00:00",
            status="pending_review",
            status_label="待人工核验",
            candidate=candidate,
            packet=packet,
            checks=[
                ResearchVerificationCheck(
                    name="行情核验",
                    status="pass",
                    detail="已有 QQQ 行情。",
                )
            ],
            evidence_board={
                "support": ["行情: QQQ 530.26 USD"],
                "risk": ["一致性核验: 待人工核验行情、成交量、新闻是否同向。"],
                "missing": [],
            },
            decision_board=_fake_ready_decision_board(),
            conclusion="一致性结论: 待人工核验。",
            next_actions=["记录支持证据、反向证据和仍需补充的数据。"],
            artifact_path=tmp_path / "research" / "research-verification-test.json",
            workbench_result=FakeWorkbenchResult(),
        )

    def fake_generate_research_memo(**kwargs: object) -> ResearchMemoResult:
        app = app_holder[0]
        observed_status.append(str(app.query_one("#action-status", Static).content))
        calls.append(kwargs)
        verification = fake_verification()
        return ResearchMemoResult(
            created_at="2026-07-05T10:02:00+00:00",
            memo=ResearchMemo(
                summary="QQQ 线索需要先区分科技独立主线和大盘反弹。",
                working_hypothesis=(
                    "如果科技股是独立主线，QQQ 相对 SPY 应持续更强并伴随成交量扩散。"
                ),
                evidence_reading="已有行情证据，但仍需补充相对强弱和成交量证据。",
                support_points=["QQQ 已有可观察行情入口。"],
                skeptic_review=["单一 ETF 不能证明科技主线独立成立。"],
                falsification_checks=[
                    "若 QQQ 只是跟随 SPY 同步反弹且成交量不扩散，应降低主题置信度。"
                ],
                missing_evidence=["缺少 QQQ 与 SPY 的相对强弱对比。"],
                next_data_requests=["拉取 QQQ/SPY 近 20 日行情、成交量和前十大成分变化。"],
                next_research_steps=["补充 QQQ/SPY 对比和成交量扩散证据。"],
                confidence="medium",
            ),
            candidate=verification.candidate,
            verification=verification,
            artifact_path=tmp_path / "research" / "research-memo-test.json",
            db_path=tmp_path / "research.sqlite3",
        )

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        lambda **kwargs: FakeWorkbenchResult(),
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "generate_research_memo",
        fake_generate_research_memo,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        app_holder.append(app)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            action_menu = app.query_one("#research-detail-action-menu", OptionList)
            memo_action = str(action_menu.get_option_at_index(4).prompt)
            assert "研究备忘录" in memo_action

            await pilot.press("down", "down", "down", "down")
            await pilot.press("enter")
            await pilot.pause()

            assert calls == [
                {
                    "output_dir": tmp_path,
                    "symbol": "QQQ",
                    "name": None,
                }
            ]
            assert any("正在调用 LLM 生成研究备忘录" in item for item in observed_status)
            detail = app.query_one("#action-status", Static)
            text = str(detail.content)
            assert "研究备忘录" in text
            assert "QQQ 线索需要先区分" in text
            assert "反方审查" in text
            assert "下一步研究动作" in text
            assert "研究备忘录不是买卖建议" in text
            next_menu = app.query_one("#research-detail-action-menu", OptionList)
            next_actions = [
                str(next_menu.get_option_at_index(index).prompt)
                for index in range(next_menu.option_count)
            ]
            assert next_actions[:5] == [
                "记录研究复核: 继续研究",
                "重新下钻核验",
                "查看研究数据请求",
                "查看研究备忘录历史",
                "返回研究任务列表",
            ]

    asyncio.run(run_case())


def test_dashboard_research_verification_can_record_review_verdict(
    monkeypatch, tmp_path: Path
) -> None:
    review_calls: list[dict[str, object]] = []

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 1
        blocked_count = 0
        candidates = [
            CandidateCheck(
                display_name="纳斯达克100ETF观察",
                market="US",
                symbol="QQQ",
                proxy_symbols=[],
                evidence_count=1,
                gap_count=0,
                data_gaps=[],
                status="ready",
                explanation="",
                beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
                why_it_matters="",
                observation_entry="QQQ",
                what_to_check="对比 QQQ 与 SPY。",
                next_step="检查成交量是否配合反弹",
                priority="P2 待增强证据",
                evidence_status="证据 1 条；缺口 0 个",
            )
        ]
        deepen_result = ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[
                ResearchPacket(
                    packet_id="research:test:1",
                    candidate_id=1,
                    created_at="2026-07-05T10:00:00+00:00",
                    display_name="纳斯达克100ETF观察",
                    symbol="QQQ",
                    market="US",
                    packet={
                        "candidate": {"asset_type": "ETF"},
                        "evidence": [],
                        "local_data": {
                            "price": {},
                            "related_news": [],
                            "filings": [],
                            "symbol_mapping": [],
                        },
                        "data_gaps": [],
                    },
                )
            ],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        )
        beginner_brief = "AlphaDesk 研究工作台"

    def fake_verification() -> ResearchVerificationResult:
        candidate = FakeWorkbenchResult.candidates[0]
        packet = FakeWorkbenchResult.deepen_result.packets[0]
        decision_board = _fake_needs_more_evidence_decision_board()
        return ResearchVerificationResult(
            created_at="2026-07-05T10:00:00+00:00",
            status="pending_review",
            status_label="待人工核验",
            candidate=candidate,
            packet=packet,
            checks=[
                ResearchVerificationCheck(
                    name="行情核验",
                    status="pass",
                    detail="已有 QQQ 行情。",
                )
            ],
            evidence_board={
                "support": ["行情: QQQ 530.26 USD"],
                "risk": ["一致性核验: 待人工核验行情、成交量、新闻是否同向。"],
                "missing": [],
            },
            decision_board=decision_board,
            conclusion="一致性结论: 待人工核验。",
            next_actions=["记录支持证据、反向证据和仍需补充的数据。"],
            artifact_path=tmp_path / "research" / "research-verification-test.json",
            workbench_result=FakeWorkbenchResult(),
        )

    def fake_record_research_review(**kwargs: object) -> ResearchReviewResult:
        review_calls.append(kwargs)
        verification = fake_verification()
        verdict = str(kwargs["verdict"])
        verdict_label = "需要补证据" if verdict == "needs_more_evidence" else "继续研究"
        return ResearchReviewResult(
            created_at="2026-07-05T10:01:00+00:00",
            verdict=verdict,
            verdict_label=verdict_label,
            note=f"TUI 快速复核: {verdict_label}",
            evidence_counts={"support": 1, "risk": 1, "off_topic": 0, "missing": 0},
            verification=verification,
            artifact_path=tmp_path / "research" / "research-review-test.json",
            db_path=tmp_path / "research.sqlite3",
        )

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        lambda **kwargs: FakeWorkbenchResult(),
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "verify_research_task",
        lambda **kwargs: fake_verification(),
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "record_research_review",
        fake_record_research_review,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            action_menu = app.query_one("#research-detail-action-menu", OptionList)
            first_review_action = str(action_menu.get_option_at_index(0).prompt)
            assert "按工作台建议记录: 需要补证据" in first_review_action

            await pilot.press("enter")
            await pilot.pause()

            assert review_calls == [
                {
                    "output_dir": tmp_path,
                    "symbol": "QQQ",
                    "name": None,
                    "verdict": "needs_more_evidence",
                    "note": "TUI 快速复核: 需要补证据",
                }
            ]
            detail = app.query_one("#action-status", Static)
            text = str(detail.content)
            assert "研究复核已记录" in text
            assert "复核判断: 需要补证据" in text
            assert "研究复核不是买卖建议" in text
            next_menu = app.query_one("#research-detail-action-menu", OptionList)
            next_actions = [
                str(next_menu.get_option_at_index(index).prompt)
                for index in range(next_menu.option_count)
            ]
            assert next_actions[:3] == [
                "刷新主题新闻",
                "重新下钻核验",
                "返回研究任务列表",
            ]

    asyncio.run(run_case())


def test_dashboard_refresh_topic_news_keeps_current_research_task(
    monkeypatch, tmp_path: Path
) -> None:
    refresh_calls: list[dict[str, object]] = []
    workbench_calls = 0

    def candidate(display_name: str, symbol: str) -> CandidateCheck:
        return CandidateCheck(
            display_name=display_name,
            market="US",
            symbol=symbol,
            proxy_symbols=[],
            evidence_count=1,
            gap_count=0,
            data_gaps=[],
            status="ready",
            explanation="",
            beginner_question="这个主题是否值得继续研究？",
            why_it_matters="",
            observation_entry=symbol,
            what_to_check=f"核验 {symbol} 证据方向。",
            next_step="先补主题新闻。",
            priority="P2 先复核证据",
            evidence_status="证据 1 条；缺口 0 个",
            evidence_quality="needs_review",
        )

    def packet(display_name: str, symbol: str, candidate_id: int) -> ResearchPacket:
        return ResearchPacket(
            packet_id=f"research:test:{candidate_id}",
            candidate_id=candidate_id,
            created_at="2026-07-05T10:00:00+00:00",
            display_name=display_name,
            symbol=symbol,
            market="US",
            packet={
                "candidate": {
                    "asset_type": "ETF",
                    "related_theme": f"{symbol} technology theme",
                    "why_watch": f"{symbol} theme needs review.",
                },
                "evidence": [],
                "local_data": {
                    "price": {},
                    "related_news": [],
                    "filings": [],
                    "symbol_mapping": [],
                },
                "data_gaps": [],
            },
        )

    qqq = candidate("纳斯达克100ETF观察", "QQQ")
    spy = candidate("标普500ETF观察", "SPY")
    qqq_packet = packet("纳斯达克100ETF观察", "QQQ", 1)
    spy_packet = packet("标普500ETF观察", "SPY", 2)

    class FakeWorkbenchResult:
        status = "ready"
        ready_count = 2
        blocked_count = 0
        artifact_path = None
        db_path = tmp_path / "research.sqlite3"
        beginner_brief = "AlphaDesk 研究工作台"

        def __init__(
            self,
            candidates: list[CandidateCheck],
            packets: list[ResearchPacket],
        ) -> None:
            self.candidates = candidates
            self.deepen_result = ResearchDeepenResult(
                created_at="2026-07-05T10:00:00+00:00",
                packets=packets,
                artifact_path=None,
                db_path=tmp_path / "research.sqlite3",
            )

    def fake_run_workbench_check(**kwargs: object) -> FakeWorkbenchResult:
        nonlocal workbench_calls
        workbench_calls += 1
        if workbench_calls == 1:
            return FakeWorkbenchResult([qqq, spy], [qqq_packet, spy_packet])
        return FakeWorkbenchResult([spy, qqq], [spy_packet, qqq_packet])

    def fake_pull_news_events(**kwargs: object) -> PullResult:
        refresh_calls.append(kwargs)
        output_path = tmp_path / "data" / "news-events.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{}", encoding="utf-8")
        return PullResult("news", "test-news", 2, output_path, [])

    monkeypatch.setattr(
        tui_app,
        "run_workbench_check",
        fake_run_workbench_check,
        raising=False,
    )
    monkeypatch.setattr(
        tui_app,
        "pull_news_events",
        fake_pull_news_events,
        raising=False,
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            action_menu = app.query_one("#research-detail-action-menu", OptionList)
            assert str(action_menu.get_option_at_index(3).prompt) == "刷新主题新闻"

            await pilot.press("down", "down", "down")
            await pilot.press("enter")
            await pilot.pause()

            assert refresh_calls
            assert refresh_calls[0]["symbols"] == ["QQQ"]
            detail = app.query_one("#action-status", Static)
            text = str(detail.content)
            assert "任务: 纳斯达克100ETF观察 [US]" in text
            assert "入口: QQQ" in text
            assert "已执行: 刷新主题新闻" in text
            assert "返回行数: 2" in text
            refreshed_menu = app.query_one("#research-detail-action-menu", OptionList)
            refreshed_actions = [
                str(refreshed_menu.get_option_at_index(index).prompt)
                for index in range(refreshed_menu.option_count)
            ]
            assert refreshed_actions[:3] == [
                "重新下钻核验",
                "生成研究备忘录",
                "返回研究任务列表",
            ]

    asyncio.run(run_case())


def test_dashboard_today_discovery_requires_llm_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            text = str(status.content)
            assert "LLM 尚未配置" in text
            assert "lychee setup llm set" in text
            assert not (tmp_path / "data" / "discovery-today.json").exists()
            assert not app.query(Input)

    asyncio.run(run_case())


def test_dashboard_today_discovery_action_writes_report_when_llm_configured(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    set_openai_compatible_llm(
        "https://llm.example.com/v1",
        "sk-demo-secret",
        "demo-model",
    )

    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == []
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        news_path = data_dir / "news-events.json"
        news_path.write_text(
            json.dumps(
                {
                    "provider": "newsapi",
                    "created_at": "2026-07-06T10:00:00+00:00",
                    "warnings": [],
                    "rows": [
                        {
                            "timestamp": "2026-07-06T10:00:00+00:00",
                            "headline": "TUI market news",
                            "summary": "Prepared before LLM.",
                            "symbols": ["MARKET"],
                            "source_url": "https://example.com/tui-market-news",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", 1, news_path, [])

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        return {
            "choices": [
                {
                    "message": {
                        "content": """
                        {
                          "themes": [
                            {
                              "name": "TUI model theme",
                              "markets": ["US", "HK", "CN"],
                              "summary": "Model generated this TUI theme.",
                              "evidence": ["news_001"],
                              "sectors": ["Technology"],
                              "risk_flags": ["Model uncertainty"],
                              "confidence": "medium"
                            }
                          ],
                          "candidates": [
                            {
                              "display_name": "TUI model candidate",
                              "symbol": "NVDA",
                              "market": "US",
                              "asset_type": "stock",
                              "related_theme": "TUI model theme",
                              "why_watch": "Generated by the model.",
                              "evidence": ["news_001"],
                              "risk_flags": ["Model uncertainty"],
                              "next_actions": ["Pull filings"],
                              "confidence": "medium",
                              "recommendation": "research"
                            }
                          ],
                          "warnings": [],
                          "next_actions": ["Review model evidence"]
                        }
                        """
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "lychee_alphadesk.core.discovery.pull_news_events",
        fake_pull_news_events,
    )
    monkeypatch.setattr("lychee_alphadesk.core.llm._post_json", fake_post)

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            text = str(status.content)
            assert "今日市场发现" in text
            assert "模式: llm-synthesized" in text
            assert "TUI model theme" in text
            assert "非投资建议" in text
            assert "研究库已更新:" in text
            assert (tmp_path / "data" / "discovery-today.json").exists()
            queue = list_research_queue(tmp_path)
            assert queue[0].display_name == "TUI model candidate"
            assert queue[0].symbol == "NVDA"
            assert not app.query(Input)

    asyncio.run(run_case())


def test_dashboard_today_discovery_shows_loading_before_llm_call(
    monkeypatch, tmp_path: Path
) -> None:
    observed_status: list[str] = []

    def fake_report() -> DiscoveryReport:
        return DiscoveryReport(
            mode="llm-synthesized",
            created_at="2026-07-05T00:00:00+00:00",
            markets=["US", "HK", "CN"],
            sources=[
                DiscoverySource(
                    provider="test-llm",
                    market="US",
                    description="test",
                )
            ],
            themes=[
                DiscoveryTheme(
                    name="测试主题",
                    markets=["US"],
                    summary="测试摘要",
                    evidence=["测试证据"],
                    sectors=["科技"],
                    risk_flags=["测试风险"],
                    confidence="medium",
                )
            ],
            candidates=[
                DiscoveryCandidate(
                    display_name="测试候选",
                    symbol="TEST",
                    market="US",
                    asset_type="stock",
                    related_theme="测试主题",
                    why_watch="测试原因",
                    evidence=["测试证据"],
                    risk_flags=["测试风险"],
                    next_actions=["继续研究"],
                    confidence="medium",
                    recommendation="research",
                )
            ],
            warnings=[],
            next_actions=["继续研究"],
            disclaimer="非投资建议。",
        )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)

        def fake_build_today_discovery_report(**kwargs: object) -> DiscoveryReport:
            status = app.query_one("#action-status", Static)
            observed_status.append(str(status.content))
            return fake_report()

        monkeypatch.setattr(
            tui_app,
            "build_today_discovery_report",
            fake_build_today_discovery_report,
        )
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()

        assert any("正在准备市场级新闻" in status for status in observed_status)

    asyncio.run(run_case())


def test_dashboard_market_menu_action_pulls_prices(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_pull_market_prices(**kwargs: object) -> PullResult:
        calls.append(kwargs["symbols"])  # type: ignore[arg-type]
        return PullResult(
            domain="market",
            provider="alpha_vantage",
            count=2,
            output_path=tmp_path / "data" / "market-prices.json",
            warnings=[],
        )

    monkeypatch.setattr(tui_app, "pull_market_prices", fake_pull_market_prices)

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            market_index = _option_index(menu, "手动查看行情")
            await pilot.press(*(["down"] * market_index))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app.query_one("#symbols-input", Input)

            await pilot.press(*"AAPL,TSLA")
            await pilot.press("enter")
            await pilot.pause()

            assert calls == [["AAPL", "TSLA"]]
            status = app.query_one("#action-status", Static)
            assert "已拉取行情: 2" in str(status.content)

    asyncio.run(run_case())


def test_dashboard_symbol_prompt_handles_empty_submit(tmp_path: Path) -> None:
    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            menu = app.query_one("#action-menu", OptionList)
            market_index = _option_index(menu, "手动查看行情")
            await pilot.press(*(["down"] * market_index))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app.query_one("#symbols-input", Input)

            await pilot.press("enter")
            await pilot.pause()

            status = app.query_one("#action-status", Static)
            assert "未输入证券代码。" in str(status.content)
            app.query_one("#symbols-input", Input)

    asyncio.run(run_case())


def test_dashboard_disables_textual_command_palette(tmp_path: Path) -> None:
    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()

            assert app.screen.id != "--command-palette"

    asyncio.run(run_case())


def test_dashboard_disables_text_selection_to_avoid_mouse_selection_crash() -> None:
    assert AlphaDeskApp.ALLOW_SELECT is False
