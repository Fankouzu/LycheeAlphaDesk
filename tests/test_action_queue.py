import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import lychee_alphadesk.core.action_queue as action_queue
from lychee_alphadesk.core.action_queue import build_action_queue
from lychee_alphadesk.core.live_data import PullResult
from lychee_alphadesk.core.opportunity_radar import (
    OpportunityDrilldownTarget,
    OpportunityRadarReport,
    OpportunitySignal,
)
from lychee_alphadesk.core.research import ResearchDeepenResult, ResearchGapFillResult
from lychee_alphadesk.core.research_requests import (
    ProviderBacklogItem,
    ResearchDataRequest,
    ResearchDataRequestAction,
    ResearchDataRequestExecution,
    ResearchDataRequestFulfillment,
)
from lychee_alphadesk.core.workbench import CandidateCheck, PendingEvidenceReviewItem


def test_action_queue_prioritizes_concrete_next_steps(tmp_path: Path) -> None:
    candidate = CandidateCheck(
        display_name="Seagate",
        market="US",
        symbol="STX",
        proxy_symbols=[],
        evidence_count=2,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="证据可以继续核验。",
        beginner_question="AI 存储需求是否反映到公司和供应链？",
        why_it_matters="需要避免只看热门叙事。",
        observation_entry="STX",
        what_to_check="行情、新闻、公告和研究指标是否同向。",
        next_step="重新下钻核验。",
        priority="P1 可研究",
        evidence_status="支持 2 | 待补 0",
        ranking_reason="有行情、新闻和公告线索。",
        next_command="lychee research verify --symbol STX",
    )
    workbench_result = SimpleNamespace(
        candidates=[candidate],
        deepen_result=ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        ),
        fill_result=ResearchGapFillResult(1, [], [], [], []),
    )
    pending_item = PendingEvidenceReviewItem(
        created_at="2026-07-05T10:01:00+00:00",
        display_name="Seagate",
        symbol="STX",
        market="US",
        primary_question="AI 存储需求是否反映到公司和供应链？",
        evidence_text="新闻待判定: STX storage demand debate continues",
        raw_evidence="STX storage demand debate continues",
        suggested_verdict="support",
        suggested_verdict_label="支持",
        suggested_reason="命中需求改善线索。",
        artifact_path=str(tmp_path / "research" / "verify.json"),
        review_command=(
            "lychee research evidence-review --symbol STX "
            '--evidence "STX storage demand debate continues" --verdict support'
        ),
    )
    data_request = ResearchDataRequest(
        request_id="memo:test:data-request:1",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Seagate",
        symbol="STX",
        market="US",
        confidence="medium",
        request_text="请补充 STX 行情和成交量。",
        suggested_commands=[
            "lychee data pull market --symbols STX --provider auto --force",
            "lychee research verify --symbol STX",
        ],
        memo_path=str(tmp_path / "research" / "memo.json"),
        verification_path=str(tmp_path / "research" / "verify.json"),
        suggested_actions=[
            ResearchDataRequestAction(
                "market",
                "lychee data pull market --symbols STX --provider auto --force",
            ),
            ResearchDataRequestAction("verify", "lychee research verify --symbol STX"),
        ],
    )
    provider_gap = ProviderBacklogItem(
        request_id="memo:test:data-request:2",
        created_at="2026-07-05T10:03:00+00:00",
        display_name="Seagate",
        symbol="STX",
        market="US",
        confidence="medium",
        request_text="请补充 AI 存储链扩散指标。",
        data_domain="市场广度",
        plugin_type="market_breadth",
        coverage_gap="当前缺少市场广度 provider。",
        suggested_provider_examples=["行业/子行业表现数据源"],
        suggested_commands=[
            "lychee data set metric --symbol STX --domain market_breadth "
            '--name "<填入指标名称>" --value "<填入核验后的读数>" '
            '--as-of YYYY-MM-DD --source-url "<资料来源URL>"'
        ],
        next_step="先写入 source-backed 研究指标。",
        memo_path=str(tmp_path / "research" / "memo.json"),
        verification_path=str(tmp_path / "research" / "verify.json"),
    )

    queue = build_action_queue(
        tmp_path,
        workbench_runner=lambda **kwargs: workbench_result,
        pending_reader=lambda **kwargs: [pending_item],
        data_request_reader=lambda **kwargs: [data_request],
        provider_backlog_reader=lambda **kwargs: [provider_gap],
    )

    assert [item.area for item in queue] == [
        "待判定证据",
        "研究数据请求",
        "研究任务",
        "数据源缺口",
    ]
    assert queue[0].command.startswith("lychee research evidence-review --symbol STX")
    assert queue[1].title == "补行情数据: Seagate"
    assert queue[1].command == "lychee research run-data-request --request 1 --symbol STX"
    assert queue[2].command == "lychee research verify --symbol STX"
    assert queue[3].command.startswith("lychee data set metric --symbol STX")
    assert all("买入" not in item.detail for item in queue)


def test_action_queue_numbers_data_requests_within_selected_task(
    tmp_path: Path,
) -> None:
    first = ResearchDataRequest(
        request_id="memo:test:qqq:data-request:1",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="medium",
        request_text="请补充 QQQ 行情。",
        suggested_commands=["lychee data pull market --symbols QQQ --force"],
        memo_path=str(tmp_path / "research" / "memo-qqq.json"),
        verification_path=str(tmp_path / "research" / "verify-qqq.json"),
        suggested_actions=[
            ResearchDataRequestAction(
                "market",
                "lychee data pull market --symbols QQQ --force",
            )
        ],
    )
    second = ResearchDataRequest(
        request_id="memo:test:stx:data-request:1",
        created_at="2026-07-05T10:03:00+00:00",
        display_name="Seagate",
        symbol="STX",
        market="US",
        confidence="medium",
        request_text="请补充 STX 行情。",
        suggested_commands=["lychee data pull market --symbols STX --force"],
        memo_path=str(tmp_path / "research" / "memo-stx.json"),
        verification_path=str(tmp_path / "research" / "verify-stx.json"),
        suggested_actions=[
            ResearchDataRequestAction(
                "market",
                "lychee data pull market --symbols STX --force",
            )
        ],
    )
    workbench_result = SimpleNamespace(
        candidates=[],
        deepen_result=ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        ),
        fill_result=ResearchGapFillResult(1, [], [], [], []),
    )

    queue = build_action_queue(
        tmp_path,
        workbench_runner=lambda **kwargs: workbench_result,
        pending_reader=lambda **kwargs: [],
        data_request_reader=lambda **kwargs: [first, second],
        provider_backlog_reader=lambda **kwargs: [],
    )

    assert [item.command for item in queue] == [
        "lychee research run-data-request --request 1 --symbol QQQ",
        "lychee research run-data-request --request 1 --symbol STX",
    ]


def test_action_queue_passes_limit_into_workbench_scan(tmp_path: Path) -> None:
    scanned_limits: list[int | None] = []
    candidate = CandidateCheck(
        display_name="512480.SH",
        market="CN",
        symbol="512480.SH",
        proxy_symbols=[],
        evidence_count=1,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="机会雷达已经把半导体 ETF 推入研究队列。",
        beginner_question="AI 基础设施主题是否扩散到 A 股半导体代理？",
        why_it_matters="这是雷达补证据后的后续研究动作。",
        observation_entry="512480.SH",
        what_to_check="核验行情、新闻方向和代理标的有效性。",
        next_step="下钻核验证据板。",
        priority="P2 证据待分类",
        evidence_status="证据 1 条；缺口 0 个",
        ranking_reason="主题新闻已刷新，下一步是核验证据方向。",
        next_command="lychee research verify --symbol 512480.SH",
    )

    def fake_workbench_runner(**kwargs: object) -> SimpleNamespace:
        raw_limit = kwargs.get("limit")
        scanned_limits.append(raw_limit if isinstance(raw_limit, int) else None)
        candidates = [candidate] if raw_limit == 20 else []
        return SimpleNamespace(
            candidates=candidates,
            deepen_result=ResearchDeepenResult(
                created_at="2026-07-05T10:00:00+00:00",
                packets=[],
                artifact_path=None,
                db_path=tmp_path / "research.sqlite3",
            ),
            fill_result=ResearchGapFillResult(1, [], [], [], []),
        )

    queue = build_action_queue(
        tmp_path,
        limit=20,
        workbench_runner=fake_workbench_runner,
        pending_reader=lambda **kwargs: [],
        data_request_reader=lambda **kwargs: [],
        provider_backlog_reader=lambda **kwargs: [],
        radar_reader=lambda **kwargs: OpportunityRadarReport(
            created_at="2026-07-08T00:00:00+00:00",
            status="empty",
            signals=[],
            warnings=[],
            disclaimer="非投资建议。",
        ),
    )

    assert scanned_limits == [20]
    assert [item.command for item in queue] == [
        "lychee research verify --symbol 512480.SH"
    ]


def test_action_queue_includes_opportunity_radar_drilldown_targets(
    tmp_path: Path,
) -> None:
    workbench_result = SimpleNamespace(
        candidates=[],
        deepen_result=ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        ),
        fill_result=ResearchGapFillResult(1, [], [], [], []),
    )
    radar_report = OpportunityRadarReport(
        created_at="2026-07-08T00:00:00+00:00",
        status="ready",
        signals=[
            OpportunitySignal(
                symbol="QQQ",
                market="US",
                theme="AI 基础设施扩散",
                score=341,
                news_count=30,
                theme_hits=73,
                volume_rank=11,
                price_snapshot="708.12 USD | 2026-07-07",
                why_it_matters="新闻热度和主题命中同时出现。",
                evidence=["Which Companies Will Actually Win From AI?"],
                next_steps=["lychee research run --symbol QQQ --force"],
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
                            ),
                            "lychee research run --symbol NVDA --force",
                        ],
                    )
                ],
            )
        ],
        warnings=[],
        disclaimer="非投资建议。",
    )
    data_request = ResearchDataRequest(
        request_id="memo:test:qqq:data-request:1",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="medium",
        request_text="请补充 QQQ 行情。",
        suggested_commands=["lychee data pull market --symbols QQQ --force"],
        memo_path=str(tmp_path / "research" / "memo-qqq.json"),
        verification_path=str(tmp_path / "research" / "verify-qqq.json"),
        suggested_actions=[
            ResearchDataRequestAction(
                "market",
                "lychee data pull market --symbols QQQ --force",
            )
        ],
    )
    provider_gap = ProviderBacklogItem(
        request_id="memo:test:data-request:2",
        created_at="2026-07-05T10:03:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="medium",
        request_text="请补充 QQQ 市场广度。",
        data_domain="市场广度",
        plugin_type="market_breadth",
        coverage_gap="当前缺少市场广度 provider。",
        suggested_provider_examples=["行业/子行业表现数据源"],
        suggested_commands=[
            "lychee data set metric --symbol QQQ --domain market_breadth "
            '--name "<填入指标名称>" --value "<填入核验后的读数>" '
            '--as-of YYYY-MM-DD --source-url "<资料来源URL>"'
        ],
        next_step="先写入 source-backed 研究指标。",
        memo_path=str(tmp_path / "research" / "memo-qqq.json"),
        verification_path=str(tmp_path / "research" / "verify-qqq.json"),
    )

    queue = build_action_queue(
        tmp_path,
        workbench_runner=lambda **kwargs: workbench_result,
        pending_reader=lambda **kwargs: [],
        data_request_reader=lambda **kwargs: [data_request],
        provider_backlog_reader=lambda **kwargs: [provider_gap],
        radar_reader=lambda **kwargs: radar_report,
    )

    assert [item.area for item in queue] == ["机会雷达", "研究数据请求", "数据源缺口"]
    assert queue[0].title == "下钻 NVIDIA: AI 基础设施扩散"
    assert "来自 QQQ 雷达信号" in queue[0].detail
    assert "缺少该标的的主题新闻缓存" in queue[0].detail
    assert queue[0].command.startswith("lychee data pull news --symbols NVDA")
    assert queue[0].source == "opportunity-radar"
    assert all("买入" not in item.detail for item in queue)


def test_execute_action_queue_runs_radar_news_refresh(tmp_path: Path) -> None:
    item = action_queue.ActionQueueItem(
        priority=20,
        area="机会雷达",
        title="下钻 NVIDIA: AI 基础设施扩散",
        detail="来自 QQQ 雷达信号；缺少该标的的主题新闻缓存。",
        command=(
            "lychee data pull news --symbols NVDA "
            '--query "AI chip data center semiconductor demand" --force'
        ),
        source="opportunity-radar",
    )
    calls: list[dict[str, object]] = []

    def fake_pull_news(**kwargs: object) -> PullResult:
        calls.append(kwargs)
        return PullResult(
            domain="news",
            provider="test-news",
            count=3,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    result = action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        limit=5,
        queue_builder=lambda *args, **kwargs: [item],
        pull_news=fake_pull_news,
    )

    assert calls == [
        {
            "symbols": ["NVDA"],
            "query": "AI chip data center semiconductor demand",
            "output_dir": tmp_path,
            "provider_id": "auto",
            "force": True,
        }
    ]
    assert result.status == "completed"
    assert result.item.title == "下钻 NVIDIA: AI 基础设施扩散"
    assert result.count == 3
    assert result.output_path == tmp_path / "data" / "news-events.json"
    assert result.next_command == "lychee research run --symbol NVDA --force"


def test_execute_action_queue_does_not_advance_empty_news_refresh(
    tmp_path: Path,
) -> None:
    item = action_queue.ActionQueueItem(
        priority=20,
        area="机会雷达",
        title="下钻 Alibaba: AI 基础设施扩散",
        detail="来自 NVDA 雷达信号；缺少该标的的主题新闻缓存。",
        command=(
            "lychee data pull news --symbols BABA "
            '--query "AI cloud revenue Alibaba data center" --force'
        ),
        source="opportunity-radar",
    )

    def fake_pull_news(**kwargs: object) -> PullResult:
        return PullResult(
            domain="news",
            provider="test-news",
            count=0,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=["provider 返回 0 条新闻"],
        )

    result = action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        limit=5,
        queue_builder=lambda *args, **kwargs: [item],
        pull_news=fake_pull_news,
    )

    assert result.status == "no-data"
    assert "没有获取到" in result.message
    assert result.next_command == ""
    assert result.warnings == ["provider 返回 0 条新闻"]


def test_no_data_radar_action_enters_cooldown_and_leaves_queue(
    tmp_path: Path,
) -> None:
    first = OpportunityDrilldownTarget(
        symbol="BABA",
        market="US",
        display_name="Alibaba",
        category="AI 云观察",
        reason="用阿里云线索校验 AI 基础设施是否扩散到中国云厂商。",
        evidence_gap="缺少该标的的主题新闻缓存，需补新闻验证。",
        next_steps=[
            (
                "lychee data pull news --symbols BABA "
                '--query "AI cloud revenue Alibaba data center" --force'
            ),
            "lychee research run --symbol BABA --force",
        ],
    )
    second = OpportunityDrilldownTarget(
        symbol="512480.SH",
        market="CN",
        display_name="半导体 ETF",
        category="A 股供应链代理",
        reason="用半导体 ETF 观察 AI 基础设施主题是否扩散到 A 股供应链。",
        evidence_gap="缺少该标的的主题新闻缓存，需补新闻验证。",
        next_steps=[
            (
                "lychee data pull news --symbols 512480.SH "
                '--query "AI semiconductor data center China" --force'
            ),
            "lychee research run --symbol 512480.SH --force",
        ],
    )
    radar_report = OpportunityRadarReport(
        created_at="2026-07-08T00:00:00+00:00",
        status="ready",
        signals=[
            OpportunitySignal(
                symbol="QQQ",
                market="US",
                theme="AI 基础设施扩散",
                score=300,
                news_count=20,
                theme_hits=50,
                volume_rank=10,
                price_snapshot="708.12 USD | 2026-07-07",
                why_it_matters="新闻热度和主题命中同时出现。",
                evidence=["AI infrastructure spending accelerates"],
                next_steps=[],
                drilldown_targets=[first, second],
            )
        ],
        warnings=[],
        disclaimer="非投资建议。",
    )
    workbench_result = SimpleNamespace(
        candidates=[],
        deepen_result=ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        ),
        fill_result=ResearchGapFillResult(1, [], [], [], []),
    )

    def fake_pull_news(**kwargs: object) -> PullResult:
        return PullResult(
            domain="news",
            provider="test-news",
            count=0,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=["provider 返回 0 条新闻"],
        )

    result = action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        limit=5,
        queue_builder=lambda *args, **kwargs: build_action_queue(
            tmp_path,
            workbench_runner=lambda **kwargs: workbench_result,
            pending_reader=lambda **kwargs: [],
            data_request_reader=lambda **kwargs: [],
            provider_backlog_reader=lambda **kwargs: [],
            radar_reader=lambda **kwargs: radar_report,
        ),
        pull_news=fake_pull_news,
    )

    queue = build_action_queue(
        tmp_path,
        workbench_runner=lambda **kwargs: workbench_result,
        pending_reader=lambda **kwargs: [],
        data_request_reader=lambda **kwargs: [],
        provider_backlog_reader=lambda **kwargs: [],
        radar_reader=lambda **kwargs: radar_report,
    )

    assert result.status == "no-data"
    assert queue[0].title == "下钻 半导体 ETF: AI 基础设施扩散"
    assert all("BABA" not in item.command for item in queue)
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT status, command
            FROM action_queue_cooldowns
            WHERE command = ?
            """,
            (first.next_steps[0],),
        ).fetchone()
    assert row == ("no-data", first.next_steps[0])


def test_completed_radar_news_action_turns_into_research_followup(
    tmp_path: Path,
) -> None:
    target = OpportunityDrilldownTarget(
        symbol="512480.SH",
        market="CN",
        display_name="半导体 ETF",
        category="A 股供应链代理",
        reason="用半导体 ETF 观察 AI 基础设施主题是否扩散到 A 股供应链。",
        evidence_gap="缺少该标的的主题新闻缓存，需补新闻验证。",
        next_steps=[
            (
                "lychee data pull news --symbols 512480.SH "
                '--query "AI semiconductor China ETF" --force'
            ),
            "lychee research run --symbol 512480.SH --force",
        ],
    )
    radar_report = OpportunityRadarReport(
        created_at="2026-07-08T00:00:00+00:00",
        status="ready",
        signals=[
            OpportunitySignal(
                symbol="QQQ",
                market="US",
                theme="AI 基础设施扩散",
                score=300,
                news_count=20,
                theme_hits=50,
                volume_rank=10,
                price_snapshot="708.12 USD | 2026-07-07",
                why_it_matters="新闻热度和主题命中同时出现。",
                evidence=["AI infrastructure spending accelerates"],
                next_steps=[],
                drilldown_targets=[target],
            )
        ],
        warnings=[],
        disclaimer="非投资建议。",
    )
    workbench_result = SimpleNamespace(
        candidates=[],
        deepen_result=ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        ),
        fill_result=ResearchGapFillResult(1, [], [], [], []),
    )

    def fake_pull_news(**kwargs: object) -> PullResult:
        return PullResult(
            domain="news",
            provider="test-news",
            count=6,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    result = action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        queue_builder=lambda *args, **kwargs: build_action_queue(
            tmp_path,
            workbench_runner=lambda **kwargs: workbench_result,
            pending_reader=lambda **kwargs: [],
            data_request_reader=lambda **kwargs: [],
            provider_backlog_reader=lambda **kwargs: [],
            radar_reader=lambda **kwargs: radar_report,
        ),
        pull_news=fake_pull_news,
    )

    queue = build_action_queue(
        tmp_path,
        workbench_runner=lambda **kwargs: workbench_result,
        pending_reader=lambda **kwargs: [],
        data_request_reader=lambda **kwargs: [],
        provider_backlog_reader=lambda **kwargs: [],
        radar_reader=lambda **kwargs: radar_report,
    )

    assert result.status == "completed"
    assert result.next_command == "lychee research run --symbol 512480.SH --force"
    assert queue[0].title == "继续研究 半导体 ETF: AI 基础设施扩散"
    assert queue[0].command == "lychee research run --symbol 512480.SH --force"
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT status, next_command
            FROM action_queue_cooldowns
            WHERE command = ?
            """,
            (target.next_steps[0],),
        ).fetchone()
    assert row == ("completed", "lychee research run --symbol 512480.SH --force")


def test_completed_radar_research_followup_leaves_queue(tmp_path: Path) -> None:
    target = OpportunityDrilldownTarget(
        symbol="512480.SH",
        market="CN",
        display_name="半导体 ETF",
        category="A 股供应链代理",
        reason="用半导体 ETF 观察 AI 基础设施主题是否扩散到 A 股供应链。",
        evidence_gap="缺少该标的的主题新闻缓存，需补新闻验证。",
        next_steps=[
            (
                "lychee data pull news --symbols 512480.SH "
                '--query "AI semiconductor China ETF" --force'
            ),
            "lychee research run --symbol 512480.SH --force",
        ],
    )
    radar_report = OpportunityRadarReport(
        created_at="2026-07-08T00:00:00+00:00",
        status="ready",
        signals=[
            OpportunitySignal(
                symbol="QQQ",
                market="US",
                theme="AI 基础设施扩散",
                score=300,
                news_count=20,
                theme_hits=50,
                volume_rank=10,
                price_snapshot="708.12 USD | 2026-07-07",
                why_it_matters="新闻热度和主题命中同时出现。",
                evidence=["AI infrastructure spending accelerates"],
                next_steps=[],
                drilldown_targets=[target],
            )
        ],
        warnings=[],
        disclaimer="非投资建议。",
    )
    workbench_result = SimpleNamespace(
        candidates=[],
        deepen_result=ResearchDeepenResult(
            created_at="2026-07-05T10:00:00+00:00",
            packets=[],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        ),
        fill_result=ResearchGapFillResult(1, [], [], [], []),
    )

    def queue_builder(*args: object, **kwargs: object) -> list[action_queue.ActionQueueItem]:
        return build_action_queue(
            tmp_path,
            workbench_runner=lambda **kwargs: workbench_result,
            pending_reader=lambda **kwargs: [],
            data_request_reader=lambda **kwargs: [],
            provider_backlog_reader=lambda **kwargs: [],
            radar_reader=lambda **kwargs: radar_report,
        )

    def fake_pull_news(**kwargs: object) -> PullResult:
        return PullResult(
            domain="news",
            provider="test-news",
            count=6,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    def fake_run_research(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            status="completed",
            actions=[],
            artifact_path=tmp_path / "research" / "research-run-512480.json",
        )

    action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        queue_builder=queue_builder,
        pull_news=fake_pull_news,
    )
    action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        queue_builder=queue_builder,
        run_research=fake_run_research,
        radar_candidate_writer=lambda **kwargs: tmp_path / "research.sqlite3",
    )

    queue = queue_builder()

    assert queue == []


def test_execute_action_queue_rejects_unsupported_commands(tmp_path: Path) -> None:
    item = action_queue.ActionQueueItem(
        priority=10,
        area="待判定证据",
        title="复核 QQQ 的待判定证据",
        detail="需要人工判断证据方向。",
        command="lychee research evidence-review --symbol QQQ --verdict support",
        source="research-verification-test.json",
    )

    with pytest.raises(ValueError, match="暂不支持自动执行"):
        action_queue.execute_action_queue_item(
            tmp_path,
            action_index=1,
            queue_builder=lambda *args, **kwargs: [item],
        )


def test_execute_action_queue_runs_research_task(tmp_path: Path) -> None:
    item = action_queue.ActionQueueItem(
        priority=20,
        area="机会雷达",
        title="下钻 NVIDIA: AI 基础设施扩散",
        detail="已有行情和主题新闻，可进入下钻核验。",
        command="lychee research run --symbol NVDA --force",
        source="opportunity-radar",
    )
    calls: list[dict[str, object]] = []

    def fake_run_research(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            status="completed",
            actions=[SimpleNamespace(status="completed")],
            artifact_path=tmp_path / "research" / "research-run-nvda.json",
            candidate=SimpleNamespace(symbol="NVDA", display_name="NVIDIA"),
        )

    result = action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        limit=5,
        queue_builder=lambda *args, **kwargs: [item],
        run_research=fake_run_research,
    )

    assert calls == [
        {
            "output_dir": tmp_path,
            "symbol": "NVDA",
            "name": None,
            "limit": 5,
            "force": True,
        }
    ]
    assert result.status == "completed"
    assert result.count == 1
    assert result.output_path == tmp_path / "research" / "research-run-nvda.json"
    assert result.next_command == "lychee research verify --symbol NVDA"


def test_execute_action_queue_records_pending_evidence_review(
    tmp_path: Path,
) -> None:
    item = action_queue.ActionQueueItem(
        priority=10,
        area="待判定证据",
        title="复核 NVIDIA 的待判定证据",
        detail="AI 算力需求是否扩散？ | 系统建议: 无关/排除",
        command=(
            "lychee research evidence-review --symbol NVDA "
            "--text \"Perplexity says it plans to use Nvidia's new CPU\" "
            "--verdict irrelevant "
            '--note "系统暂未识别明确方向，建议先按无关/排除处理。"'
        ),
        source="research-verification-test.json",
    )
    calls: list[dict[str, object]] = []

    def fake_record_review(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            verdict="irrelevant",
            verdict_label="无关/排除",
            evidence_text="Perplexity says it plans to use Nvidia's new CPU",
            note="系统暂未识别明确方向，建议先按无关/排除处理。",
            artifact_path=tmp_path / "research" / "research-evidence-review-nvda.json",
        )

    result = action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        limit=12,
        queue_builder=lambda *args, **kwargs: [item],
        record_evidence_review=fake_record_review,
    )

    assert calls == [
        {
            "output_dir": tmp_path,
            "symbol": "NVDA",
            "name": None,
            "evidence_text": "Perplexity says it plans to use Nvidia's new CPU",
            "verdict": "irrelevant",
            "note": "系统暂未识别明确方向，建议先按无关/排除处理。",
            "limit": 12,
        }
    ]
    assert result.status == "completed"
    assert result.count == 1
    assert result.output_path == tmp_path / "research" / "research-evidence-review-nvda.json"
    assert result.next_command == "lychee research verify --symbol NVDA"
    assert "已记录证据复核" in result.message


def test_execute_action_queue_runs_research_data_request(tmp_path: Path) -> None:
    item = action_queue.ActionQueueItem(
        priority=30,
        area="研究数据请求",
        title="执行 Invesco QQQ Trust 的补数据请求",
        detail="请提供 QQQ 行情和成交量。",
        command="lychee research run-data-request --request 2 --symbol QQQ",
        source="research-memo-test.json",
    )
    request = ResearchDataRequest(
        request_id="memo:test:data-request:2",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="medium",
        request_text="请提供 QQQ 行情和成交量。",
        suggested_commands=["lychee data pull market --symbols QQQ"],
        memo_path="research-memo-test.json",
        verification_path="research-verification-test.json",
    )
    calls: list[dict[str, object]] = []

    def fake_fulfill_data_request(
        output_dir: Path,
        **kwargs: object,
    ) -> ResearchDataRequestFulfillment:
        calls.append({"output_dir": output_dir, **kwargs})
        return ResearchDataRequestFulfillment(
            request=request,
            executions=[
                ResearchDataRequestExecution(
                    action_type="market",
                    status="completed",
                    command="lychee data pull market --symbols QQQ",
                    count=2,
                    output_path=tmp_path / "data" / "market-prices.json",
                    message="行情已刷新。",
                ),
                ResearchDataRequestExecution(
                    action_type="verify",
                    status="completed",
                    command="lychee research verify --symbol QQQ",
                    count=1,
                    output_path=tmp_path / "research" / "verify-qqq.json",
                    message="已重新下钻核验。",
                ),
            ],
        )

    result = action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        limit=7,
        force=False,
        queue_builder=lambda *args, **kwargs: [item],
        fulfill_data_request=fake_fulfill_data_request,
    )

    assert calls == [
        {
            "output_dir": tmp_path,
            "request_index": 2,
            "symbol": "QQQ",
            "name": None,
            "limit": 7,
            "force": False,
        }
    ]
    assert result.status == "completed"
    assert result.count == 3
    assert result.output_path == tmp_path / "research" / "verify-qqq.json"
    assert result.next_command == ""
    assert "已执行研究数据请求" in result.message


def test_execute_action_queue_data_request_no_data_does_not_advance(
    tmp_path: Path,
) -> None:
    item = action_queue.ActionQueueItem(
        priority=30,
        area="研究数据请求",
        title="执行 Invesco QQQ Trust 的补数据请求",
        detail="请提供 QQQ 行情和成交量。",
        command="lychee research run-data-request --request 1 --symbol QQQ",
        source="research-memo-test.json",
    )
    request = ResearchDataRequest(
        request_id="memo:test:data-request:1",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="medium",
        request_text="请提供 QQQ 行情和成交量。",
        suggested_commands=["lychee data pull market --symbols QQQ"],
        memo_path="research-memo-test.json",
        verification_path="research-verification-test.json",
    )

    def fake_fulfill_data_request(
        output_dir: Path,
        **kwargs: object,
    ) -> ResearchDataRequestFulfillment:
        return ResearchDataRequestFulfillment(
            request=request,
            executions=[
                ResearchDataRequestExecution(
                    action_type="market",
                    status="no-data",
                    command="lychee data pull market --symbols QQQ",
                    count=0,
                    output_path=tmp_path / "data" / "market-prices.json",
                    message="没有获取到匹配数据，未改变本地研究证据。",
                ),
                ResearchDataRequestExecution(
                    action_type="verify",
                    status="skipped",
                    command="lychee research verify --symbol QQQ",
                    count=0,
                    output_path=None,
                    message="本次没有改变本地数据，未重新核验。",
                ),
            ],
        )

    result = action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        queue_builder=lambda *args, **kwargs: [item],
        fulfill_data_request=fake_fulfill_data_request,
    )

    assert result.status == "no-data"
    assert result.count == 0
    assert result.next_command == ""
    assert "没有获取到匹配数据" in result.message


def test_execute_action_queue_batch_rebuilds_queue_between_actions(
    tmp_path: Path,
) -> None:
    first = action_queue.ActionQueueItem(
        priority=10,
        area="待判定证据",
        title="复核 NVIDIA 的待判定证据",
        detail="系统建议: 无关/排除",
        command=(
            "lychee research evidence-review --symbol NVDA "
            '--text "first pending evidence" --verdict irrelevant'
        ),
        source="verify-1.json",
    )
    second = action_queue.ActionQueueItem(
        priority=10,
        area="待判定证据",
        title="复核 QQQ 的待判定证据",
        detail="系统建议: 支持证据",
        command=(
            "lychee research evidence-review --symbol QQQ "
            '--text "second pending evidence" --verdict support'
        ),
        source="verify-2.json",
    )
    reviewed: list[str] = []

    def fake_queue_builder(*args: object, **kwargs: object) -> list[action_queue.ActionQueueItem]:
        if not reviewed:
            return [first, second]
        if reviewed == ["first pending evidence"]:
            return [second]
        return []

    def fake_record_review(**kwargs: object) -> SimpleNamespace:
        reviewed.append(str(kwargs["evidence_text"]))
        return SimpleNamespace(
            verdict_label="已复核",
            artifact_path=tmp_path / "research" / f"review-{len(reviewed)}.json",
        )

    result = action_queue.execute_action_queue_batch(
        tmp_path,
        max_actions=3,
        limit=5,
        queue_builder=fake_queue_builder,
        record_evidence_review=fake_record_review,
    )

    assert [execution.item.command for execution in result.executions] == [
        first.command,
        second.command,
    ]
    assert result.status == "completed"
    assert result.stop_reason == "行动队列已清空。"
    assert reviewed == ["first pending evidence", "second pending evidence"]


def test_execute_action_queue_batch_stops_on_no_data_without_repeating(
    tmp_path: Path,
) -> None:
    item = action_queue.ActionQueueItem(
        priority=20,
        area="机会雷达",
        title="下钻 Alibaba: AI 基础设施扩散",
        detail="缺少主题新闻缓存。",
        command='lychee data pull news --symbols BABA --query "AI cloud" --force',
        source="opportunity-radar",
    )
    calls = 0

    def fake_pull_news(**kwargs: object) -> PullResult:
        nonlocal calls
        calls += 1
        return PullResult(
            domain="news",
            provider="test-news",
            count=0,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    result = action_queue.execute_action_queue_batch(
        tmp_path,
        max_actions=3,
        queue_builder=lambda *args, **kwargs: [item],
        pull_news=fake_pull_news,
    )

    assert calls == 1
    assert len(result.executions) == 1
    assert result.status == "no-data"
    assert result.stop_reason == "队列首项没有变化，停止批量推进，避免重复执行同一动作。"


def test_execute_action_queue_batch_continues_after_no_data_when_queue_advances(
    tmp_path: Path,
) -> None:
    first = action_queue.ActionQueueItem(
        priority=20,
        area="机会雷达",
        title="下钻 Alibaba: AI 基础设施扩散",
        detail="缺少主题新闻缓存。",
        command='lychee data pull news --symbols BABA --query "AI cloud" --force',
        source="opportunity-radar",
    )
    second = action_queue.ActionQueueItem(
        priority=20,
        area="机会雷达",
        title="下钻 半导体 ETF: AI 基础设施扩散",
        detail="缺少主题新闻缓存。",
        command=(
            'lychee data pull news --symbols 512480.SH --query "AI chips" --force'
        ),
        source="opportunity-radar",
    )
    calls: list[list[str]] = []

    def fake_queue_builder(
        output_dir: Path,
        **kwargs: object,
    ) -> list[action_queue.ActionQueueItem]:
        if action_queue._action_is_in_cooldown(output_dir, first):
            return [second]
        return [first, second]

    def fake_pull_news(**kwargs: object) -> PullResult:
        symbols = kwargs["symbols"]
        assert isinstance(symbols, list)
        calls.append([str(symbol) for symbol in symbols])
        if symbols == ["BABA"]:
            return PullResult(
                domain="news",
                provider="test-news",
                count=0,
                output_path=tmp_path / "data" / "news-events.json",
                warnings=[],
            )
        return PullResult(
            domain="news",
            provider="test-news",
            count=2,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    result = action_queue.execute_action_queue_batch(
        tmp_path,
        max_actions=2,
        queue_builder=fake_queue_builder,
        pull_news=fake_pull_news,
    )

    assert calls == [["BABA"], ["512480.SH"]]
    assert [execution.status for execution in result.executions] == [
        "no-data",
        "completed",
    ]
    assert result.status == "completed"
    assert result.stop_reason == "已达到本次批量上限 2。"


def test_action_queue_batch_status_preserves_partial_progress(tmp_path: Path) -> None:
    item = action_queue.ActionQueueItem(
        priority=20,
        area="机会雷达",
        title="继续研究 半导体 ETF: AI 基础设施扩散",
        detail="主题新闻已补到，下一步进入研究链。",
        command="lychee research run --symbol 512480.SH --force",
        source="opportunity-radar",
    )
    executions = [
        action_queue.ActionQueueExecution(
            item=item,
            status="cached",
            message="已使用未过期新闻缓存。",
            count=6,
            output_path=tmp_path / "data" / "news-events.json",
            next_command=item.command,
            warnings=[],
        ),
        action_queue.ActionQueueExecution(
            item=item,
            status="partial",
            message="已执行研究任务刷新链。",
            count=3,
            output_path=tmp_path / "research" / "research-run.json",
            next_command="lychee research verify --symbol 512480.SH",
            warnings=[],
        ),
        action_queue.ActionQueueExecution(
            item=item,
            status="no-data",
            message="没有获取到匹配新闻。",
            count=0,
            output_path=tmp_path / "data" / "news-events.json",
            next_command="",
            warnings=[],
        ),
    ]

    assert action_queue._batch_status(executions) == "partial"


def test_execute_action_queue_seeds_radar_target_before_research_run(
    tmp_path: Path,
) -> None:
    item = action_queue.ActionQueueItem(
        priority=20,
        area="机会雷达",
        title="下钻 NVIDIA: AI 基础设施扩散",
        detail="来自 QQQ 雷达信号；已有行情和主题新闻，可进入下钻核验。",
        command="lychee research run --symbol NVDA --force",
        source="opportunity-radar",
    )
    seeded: list[dict[str, object]] = []

    def fake_seed_candidate(**kwargs: object) -> Path:
        seeded.append(kwargs)
        return tmp_path / "research.sqlite3"

    def fake_run_research(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            status="completed",
            actions=[],
            artifact_path=tmp_path / "research" / "research-run-nvda.json",
        )

    action_queue.execute_action_queue_item(
        tmp_path,
        action_index=1,
        queue_builder=lambda *args, **kwargs: [item],
        run_research=fake_run_research,
        radar_candidate_writer=fake_seed_candidate,
    )

    assert seeded == [
        {
            "output_dir": tmp_path,
            "display_name": "NVIDIA",
            "symbol": "NVDA",
            "market": "US",
            "related_theme": "AI 基础设施扩散",
            "why_watch": item.detail,
            "next_actions": [item.command],
        }
    ]
