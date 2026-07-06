import asyncio
import json
from pathlib import Path

from textual.widgets import Input, OptionList, Static

import lychee_alphadesk.tui.app as tui_app
from lychee_alphadesk.core.config import set_openai_compatible_llm
from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.live_data import PullResult
from lychee_alphadesk.core.research import ResearchDeepenResult, ResearchPacket
from lychee_alphadesk.core.research_db import (
    ResearchMemoRecord,
    ResearchReviewRecord,
    list_research_queue,
)
from lychee_alphadesk.core.research_memo import ResearchMemo, ResearchMemoResult
from lychee_alphadesk.core.workbench import (
    CandidateCheck,
    ResearchDecisionBoard,
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

            await pilot.press("down")
            await pilot.pause()

            assert menu.highlighted == 1

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
            assert "给新手的读法" not in text
            task_menu = app.query_one("#research-task-menu", OptionList)
            task_label = str(task_menu.get_option_at_index(0).prompt)
            assert "纳斯达克100ETF观察" in task_label
            assert "QQQ" in task_label
            assert "排序: 有直接代码且当前没有数据缺口" in task_label
            assert not app.query(Input)

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
            assert "数据缺口: 无" in text
            assert "下一步动作:" in text
            assert "对比 QQQ 与 SPY。" in text
            action_menu = app.query_one("#research-detail-action-menu", OptionList)
            first_action = str(action_menu.get_option_at_index(0).prompt)
            assert "刷新行情" in first_action
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
            verify_action = str(action_menu.get_option_at_index(2).prompt)
            assert "下钻核验" in verify_action

            await pilot.press("down", "down")
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
                evidence_reading="已有行情证据，但仍需补充相对强弱和成交量证据。",
                support_points=["QQQ 已有可观察行情入口。"],
                skeptic_review=["单一 ETF 不能证明科技主线独立成立。"],
                missing_evidence=["缺少 QQQ 与 SPY 的相对强弱对比。"],
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
            memo_action = str(action_menu.get_option_at_index(3).prompt)
            assert "研究备忘录" in memo_action

            await pilot.press("down", "down", "down")
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
            evidence_counts={"support": 1, "risk": 1, "missing": 0},
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
            await pilot.press("down", "down")
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
