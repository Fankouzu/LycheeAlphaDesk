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
