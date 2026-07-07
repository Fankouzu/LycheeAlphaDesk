from pathlib import Path
from types import SimpleNamespace

from lychee_alphadesk.core.action_queue import build_action_queue
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
        "数据源缺口",
        "研究数据请求",
        "研究任务",
    ]
    assert queue[0].command.startswith("lychee research evidence-review --symbol STX")
    assert queue[1].command.startswith("lychee data set metric --symbol STX")
    assert queue[2].command == "lychee research run-data-request --request 1 --symbol STX"
    assert queue[3].command == "lychee research verify --symbol STX"
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
