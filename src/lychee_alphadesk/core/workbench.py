import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.live_data import (
    PullResult,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
)
from lychee_alphadesk.core.research import (
    ResearchDeepenResult,
    ResearchGapFillResult,
    ResearchPacket,
    deepen_research_queue,
    fill_research_data_gaps,
)
from lychee_alphadesk.core.research_db import write_research_review_record

PullMarket = Callable[..., PullResult]
PullNews = Callable[..., PullResult]
PullFilings = Callable[..., PullResult]


@dataclass(frozen=True)
class WorkbenchGate:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class CandidateCheck:
    display_name: str
    market: str
    symbol: str | None
    proxy_symbols: list[str]
    evidence_count: int
    gap_count: int
    data_gaps: list[str]
    status: str
    explanation: str
    beginner_question: str
    why_it_matters: str
    observation_entry: str
    what_to_check: str
    next_step: str
    priority: str
    evidence_status: str
    ranking_reason: str = ""
    evidence_quality: str = ""


@dataclass(frozen=True)
class WorkbenchCheckResult:
    created_at: str
    status: str
    packets_checked: int
    ready_count: int
    blocked_count: int
    proxy_price_count: int
    proxy_total: int
    gates: list[WorkbenchGate]
    candidates: list[CandidateCheck]
    beginner_brief: str
    artifact_path: Path | None
    fill_result: ResearchGapFillResult
    deepen_result: ResearchDeepenResult

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True)
class ResearchRunAction:
    action_type: str
    status: str
    symbols: list[str]
    count: int
    output_path: Path | None
    warnings: list[str]
    message: str


@dataclass(frozen=True)
class ResearchRunResult:
    created_at: str
    status: str
    candidate: CandidateCheck
    packet: ResearchPacket | None
    assessment: "ResearchAssessment"
    actions: list[ResearchRunAction]
    detail: str
    artifact_path: Path
    workbench_result: WorkbenchCheckResult


@dataclass(frozen=True)
class ResearchAssessment:
    stage: str
    stage_label: str
    consistency: str
    consistency_label: str
    evidence_reading: str
    next_decision: str


@dataclass(frozen=True)
class ResearchVerificationCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class ResearchDecisionBoard:
    workflow_state: str
    workflow_label: str
    primary_question: str
    decision_rule: str
    suggested_verdict: str
    suggested_verdict_label: str
    next_steps: list[str]


@dataclass(frozen=True)
class ResearchEvidenceChange:
    status: str
    status_label: str
    summary: str
    support_delta: int
    risk_delta: int
    missing_delta: int
    added: dict[str, list[str]] = field(
        default_factory=lambda: _empty_evidence_change_items()
    )
    removed: dict[str, list[str]] = field(
        default_factory=lambda: _empty_evidence_change_items()
    )
    previous_artifact_path: str | None = None
    previous_created_at: str | None = None


@dataclass(frozen=True)
class ResearchVerificationResult:
    created_at: str
    status: str
    status_label: str
    candidate: CandidateCheck
    packet: ResearchPacket | None
    checks: list[ResearchVerificationCheck]
    evidence_board: dict[str, list[str]]
    decision_board: ResearchDecisionBoard
    conclusion: str
    next_actions: list[str]
    artifact_path: Path
    workbench_result: WorkbenchCheckResult
    evidence_change: ResearchEvidenceChange = field(
        default_factory=lambda: _first_research_evidence_change()
    )


@dataclass(frozen=True)
class ResearchReviewResult:
    created_at: str
    verdict: str
    verdict_label: str
    note: str
    evidence_counts: dict[str, int]
    verification: ResearchVerificationResult
    artifact_path: Path
    db_path: Path


@dataclass(frozen=True)
class NewsTopicRelevance:
    terms: list[str]
    matched_rows: list[dict[str, object]]
    support_rows: list[dict[str, object]]
    reverse_rows: list[dict[str, object]]
    neutral_rows: list[dict[str, object]]
    unmatched_rows: list[dict[str, object]]

    @property
    def matched_count(self) -> int:
        return len(self.matched_rows)

    @property
    def support_count(self) -> int:
        return len(self.support_rows)

    @property
    def reverse_count(self) -> int:
        return len(self.reverse_rows)

    @property
    def neutral_count(self) -> int:
        return len(self.neutral_rows)


@dataclass(frozen=True)
class CandidateEvidenceQuality:
    status: str
    support_count: int
    reverse_count: int
    neutral_count: int
    off_topic_count: int

    @property
    def needs_review(self) -> bool:
        return self.status in {"mixed", "needs_review", "missing"}


RESEARCH_REVIEW_VERDICTS = {
    "continue_research": "继续研究",
    "needs_more_evidence": "需要补证据",
    "pause_watch": "暂停观察",
    "blocked": "存在阻塞",
}


def run_workbench_check(
    *,
    output_dir: Path,
    status: str | None = "new",
    limit: int = 5,
    force: bool = False,
    now: datetime | None = None,
    pull_market: PullMarket | None = None,
    pull_filings: PullFilings | None = None,
) -> WorkbenchCheckResult:
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    fill_result = fill_research_data_gaps(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=force,
        pull_market=pull_market or pull_market_prices,
        pull_filings=pull_filings or pull_sec_filings,
    )
    deepen_result = deepen_research_queue(
        output_dir=output_dir,
        status=status,
        limit=limit,
        now=now,
    )
    candidates = _candidate_checks(deepen_result.packets)
    proxy_price_count, proxy_total = _proxy_price_coverage(deepen_result.packets)
    gates = _workbench_gates(candidates, proxy_price_count, proxy_total)
    result_status = "blocked" if any(gate.status == "fail" for gate in gates) else "ready"
    beginner_brief = _beginner_brief(result_status, candidates)
    artifact_path = _write_workbench_check_artifact(
        output_dir=output_dir,
        created_at=created_at,
        status=result_status,
        candidates=candidates,
        gates=gates,
        proxy_price_count=proxy_price_count,
        proxy_total=proxy_total,
        beginner_brief=beginner_brief,
    )
    return WorkbenchCheckResult(
        created_at=created_at,
        status=result_status,
        packets_checked=len(deepen_result.packets),
        ready_count=sum(1 for candidate in candidates if candidate.status == "ready"),
        blocked_count=sum(1 for candidate in candidates if candidate.status == "blocked"),
        proxy_price_count=proxy_price_count,
        proxy_total=proxy_total,
        gates=gates,
        candidates=candidates,
        beginner_brief=beginner_brief,
        artifact_path=artifact_path,
        fill_result=fill_result,
        deepen_result=deepen_result,
    )


def verify_research_task(
    *,
    output_dir: Path,
    symbol: str | None = None,
    name: str | None = None,
    status: str | None = "new",
    limit: int = 5,
    now: datetime | None = None,
    pull_market: PullMarket | None = None,
    pull_filings: PullFilings | None = None,
) -> ResearchVerificationResult:
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    workbench = run_workbench_check(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=False,
        now=now,
        pull_market=pull_market,
        pull_filings=pull_filings,
    )
    selected_index = select_research_candidate_index(
        workbench,
        symbol=symbol,
        name=name,
    )
    if selected_index is None:
        raise ValueError("没有找到匹配的研究任务。")
    candidate = workbench.candidates[selected_index]
    packet = (
        workbench.deepen_result.packets[selected_index]
        if selected_index < len(workbench.deepen_result.packets)
        else None
    )
    checks = build_research_verification_checks(candidate, packet)
    evidence_board = build_research_evidence_board(candidate, packet, checks)
    decision_board = build_research_decision_board(
        candidate,
        packet,
        checks,
        evidence_board,
    )
    evidence_change = build_research_evidence_change(
        output_dir=output_dir,
        candidate=candidate,
        evidence_board=evidence_board,
    )
    verify_status = _verification_status(checks)
    status_label = _verification_status_label(verify_status)
    conclusion = _verification_conclusion(verify_status)
    next_actions = _verification_next_actions(checks, candidate)
    artifact_path = _write_research_verification_artifact(
        output_dir=output_dir,
        created_at=created_at,
        status=verify_status,
        status_label=status_label,
        candidate=candidate,
        checks=checks,
        evidence_board=evidence_board,
        decision_board=decision_board,
        evidence_change=evidence_change,
        conclusion=conclusion,
        next_actions=next_actions,
    )
    return ResearchVerificationResult(
        created_at=created_at,
        status=verify_status,
        status_label=status_label,
        candidate=candidate,
        packet=packet,
        checks=checks,
        evidence_board=evidence_board,
        decision_board=decision_board,
        evidence_change=evidence_change,
        conclusion=conclusion,
        next_actions=next_actions,
        artifact_path=artifact_path,
        workbench_result=workbench,
    )


def record_research_review(
    *,
    output_dir: Path,
    verdict: str,
    note: str = "",
    symbol: str | None = None,
    name: str | None = None,
    status: str | None = "new",
    limit: int = 5,
    now: datetime | None = None,
) -> ResearchReviewResult:
    if verdict not in RESEARCH_REVIEW_VERDICTS:
        allowed = ", ".join(RESEARCH_REVIEW_VERDICTS)
        raise ValueError(f"未知复核判断: {verdict}。可选值: {allowed}")
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    verification = verify_research_task(
        output_dir=output_dir,
        symbol=symbol,
        name=name,
        status=status,
        limit=limit,
        now=now,
    )
    evidence_counts = {
        "support": len(verification.evidence_board["support"]),
        "risk": len(verification.evidence_board["risk"]),
        "missing": len(verification.evidence_board["missing"]),
    }
    cleaned_note = note.strip() or "未填写复核备注。"
    verdict_label = RESEARCH_REVIEW_VERDICTS[verdict]
    payload = _research_review_payload(
        created_at=created_at,
        verdict=verdict,
        verdict_label=verdict_label,
        note=cleaned_note,
        evidence_counts=evidence_counts,
        verification=verification,
    )
    artifact_path = _write_research_review_artifact(
        output_dir=output_dir,
        created_at=created_at,
        payload=payload,
    )
    db_path = write_research_review_record(
        output_dir=output_dir,
        review_id=str(payload["review_id"]),
        created_at=created_at,
        display_name=verification.candidate.display_name,
        symbol=verification.candidate.symbol,
        market=verification.candidate.market,
        verdict=verdict,
        verdict_label=verdict_label,
        note=cleaned_note,
        support_count=evidence_counts["support"],
        risk_count=evidence_counts["risk"],
        missing_count=evidence_counts["missing"],
        review_path=artifact_path,
        verification_path=verification.artifact_path,
        payload=payload,
    )
    return ResearchReviewResult(
        created_at=created_at,
        verdict=verdict,
        verdict_label=verdict_label,
        note=cleaned_note,
        evidence_counts=evidence_counts,
        verification=verification,
        artifact_path=artifact_path,
        db_path=db_path,
    )


def run_research_task(
    *,
    output_dir: Path,
    symbol: str | None = None,
    name: str | None = None,
    status: str | None = "new",
    limit: int = 5,
    force: bool = False,
    now: datetime | None = None,
    pull_market: PullMarket | None = None,
    pull_news: PullNews | None = None,
    pull_filings: PullFilings | None = None,
) -> ResearchRunResult:
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    initial = run_workbench_check(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=False,
        now=now,
        pull_market=pull_market,
        pull_filings=pull_filings,
    )
    selected_index = select_research_candidate_index(
        initial,
        symbol=symbol,
        name=name,
    )
    if selected_index is None:
        raise ValueError("没有找到匹配的研究任务。")

    candidate = initial.candidates[selected_index]
    packet = (
        initial.deepen_result.packets[selected_index]
        if selected_index < len(initial.deepen_result.packets)
        else None
    )
    actions = _run_research_refresh_actions(
        candidate=candidate,
        packet=packet,
        output_dir=output_dir,
        force=force,
        pull_market=pull_market or pull_market_prices,
        pull_news=pull_news or pull_news_events,
        pull_filings=pull_filings or pull_sec_filings,
    )
    refreshed = run_workbench_check(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=False,
        now=now,
        pull_market=pull_market,
        pull_filings=pull_filings,
    )
    refreshed_index = select_research_candidate_index(
        refreshed,
        symbol=symbol or candidate.symbol,
        name=name or candidate.display_name,
    )
    if refreshed_index is None:
        refreshed_index = selected_index
    refreshed_candidate = refreshed.candidates[refreshed_index]
    refreshed_packet = (
        refreshed.deepen_result.packets[refreshed_index]
        if refreshed_index < len(refreshed.deepen_result.packets)
        else None
    )
    action_status = _research_run_action_status(actions)
    detail = render_research_task_detail(
        refreshed_candidate,
        refreshed_packet,
        action_status=action_status,
    )
    assessment = build_research_assessment(refreshed_candidate, refreshed_packet)
    run_status = "completed" if all(action.status != "failed" for action in actions) else "partial"
    artifact_path = _write_research_run_artifact(
        output_dir=output_dir,
        created_at=created_at,
        status=run_status,
        candidate=refreshed_candidate,
        assessment=assessment,
        actions=actions,
        detail=detail,
    )
    return ResearchRunResult(
        created_at=created_at,
        status=run_status,
        candidate=refreshed_candidate,
        packet=refreshed_packet,
        assessment=assessment,
        actions=actions,
        detail=detail,
        artifact_path=artifact_path,
        workbench_result=refreshed,
    )


def build_research_verification_checks(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[ResearchVerificationCheck]:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    price = _dict_value(local_data.get("price"))
    evidence = _dict_list(packet_payload.get("evidence"))
    related_news = _dict_list(local_data.get("related_news"))
    filings = _dict_list(local_data.get("filings"))
    data_gaps = _text_list(packet_payload.get("data_gaps")) or candidate.data_gaps
    asset_type = _asset_type(packet)
    checks: list[ResearchVerificationCheck] = []
    if data_gaps:
        checks.append(
            ResearchVerificationCheck(
                name="数据缺口",
                status="fail",
                detail="；".join(data_gaps),
            )
        )
    if price:
        checks.append(
            ResearchVerificationCheck(
                name="行情核验",
                status="pass",
                detail=_price_line(price).removeprefix("行情: "),
            )
        )
        volume = price.get("volume")
        volume_ok = isinstance(volume, int | float) and volume > 0
        checks.append(
            ResearchVerificationCheck(
                name="成交量核验",
                status="pass" if volume_ok else "warn",
                detail=f"成交量 {volume}" if volume is not None else "缺少成交量字段。",
            )
        )
    else:
        checks.append(
            ResearchVerificationCheck(
                name="行情核验",
                status="fail",
                detail="缺少本地行情，无法核验价格和成交量。",
            )
        )
    news_count = len(evidence) + len(related_news)
    topic_relevance = _news_topic_relevance(candidate, packet)
    checks.append(
        ResearchVerificationCheck(
            name="新闻核验",
            status="pass" if news_count else "fail",
            detail=f"可核验新闻/证据 {news_count} 条。"
            if news_count
            else "缺少 discovery 证据或相关新闻。",
        )
    )
    checks.append(
        ResearchVerificationCheck(
            name="主题相关性核验",
            status=_topic_relevance_status(news_count, topic_relevance.matched_count),
            detail=_topic_relevance_detail(news_count, topic_relevance),
        )
    )
    checks.append(
        ResearchVerificationCheck(
            name="证据方向核验",
            status=_evidence_direction_status(news_count, topic_relevance),
            detail=_evidence_direction_detail(news_count, topic_relevance),
        )
    )
    filings_required = candidate.market.upper() == "US" and asset_type == "stock"
    if filings_required:
        checks.append(
            ResearchVerificationCheck(
                name="公告/财报核验",
                status="pass" if filings else "fail",
                detail=f"可核验公告/财报 {len(filings)} 条。"
                if filings
                else "美股股票缺少 SEC 公告/财报线索。",
            )
        )
    else:
        checks.append(
            ResearchVerificationCheck(
                name="公告/财报核验",
                status="na",
                detail="当前任务不要求 SEC 公告/财报核验。",
            )
        )
    if candidate.proxy_symbols:
        checks.append(
            ResearchVerificationCheck(
                name="代理标的核验",
                status="warn",
                detail="代理标的需要人工核对成分、费用、流动性和可交易性。",
            )
        )
    checks.append(
        ResearchVerificationCheck(
            name="一致性核验",
            status="warn" if all(check.status != "fail" for check in checks) else "fail",
            detail="待人工核验行情、成交量、新闻和公告/财报是否指向同一主题。",
        )
    )
    return checks


def build_research_evidence_board(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    checks: list[ResearchVerificationCheck],
) -> dict[str, list[str]]:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    price = _dict_value(local_data.get("price"))
    filings = _dict_list(local_data.get("filings"))
    support: list[str] = []
    risk: list[str] = []
    missing: list[str] = []
    if price:
        support.append(_price_line(price).removeprefix("行情: "))
    topic_relevance = _news_topic_relevance(candidate, packet)
    for row in topic_relevance.support_rows[:3]:
        headline = _string_value(row.get("headline")) or "未命名新闻证据"
        support.append(f"新闻: {headline}")
    for row in topic_relevance.reverse_rows[:3]:
        headline = _string_value(row.get("headline")) or "未命名新闻证据"
        risk.append(f"反向证据: {headline}")
    for row in topic_relevance.neutral_rows[:3]:
        headline = _string_value(row.get("headline")) or "未命名新闻证据"
        risk.append(f"新闻待判定: {headline} 命中主题但方向未明。")
    for row in topic_relevance.unmatched_rows[:3]:
        headline = _string_value(row.get("headline")) or "未命名新闻证据"
        risk.append(f"新闻待查: {headline} 未命中研究主题关键词。")
    for row in filings[:2]:
        form = _string_value(row.get("form")) or "公告"
        date = _string_value(row.get("date"))
        summary = _string_value(row.get("summary"))
        filing = f"公告/财报: {form}"
        if date:
            filing += f" {date}"
        if summary:
            filing += f" - {summary}"
        support.append(filing)
    for check in checks:
        if check.status == "fail":
            missing.append(f"{check.name}: {check.detail}")
        elif check.status == "warn":
            risk.append(f"{check.name}: {check.detail}")
    if candidate.proxy_symbols:
        risk.append(
            "代理标的: "
            + ", ".join(candidate.proxy_symbols)
            + " 需要人工核对成分、费用、流动性和可交易性。"
        )
    return {
        "support": support,
        "risk": risk,
        "missing": missing,
    }


def build_research_decision_board(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    checks: list[ResearchVerificationCheck],
    evidence_board: dict[str, list[str]],
) -> ResearchDecisionBoard:
    failed = [check for check in checks if check.status == "fail"]
    topic_relevance = _news_topic_relevance(candidate, packet)
    question = candidate.beginner_question.strip() or (
        f"{candidate.display_name} 这条研究线索是否有足够证据继续下钻？"
    )
    if failed:
        return _research_decision_board(
            workflow_state="blocked",
            workflow_label="存在阻塞",
            primary_question=question,
            decision_rule="至少一个必要核验失败，先补齐阻塞项再继续研究。",
            suggested_verdict="blocked",
            next_steps=[f"先处理{check.name}: {check.detail}" for check in failed[:3]],
        )
    if topic_relevance.matched_count == 0:
        return _research_decision_board(
            workflow_state="evidence_review",
            workflow_label="先复核主题相关性",
            primary_question=question,
            decision_rule="现有新闻或证据没有命中研究主题关键词，不能把市场噪音当成研究依据。",
            suggested_verdict="needs_more_evidence",
            next_steps=[
                "刷新主题新闻，并确认风险栏新闻是否真的回答研究问题。",
                "如果仍无主题证据，先记录需要补证据，暂不生成研究备忘录。",
            ],
        )
    if topic_relevance.reverse_count:
        return _research_decision_board(
            workflow_state="risk_review",
            workflow_label="先看反向证据",
            primary_question=question,
            decision_rule="已出现反向证据，必须先解释风险栏内容，再决定是否继续研究。",
            suggested_verdict="needs_more_evidence",
            next_steps=[
                "把反向证据逐条标注为真实反向、短期噪音或仍待判定。",
                "补充同主题行情、成交量或公告证据，检查反向证据是否被数据确认。",
            ],
        )
    if topic_relevance.neutral_count:
        return _research_decision_board(
            workflow_state="direction_review",
            workflow_label="拆分证据方向",
            primary_question=question,
            decision_rule="主题相关性已通过，但部分新闻方向待判定，暂时不能归入支持证据。",
            suggested_verdict="needs_more_evidence",
            next_steps=[
                "把新闻待判定逐条归类为支持、反向或无关。",
                "只保留能回答研究问题的证据，再进入人工一致性复核。",
            ],
        )
    if candidate.proxy_symbols:
        return _research_decision_board(
            workflow_state="proxy_review",
            workflow_label="先核对代理标的",
            primary_question=question,
            decision_rule="当前任务依赖代理标的，必须先确认代理是否覆盖原主题。",
            suggested_verdict="needs_more_evidence",
            next_steps=[
                "核对代理标的的成分、费用、成交额和是否可交易。",
                "代理通过后再把代理行情和主题新闻放到同一证据板复核。",
            ],
        )
    if not evidence_board.get("support"):
        return _research_decision_board(
            workflow_state="evidence_review",
            workflow_label="先补支持证据",
            primary_question=question,
            decision_rule="证据板没有可用支持证据，不能进入一致性复核。",
            suggested_verdict="needs_more_evidence",
            next_steps=[
                "补充行情、成交量、主题新闻或公告/财报证据。",
                "支持栏出现可核验证据后再运行下钻核验。",
            ],
        )
    return _research_decision_board(
        workflow_state="ready_for_review",
        workflow_label="可进入人工一致性复核",
        primary_question=question,
        decision_rule="支持证据存在，暂无阻塞、反向证据或待补项，证据可以进入人工一致性复核。",
        suggested_verdict="continue_research",
        next_steps=[
            "记录继续研究，并进入人工一致性复核。",
            "对照支持证据与风险栏，确认它们是否回答同一个研究问题。",
            "下一步可生成研究备忘录，但仍不能得出买卖结论。",
        ],
    )


def _research_decision_board(
    *,
    workflow_state: str,
    workflow_label: str,
    primary_question: str,
    decision_rule: str,
    suggested_verdict: str,
    next_steps: list[str],
) -> ResearchDecisionBoard:
    return ResearchDecisionBoard(
        workflow_state=workflow_state,
        workflow_label=workflow_label,
        primary_question=primary_question,
        decision_rule=decision_rule,
        suggested_verdict=suggested_verdict,
        suggested_verdict_label=RESEARCH_REVIEW_VERDICTS[suggested_verdict],
        next_steps=next_steps,
    )


def build_research_evidence_change(
    *,
    output_dir: Path,
    candidate: CandidateCheck,
    evidence_board: dict[str, list[str]],
) -> ResearchEvidenceChange:
    previous = _latest_matching_verification_artifact(output_dir, candidate)
    if previous is None:
        return _first_research_evidence_change()

    previous_payload = _read_json_dict(previous)
    previous_board = _dict_value(previous_payload.get("evidence_board"))
    previous_counts = _evidence_board_counts(previous_board)
    current_counts = _evidence_board_counts(evidence_board)
    added, removed = _evidence_board_diff(previous_board, evidence_board)
    support_delta = current_counts["support"] - previous_counts["support"]
    risk_delta = current_counts["risk"] - previous_counts["risk"]
    missing_delta = current_counts["missing"] - previous_counts["missing"]
    status, status_label = _evidence_change_status(
        support_delta,
        risk_delta,
        missing_delta,
    )
    return ResearchEvidenceChange(
        status=status,
        status_label=status_label,
        summary=_evidence_change_summary(
            support_delta,
            risk_delta,
            missing_delta,
        ),
        support_delta=support_delta,
        risk_delta=risk_delta,
        missing_delta=missing_delta,
        added=added,
        removed=removed,
        previous_artifact_path=str(previous),
        previous_created_at=_string_value(previous_payload.get("created_at")) or None,
    )


def _first_research_evidence_change() -> ResearchEvidenceChange:
    return ResearchEvidenceChange(
        status="first_check",
        status_label="首次核验",
        summary="没有找到同一研究任务的上一份下钻核验，先把本次结果作为基线。",
        support_delta=0,
        risk_delta=0,
        missing_delta=0,
        added=_empty_evidence_change_items(),
        removed=_empty_evidence_change_items(),
    )


def research_evidence_change_detail_groups(
    change: ResearchEvidenceChange,
) -> list[tuple[str, list[str]]]:
    return [
        ("新增支持证据", change.added["support"]),
        ("已移除支持证据", change.removed["support"]),
        ("新增风险/反向待查", change.added["risk"]),
        ("已移除风险/反向待查", change.removed["risk"]),
        ("新增待补证据", change.added["missing"]),
        ("已补掉待补证据", change.removed["missing"]),
    ]


def _latest_matching_verification_artifact(
    output_dir: Path,
    candidate: CandidateCheck,
) -> Path | None:
    research_dir = output_dir / "research"
    if not research_dir.exists():
        return None
    for path in sorted(research_dir.glob("research-verification-*.json"), reverse=True):
        payload = _read_json_dict(path)
        payload_candidate = _dict_value(payload.get("candidate"))
        if _verification_candidate_matches(payload_candidate, candidate):
            return path
    return None


def _verification_candidate_matches(
    payload_candidate: dict[str, object],
    candidate: CandidateCheck,
) -> bool:
    if (_string_value(payload_candidate.get("market")) or "").upper() != (
        candidate.market or ""
    ).upper():
        return False
    payload_symbols = [
        _string_value(payload_candidate.get("symbol")),
        *_text_list(payload_candidate.get("proxy_symbols")),
    ]
    current_symbols = [candidate.symbol or "", *candidate.proxy_symbols]
    payload_symbol_set = {symbol.upper() for symbol in payload_symbols if symbol}
    current_symbol_set = {symbol.upper() for symbol in current_symbols if symbol}
    if payload_symbol_set and current_symbol_set:
        return bool(payload_symbol_set & current_symbol_set)
    payload_name = (_string_value(payload_candidate.get("display_name")) or "").lower()
    return bool(payload_name and payload_name == candidate.display_name.lower())


def _read_json_dict(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _dict_value(payload)


def _evidence_board_counts(board: Mapping[str, object]) -> dict[str, int]:
    return {
        "support": len(_text_list(board.get("support"))),
        "risk": len(_text_list(board.get("risk"))),
        "missing": len(_text_list(board.get("missing"))),
    }


def _empty_evidence_change_items() -> dict[str, list[str]]:
    return {
        "support": [],
        "risk": [],
        "missing": [],
    }


def _evidence_board_diff(
    previous_board: Mapping[str, object],
    current_board: Mapping[str, object],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    added = _empty_evidence_change_items()
    removed = _empty_evidence_change_items()
    for key in ("support", "risk", "missing"):
        previous_items = _dedupe_text_list(_text_list(previous_board.get(key)))
        current_items = _dedupe_text_list(_text_list(current_board.get(key)))
        previous_set = set(previous_items)
        current_set = set(current_items)
        added[key] = [item for item in current_items if item not in previous_set]
        removed[key] = [item for item in previous_items if item not in current_set]
    return added, removed


def _dedupe_text_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _evidence_change_status(
    support_delta: int,
    risk_delta: int,
    missing_delta: int,
) -> tuple[str, str]:
    if support_delta == 0 and risk_delta == 0 and missing_delta == 0:
        return "unchanged", "证据未变化"
    score = support_delta - risk_delta - missing_delta
    if score > 0:
        return "improved", "证据增强"
    if score < 0:
        return "weaker", "证据压力增加"
    return "mixed", "证据变化混合"


def _evidence_change_summary(
    support_delta: int,
    risk_delta: int,
    missing_delta: int,
) -> str:
    parts = [
        _delta_phrase("支持证据", support_delta),
        _delta_phrase("风险/反向待查", risk_delta),
        _delta_phrase("待补证据", missing_delta),
    ]
    return "；".join(parts) + "。"


def _delta_phrase(label: str, delta: int) -> str:
    if delta > 0:
        return f"{label}增加 {delta}"
    if delta < 0:
        return f"{label}减少 {abs(delta)}"
    return f"{label}无变化"


def _topic_relevance_status(news_count: int, matched_count: int) -> str:
    if news_count == 0:
        return "fail"
    if matched_count > 0:
        return "pass"
    return "warn"


def _topic_relevance_detail(
    news_count: int,
    topic_relevance: NewsTopicRelevance,
) -> str:
    if news_count == 0:
        return "缺少新闻或 discovery 证据，无法检查主题相关性。"
    terms = ", ".join(topic_relevance.terms[:6])
    if topic_relevance.matched_count > 0:
        return (
            f"可核验证据 {news_count} 条，其中 {topic_relevance.matched_count} "
            f"条命中研究主题关键词: {terms}。"
        )
    return (
        f"有 {news_count} 条新闻/证据，但没有命中研究主题关键词，"
        f"需要人工判断是否只是市场噪音。关键词: {terms}。"
    )


def _evidence_direction_status(
    news_count: int,
    topic_relevance: NewsTopicRelevance,
) -> str:
    if news_count == 0 or topic_relevance.matched_count == 0:
        return "fail" if news_count == 0 else "warn"
    if topic_relevance.reverse_count or topic_relevance.neutral_count:
        return "warn"
    return "pass"


def _evidence_direction_detail(
    news_count: int,
    topic_relevance: NewsTopicRelevance,
) -> str:
    if news_count == 0:
        return "缺少新闻或 discovery 证据，无法判断证据方向。"
    if topic_relevance.matched_count == 0:
        return "没有主题相关新闻，无法判断支持或反向方向。"
    parts = [
        f"支持 {topic_relevance.support_count} 条",
        f"反向 {topic_relevance.reverse_count} 条",
        f"方向待判定 {topic_relevance.neutral_count} 条",
    ]
    if topic_relevance.reverse_count:
        return "发现反向证据，必须进入风险或反向待查: " + "；".join(parts) + "。"
    if topic_relevance.neutral_count:
        return "部分相关新闻方向未明，需要人工核验: " + "；".join(parts) + "。"
    return "相关新闻方向初步支持研究问题: " + "；".join(parts) + "。"


def _news_topic_relevance(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> NewsTopicRelevance:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    rows = [
        *_dict_list(packet_payload.get("evidence")),
        *_dict_list(local_data.get("related_news")),
    ]
    terms = _topic_terms(candidate, packet)
    matched_rows: list[dict[str, object]] = []
    support_rows: list[dict[str, object]] = []
    reverse_rows: list[dict[str, object]] = []
    neutral_rows: list[dict[str, object]] = []
    unmatched_rows: list[dict[str, object]] = []
    for row in rows:
        text = _news_text(row)
        if _text_matches_any_topic_term(text, terms):
            matched_rows.append(row)
            direction = _news_evidence_direction(text)
            if direction == "reverse":
                reverse_rows.append(row)
            elif direction == "support":
                support_rows.append(row)
            else:
                neutral_rows.append(row)
        else:
            unmatched_rows.append(row)
    return NewsTopicRelevance(
        terms=terms,
        matched_rows=matched_rows,
        support_rows=support_rows,
        reverse_rows=reverse_rows,
        neutral_rows=neutral_rows,
        unmatched_rows=unmatched_rows,
    )


def _topic_terms(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    packet_payload = packet.packet if packet is not None else {}
    packet_candidate = _dict_value(packet_payload.get("candidate"))
    raw_values = [
        candidate.symbol or "",
        candidate.display_name,
        candidate.observation_entry,
        candidate.beginner_question,
        _string_value(packet_candidate.get("related_theme")),
        _string_value(packet_candidate.get("why_watch")),
    ]
    terms: list[str] = []
    for value in raw_values:
        terms.extend(_extract_topic_terms(value))
    if (candidate.symbol or "").upper() == "QQQ":
        terms.extend(["qqq", "nasdaq", "nasdaq 100", "纳指", "科技"])
    return _dedupe_terms(terms)


def _extract_topic_terms(value: str) -> list[str]:
    terms: list[str] = []
    lowered = value.lower()
    for token in re.findall(r"[a-z0-9][a-z0-9.+-]*", lowered):
        if len(token) >= 3 or token in {"ai"}:
            terms.append(token)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        terms.append(chunk)
        terms.extend(keyword for keyword in _CHINESE_TOPIC_KEYWORDS if keyword in chunk)
    return terms


_CHINESE_TOPIC_KEYWORDS = {
    "人工智能",
    "数据中心",
    "供应链",
    "半导体",
    "机器人",
    "自动驾驶",
    "电动车",
    "科技",
    "美股",
    "港股",
    "A股",
    "指数",
    "消费",
    "政策",
    "流动性",
    "利率",
    "大盘",
    "存储",
    "硬盘",
    "需求",
    "云",
}


def _dedupe_terms(terms: list[str]) -> list[str]:
    stop_terms = {
        "the",
        "and",
        "with",
        "this",
        "that",
        "research",
        "观察",
        "研究",
        "主题",
        "当前",
        "是否",
        "什么",
        "可以",
        "用于",
    }
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = term.strip().lower()
        if not cleaned or cleaned in stop_terms or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


def _news_text(row: dict[str, object]) -> str:
    return " ".join(
        item
        for item in [
            _string_value(row.get("headline")),
            _string_value(row.get("summary")),
            _string_value(row.get("title")),
            _string_value(row.get("description")),
        ]
        if item
    ).lower()


def _news_evidence_direction(text: str) -> str:
    positive = _count_signal_terms(text, _POSITIVE_EVIDENCE_TERMS)
    negative = _count_signal_terms(text, _NEGATIVE_EVIDENCE_TERMS)
    if negative > positive:
        return "reverse"
    if positive > 0:
        return "support"
    return "neutral"


_POSITIVE_EVIDENCE_TERMS = {
    "rise",
    "rises",
    "rising",
    "rose",
    "growth",
    "grow",
    "grows",
    "improve",
    "improved",
    "improves",
    "strong",
    "stronger",
    "beat",
    "beats",
    "expand",
    "expands",
    "surge",
    "surges",
    "increase",
    "increases",
    "higher",
    "robust",
    "record",
    "上升",
    "上涨",
    "增长",
    "改善",
    "强劲",
    "超预期",
    "扩张",
    "增加",
    "创新高",
    "复苏",
}


_NEGATIVE_EVIDENCE_TERMS = {
    "fall",
    "falls",
    "falling",
    "fell",
    "decline",
    "declines",
    "declining",
    "weak",
    "weaker",
    "cut",
    "cuts",
    "reduce",
    "reduces",
    "slowdown",
    "slows",
    "pressure",
    "pressures",
    "miss",
    "misses",
    "risk",
    "risks",
    "lower",
    "loss",
    "losses",
    "drop",
    "drops",
    "down",
    "contract",
    "contracts",
    "下降",
    "下滑",
    "放缓",
    "疲弱",
    "削减",
    "压力",
    "不及预期",
    "风险",
    "减少",
    "亏损",
    "降低",
}


def _count_signal_terms(text: str, terms: set[str]) -> int:
    count = 0
    for term in terms:
        if re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", term):
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
                count += 1
        elif term and term in text:
            count += 1
    return count


def _text_matches_any_topic_term(text: str, terms: list[str]) -> bool:
    for term in terms:
        if re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", term):
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
                return True
        elif term and term in text:
            return True
    return False


def select_research_candidate_index(
    result: WorkbenchCheckResult,
    *,
    symbol: str | None,
    name: str | None,
) -> int | None:
    if symbol:
        target = symbol.strip().upper()
        for index, candidate in enumerate(result.candidates):
            symbols = [candidate.symbol or "", *candidate.proxy_symbols]
            if any(item.upper() == target for item in symbols):
                return index
        return None
    if name:
        target_name = name.strip().lower()
        for index, candidate in enumerate(result.candidates):
            display_name = candidate.display_name.lower()
            if display_name == target_name or target_name in display_name:
                return index
        return None
    return 0 if result.candidates else None


def beginner_research_brief(packets: list[ResearchPacket]) -> str:
    candidates = _candidate_checks(packets)
    status = "blocked" if any(candidate.data_gaps for candidate in candidates) else "ready"
    return _beginner_brief(status, candidates)


def render_research_task_detail(
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
    commands = research_action_commands(candidate, packet)
    assessment = build_research_assessment(candidate, packet)
    lines = [
        "研究任务面板",
        f"任务: {candidate.display_name} [{candidate.market}]",
        f"入口: {candidate.observation_entry}",
        f"优先级: {candidate.priority}",
        f"排序理由: {candidate.ranking_reason}",
        f"证据状态: {candidate.evidence_status}",
        "",
        *_research_start_lines(candidate),
        "",
        "研究状态",
        f"- 阶段: {assessment.stage_label}",
        f"- 一致性: {assessment.consistency_label}",
        f"- 证据读数: {assessment.evidence_reading}",
        f"- 下一步判断: {assessment.next_decision}",
        "",
        "信号读数: "
        + _signal_reading(candidate, price, evidence, related_news, filings, data_gaps),
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


def _research_start_lines(candidate: CandidateCheck) -> list[str]:
    selector = _research_selector_arg(candidate)
    lines = [
        "本次研究要解决的问题",
        f"- {candidate.beginner_question}",
        "",
        "研究启动",
        f"- 核验目标: {candidate.what_to_check}",
    ]
    if candidate.proxy_symbols and not candidate.symbol:
        lines.append(
            "- 先核验代理: 确认代理标的是否真的覆盖原主题、是否有足够流动性。"
        )
    lines.extend(
        [
            f"- 第一步: lychee research verify {selector}",
            "- 看证据板: 支持证据 / 风险或反向待查 / 待补证据",
            (
                f"- 记录判断: lychee research review {selector} "
                '--verdict needs_more_evidence --note "写下还缺什么"'
            ),
            f"- 可选 LLM: lychee research memo {selector}",
        ]
    )
    return lines


def _research_selector_arg(candidate: CandidateCheck) -> str:
    symbols = research_action_symbols(candidate)
    if symbols:
        return f"--symbol {symbols[0]}"
    safe_name = candidate.display_name.replace('"', '\\"')
    return f'--name "{safe_name}"'


def build_research_assessment(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> ResearchAssessment:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    price = _dict_value(local_data.get("price"))
    evidence = _dict_list(packet_payload.get("evidence"))
    related_news = _dict_list(local_data.get("related_news"))
    filings = _dict_list(local_data.get("filings"))
    data_gaps = _text_list(packet_payload.get("data_gaps")) or candidate.data_gaps
    asset_type = _asset_type(packet)
    if data_gaps:
        return ResearchAssessment(
            stage="blocked",
            stage_label="待补数据",
            consistency="blocked",
            consistency_label="无法核验",
            evidence_reading="还有数据缺口，不能判断证据是否互相支持。",
            next_decision="先补齐数据缺口，再重新运行研究执行链。",
        )
    if candidate.evidence_quality in {"missing", "needs_review", "mixed"}:
        return ResearchAssessment(
            stage="evidence_review",
            stage_label="先复核证据",
            consistency="pending_evidence_direction_review",
            consistency_label="待核验",
            evidence_reading=_evidence_review_reading(candidate.evidence_quality),
            next_decision="先运行下钻核验复核证据方向，再决定是否继续研究。",
        )
    if candidate.proxy_symbols and not candidate.symbol:
        return ResearchAssessment(
            stage="proxy_review",
            stage_label="代理核验",
            consistency="pending_proxy_review",
            consistency_label="待核验",
            evidence_reading="这条线索通过代理标的观察，必须先确认代理是否覆盖主题。",
            next_decision="核对代理成分、流动性、费用和可交易性，再决定是否下钻。",
        )
    has_news = bool(evidence or related_news)
    filings_required = candidate.market.upper() == "US" and asset_type == "stock"
    filings_ready = not filings_required or bool(filings)
    if price and has_news and filings_ready:
        return ResearchAssessment(
            stage="ready_for_drilldown",
            stage_label="可下钻研究",
            consistency="pending_review",
            consistency_label="待核验",
            evidence_reading=_ready_evidence_reading(filings_required),
            next_decision="进入下钻核验，检查行情、成交量、新闻和公告/财报是否指向同一主题。",
        )
    if price or has_news or filings:
        return ResearchAssessment(
            stage="evidence_building",
            stage_label="继续补证据",
            consistency="insufficient",
            consistency_label="证据不足",
            evidence_reading="已经有部分材料，但还不足以判断这条线索是否值得下钻。",
            next_decision="继续补行情、新闻或公告/财报，再重新判断。",
        )
    return ResearchAssessment(
        stage="empty",
        stage_label="仅保留线索",
        consistency="insufficient",
        consistency_label="证据不足",
        evidence_reading="当前没有可核验的行情、新闻或公告/财报材料。",
        next_decision="先刷新数据，再决定是否进入研究队列。",
    )


def _evidence_review_reading(evidence_quality: str) -> str:
    if evidence_quality == "missing":
        return "还没有命中研究主题的新闻证据，不能把市场噪音当作支持材料。"
    if evidence_quality == "mixed":
        return "支持证据和反向/待判定证据同时存在，必须先拆分证据方向。"
    return "证据方向还没有形成支持，不能直接进入下钻结论。"


def _ready_evidence_reading(filings_required: bool) -> str:
    if filings_required:
        return "行情、消息和公告/财报材料都已出现，可以开始核验它们是否同向。"
    return "行情和消息材料都已出现，可以开始核验它们是否同向。"


def research_detail_actions(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[tuple[str, str]]:
    actions = [
        ("refresh_market", "刷新行情"),
        ("refresh_news", "刷新新闻"),
    ]
    if _needs_topic_news_refresh(candidate) and topic_news_query(candidate, packet):
        actions.append(("refresh_topic_news", "刷新主题新闻"))
    if research_filing_symbols(candidate, packet):
        actions.append(("refresh_filings", "刷新美股公告/财报"))
    actions.append(("verify_research", "下钻核验"))
    actions.append(("generate_memo", "生成研究备忘录"))
    actions.append(("back_tasks", "返回研究任务列表"))
    return actions


def research_action_commands(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    symbols = research_action_symbols(candidate)
    commands: list[str] = []
    if symbols:
        symbol_text = ",".join(symbols)
        topic_query = topic_news_query(candidate, packet)
        commands.append(
            f"刷新行情: lychee data pull market --symbols {symbol_text} "
            "--provider auto --force"
        )
        commands.append(
            f"刷新新闻: lychee data pull news --symbols {symbol_text} "
            "--provider auto --force"
        )
        if _needs_topic_news_refresh(candidate) and topic_query:
            commands.append(
                f'刷新主题新闻: lychee data pull news --symbols {symbol_text} '
                f'--query "{_escape_command_arg(topic_query)}" --provider auto --force'
            )
    else:
        commands.append("刷新行情/新闻: 需要先完成可观察入口映射。")
    filing_symbols = research_filing_symbols(candidate, packet)
    if filing_symbols:
        commands.append(
            f"刷新美股公告/财报: lychee data pull filings --symbols "
            f"{','.join(filing_symbols)}"
        )
    if symbols:
        commands.append(f"下钻核验: lychee research verify --symbol {symbols[0]}")
        commands.append(f"研究备忘录: lychee research memo --symbol {symbols[0]}")
    else:
        commands.append(f'研究备忘录: lychee research memo --name "{candidate.display_name}"')
    return commands


def research_action_symbols(candidate: CandidateCheck) -> list[str]:
    if candidate.symbol:
        return [candidate.symbol]
    return candidate.proxy_symbols


def topic_news_query(candidate: CandidateCheck, packet: ResearchPacket | None) -> str:
    packet_payload = packet.packet if packet is not None else {}
    packet_candidate = _dict_value(packet_payload.get("candidate"))
    raw_values = [
        (_string_value(packet_candidate.get("related_theme")), True),
        (_string_value(packet_candidate.get("why_watch")), False),
        (candidate.display_name, True),
        (candidate.symbol or "", True),
    ]
    terms: list[str] = []
    for value, keep_phrase in raw_values:
        if keep_phrase and _is_concise_query_phrase(value):
            terms.append(value)
        terms.extend(_query_alias_terms(value))
        terms.extend(_query_extracted_terms(value))
    if (candidate.symbol or "").upper() == "QQQ":
        terms.extend(["QQQ", "Nasdaq", "Nasdaq 100", "technology"])
    cleaned = _dedupe_query_terms(terms)
    return " OR ".join(cleaned[:8])


def _query_alias_terms(value: str) -> list[str]:
    if not value:
        return []
    terms: list[str] = []
    lowered = value.lower()
    for keyword, aliases in _TOPIC_QUERY_ALIASES.items():
        if keyword.lower() in lowered:
            terms.extend(aliases)
    return terms


def _query_extracted_terms(value: str) -> list[str]:
    return [term for term in _extract_topic_terms(value) if _is_query_term_allowed(term)]


def _is_concise_query_phrase(value: str) -> bool:
    cleaned = value.strip()
    return bool(cleaned) and len(cleaned) <= 24 and "观察" not in cleaned


def _is_query_term_allowed(term: str) -> bool:
    cleaned = term.strip()
    if not cleaned:
        return False
    if len(cleaned) > 18:
        return False
    if any(word in cleaned for word in ("用来", "用于", "观察", "是否")):
        return False
    return True


def _dedupe_query_terms(terms: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = term.strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


_TOPIC_QUERY_ALIASES = {
    "AI": ["AI", "artificial intelligence"],
    "人工智能": ["AI", "artificial intelligence"],
    "存储": ["storage", "hard drive"],
    "硬盘": ["hard drive", "storage"],
    "需求": ["demand"],
    "数据中心": ["data center", "cloud infrastructure"],
    "科技": ["technology"],
    "纳斯达克": ["Nasdaq", "QQQ"],
    "美股": ["US stocks"],
    "港股": ["Hong Kong stocks"],
    "恒生": ["Hang Seng"],
    "A股": ["China A shares"],
    "半导体": ["semiconductor", "chip"],
    "利率": ["interest rates", "yields"],
    "大盘": ["broad market"],
    "消费": ["consumer spending"],
    "政策": ["policy"],
}


def _needs_topic_news_refresh(candidate: CandidateCheck) -> bool:
    return candidate.evidence_quality in {"missing", "needs_review", "mixed"}


def _escape_command_arg(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def research_filing_symbols(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    if candidate.market.upper() != "US" or not candidate.symbol:
        return []
    if _asset_type(packet) != "stock":
        return []
    return [candidate.symbol]


def research_action_name(action: str) -> str:
    return {
        "refresh_market": "刷新行情",
        "refresh_news": "刷新新闻",
        "refresh_topic_news": "刷新主题新闻",
        "refresh_filings": "刷新美股公告/财报",
        "verify_research": "下钻核验",
        "generate_memo": "生成研究备忘录",
    }.get(action, action)


def research_action_result(
    action: str,
    count: int,
    warnings: list[str],
) -> str:
    lines = [
        f"已执行: {research_action_name(action)}",
        f"返回行数: {count}",
    ]
    if warnings:
        lines.append("警告: " + "；".join(warnings[:3]))
    return "\n".join(lines)


def _run_research_refresh_actions(
    *,
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    output_dir: Path,
    force: bool,
    pull_market: PullMarket,
    pull_news: PullNews,
    pull_filings: PullFilings,
) -> list[ResearchRunAction]:
    symbols = research_action_symbols(candidate)
    actions: list[ResearchRunAction] = []
    if symbols:
        actions.append(
            _pull_research_action(
                action_type="refresh_market",
                symbols=symbols,
                call=lambda: pull_market(
                    symbols=symbols,
                    output_dir=output_dir,
                    provider_id="auto",
                    force=force,
                ),
            )
        )
        actions.append(
            _pull_research_action(
                action_type="refresh_news",
                symbols=symbols,
                call=lambda: pull_news(
                    symbols=symbols,
                    output_dir=output_dir,
                    provider_id="auto",
                    force=force,
                ),
            )
        )
        topic_query = topic_news_query(candidate, packet)
        if _needs_topic_news_refresh(candidate) and topic_query:
            actions.append(
                _pull_research_action(
                    action_type="refresh_topic_news",
                    symbols=symbols,
                    call=lambda: pull_news(
                        symbols=symbols,
                        query=topic_query,
                        output_dir=output_dir,
                        provider_id="auto",
                        force=force,
                    ),
                )
            )
    filing_symbols = research_filing_symbols(candidate, packet)
    if filing_symbols:
        actions.append(
            _pull_research_action(
                action_type="refresh_filings",
                symbols=filing_symbols,
                call=lambda: pull_filings(
                    symbols=filing_symbols,
                    output_dir=output_dir,
                ),
            )
        )
    if not actions:
        actions.append(
            ResearchRunAction(
                action_type="none",
                status="skipped",
                symbols=[],
                count=0,
                output_path=None,
                warnings=[],
                message="当前研究任务没有可自动执行的刷新动作。",
            )
        )
    return actions


def _pull_research_action(
    *,
    action_type: str,
    symbols: list[str],
    call: Callable[[], PullResult],
) -> ResearchRunAction:
    try:
        result = call()
    except (RuntimeError, ValueError) as error:
        return ResearchRunAction(
            action_type=action_type,
            status="failed",
            symbols=symbols,
            count=0,
            output_path=None,
            warnings=[str(error)],
            message=f"{research_action_name(action_type)}失败。",
        )
    return ResearchRunAction(
        action_type=action_type,
        status="pulled" if result.refreshed else "cached",
        symbols=symbols,
        count=result.count,
        output_path=result.output_path,
        warnings=result.warnings,
        message=f"{research_action_name(action_type)}完成。",
    )


def _research_run_action_status(actions: list[ResearchRunAction]) -> str:
    lines = ["研究执行链"]
    for action in actions:
        symbol_text = ", ".join(action.symbols) if action.symbols else "-"
        status = _display_action_status(action.status)
        lines.append(
            f"- {research_action_name(action.action_type)} | {status} | "
            f"{symbol_text} | 返回 {action.count} 行"
        )
        if action.warnings:
            lines.append("  警告: " + "；".join(action.warnings[:3]))
    return "\n".join(lines)


def _display_action_status(status: str) -> str:
    return {
        "pulled": "已刷新",
        "cached": "使用缓存",
        "failed": "失败",
        "skipped": "跳过",
    }.get(status, status)


def _write_research_run_artifact(
    *,
    output_dir: Path,
    created_at: str,
    status: str,
    candidate: CandidateCheck,
    assessment: ResearchAssessment,
    actions: list[ResearchRunAction],
    detail: str,
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = research_dir / f"research-run-{_safe_timestamp(created_at)}.json"
    output_path.write_text(
        json.dumps(
            {
                "created_at": created_at,
                "status": status,
                "candidate": asdict(candidate),
                "assessment": asdict(assessment),
                "actions": [_research_run_action_to_dict(action) for action in actions],
                "detail": detail,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _verification_status(checks: list[ResearchVerificationCheck]) -> str:
    if any(check.status == "fail" for check in checks):
        return "blocked"
    return "pending_review"


def _verification_status_label(status: str) -> str:
    return {
        "blocked": "存在阻塞",
        "pending_review": "待人工核验",
    }.get(status, status)


def _verification_conclusion(status: str) -> str:
    if status == "blocked":
        return "一致性结论: 存在阻塞，先补齐失败项。"
    return "一致性结论: 待人工核验。"


def _verification_next_actions(
    checks: list[ResearchVerificationCheck],
    candidate: CandidateCheck,
) -> list[str]:
    failed = [check for check in checks if check.status == "fail"]
    if failed:
        return [f"先处理{check.name}: {check.detail}" for check in failed]
    actions = [
        "对照行情方向、成交量变化和相关新闻是否支持同一研究问题。",
        "记录支持证据、反向证据和仍需补充的数据。",
    ]
    if candidate.proxy_symbols:
        actions.insert(0, "先人工核对代理标的是否覆盖原始主题。")
    return actions


def _write_research_verification_artifact(
    *,
    output_dir: Path,
    created_at: str,
    status: str,
    status_label: str,
    candidate: CandidateCheck,
    checks: list[ResearchVerificationCheck],
    evidence_board: dict[str, list[str]],
    decision_board: ResearchDecisionBoard,
    evidence_change: ResearchEvidenceChange,
    conclusion: str,
    next_actions: list[str],
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = research_dir / f"research-verification-{_safe_timestamp(created_at)}.json"
    output_path.write_text(
        json.dumps(
            {
                "created_at": created_at,
                "status": status,
                "status_label": status_label,
                "candidate": asdict(candidate),
                "checks": [asdict(check) for check in checks],
                "evidence_board": evidence_board,
                "decision_board": asdict(decision_board),
                "evidence_change": asdict(evidence_change),
                "conclusion": conclusion,
                "next_actions": next_actions,
                "disclaimer": "下钻核验只检查证据完整度和待核验项，不构成买卖建议。",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _research_review_payload(
    *,
    created_at: str,
    verdict: str,
    verdict_label: str,
    note: str,
    evidence_counts: dict[str, int],
    verification: ResearchVerificationResult,
) -> dict[str, object]:
    return {
        "review_id": f"research-review:{created_at}",
        "created_at": created_at,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "note": note,
        "candidate": asdict(verification.candidate),
        "verification_path": str(verification.artifact_path),
        "verification": _research_verification_payload(verification),
        "evidence_counts": evidence_counts,
        "evidence_board": verification.evidence_board,
        "decision_board": asdict(verification.decision_board),
        "next_actions": verification.next_actions,
        "disclaimer": "研究复核只记录证据状态和下一步研究流程，不是买卖建议。",
    }


def _research_verification_payload(
    result: ResearchVerificationResult,
) -> dict[str, object]:
    return {
        "created_at": result.created_at,
        "status": result.status,
        "status_label": result.status_label,
        "candidate": asdict(result.candidate),
        "checks": [asdict(check) for check in result.checks],
        "evidence_board": result.evidence_board,
        "decision_board": asdict(result.decision_board),
        "evidence_change": asdict(result.evidence_change),
        "conclusion": result.conclusion,
        "next_actions": result.next_actions,
        "artifact_path": str(result.artifact_path),
    }


def _write_research_review_artifact(
    *,
    output_dir: Path,
    created_at: str,
    payload: dict[str, object],
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = research_dir / f"research-review-{_safe_timestamp(created_at)}.json"
    payload["review_path"] = str(output_path)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _research_run_action_to_dict(action: ResearchRunAction) -> dict[str, object]:
    return {
        "action_type": action.action_type,
        "status": action.status,
        "symbols": action.symbols,
        "count": action.count,
        "output_path": str(action.output_path) if action.output_path else None,
        "warnings": action.warnings,
        "message": action.message,
    }


def _candidate_checks(packets: list[ResearchPacket]) -> list[CandidateCheck]:
    checks: list[CandidateCheck] = []
    for packet in packets:
        payload = packet.packet
        data_gaps = _text_list(payload.get("data_gaps"))
        evidence_ids = _text_list(payload.get("evidence_ids"))
        proxy_symbols = _proxy_symbols(payload)
        candidate = _dict_value(payload.get("candidate"))
        asset_type = _string_value(candidate.get("asset_type"))
        related_theme = _string_value(candidate.get("related_theme"))
        why_watch = _string_value(candidate.get("why_watch"))
        next_actions = _text_list(payload.get("next_actions"))
        status = "blocked" if data_gaps else "ready"
        if packet.symbol:
            explanation = why_watch
        elif proxy_symbols:
            explanation = (
                f"这不是单一股票，先用这些代理观察工具看方向。{why_watch}"
            )
        else:
            explanation = f"还没有可研究入口。{why_watch}"
        base_candidate = CandidateCheck(
            display_name=packet.display_name,
            market=packet.market,
            symbol=packet.symbol,
            proxy_symbols=proxy_symbols,
            evidence_count=len(evidence_ids),
            gap_count=len(data_gaps),
            data_gaps=data_gaps,
            status=status,
            explanation=explanation,
            beginner_question=_beginner_question(
                display_name=packet.display_name,
                symbol=packet.symbol,
                market=packet.market,
                asset_type=asset_type,
                related_theme=related_theme,
            ),
            why_it_matters=_why_it_matters(
                symbol=packet.symbol,
                proxy_symbols=proxy_symbols,
                asset_type=asset_type,
                why_watch=why_watch,
            ),
            observation_entry=_observation_entry(packet.symbol, proxy_symbols),
            what_to_check=_what_to_check(
                market=packet.market,
                asset_type=asset_type,
                symbol=packet.symbol,
                proxy_symbols=proxy_symbols,
            ),
            next_step="",
            priority="",
            evidence_status="",
        )
        evidence_quality = _candidate_evidence_quality(base_candidate, packet)
        checks.append(
            replace(
                base_candidate,
                next_step=_next_step(next_actions, data_gaps, evidence_quality),
                priority=_priority(
                    status=status,
                    symbol=packet.symbol,
                    proxy_symbols=proxy_symbols,
                    evidence_count=len(evidence_ids),
                    gap_count=len(data_gaps),
                    evidence_quality=evidence_quality,
                ),
                ranking_reason=_ranking_reason(
                    status=status,
                    symbol=packet.symbol,
                    proxy_symbols=proxy_symbols,
                    evidence_count=len(evidence_ids),
                    gap_count=len(data_gaps),
                    evidence_quality=evidence_quality,
                ),
                evidence_status=_evidence_status(
                    evidence_count=len(evidence_ids),
                    gap_count=len(data_gaps),
                    proxy_symbols=proxy_symbols,
                    evidence_quality=evidence_quality,
                ),
                evidence_quality=evidence_quality.status,
            )
        )
    return checks


def _workbench_gates(
    candidates: list[CandidateCheck],
    proxy_price_count: int,
    proxy_total: int,
) -> list[WorkbenchGate]:
    gates: list[WorkbenchGate] = []
    if candidates:
        gates.append(WorkbenchGate("研究队列", "pass", f"已检查 {len(candidates)} 个候选。"))
    else:
        gates.append(WorkbenchGate("研究队列", "fail", "没有研究候选，请先运行市场发现。"))

    missing_evidence = [
        candidate.display_name
        for candidate in candidates
        if candidate.evidence_count == 0
    ]
    gates.append(
        WorkbenchGate(
            "证据",
            "fail" if missing_evidence else "pass",
            "缺少证据: " + ", ".join(missing_evidence)
            if missing_evidence
            else "每个候选都有本地证据 ID。",
        )
    )

    missing_entry = [
        candidate.display_name
        for candidate in candidates
        if not candidate.symbol and not candidate.proxy_symbols
    ]
    gates.append(
        WorkbenchGate(
            "研究入口",
            "fail" if missing_entry else "pass",
            "缺少直接代码或代理标的: " + ", ".join(missing_entry)
            if missing_entry
            else "每个候选都有直接代码或代理标的。",
        )
    )

    proxy_status = "pass" if proxy_price_count == proxy_total else "fail"
    if proxy_total == 0:
        proxy_detail = "当前候选不需要代理行情。"
    else:
        proxy_detail = f"代理行情覆盖 {proxy_price_count}/{proxy_total}。"
    gates.append(WorkbenchGate("代理行情", proxy_status, proxy_detail))

    blocked = [candidate for candidate in candidates if candidate.data_gaps]
    if blocked:
        detail = "；".join(
            f"{candidate.display_name}: {', '.join(candidate.data_gaps)}"
            for candidate in blocked
        )
        gates.append(WorkbenchGate("数据缺口", "fail", detail))
    else:
        gates.append(WorkbenchGate("数据缺口", "pass", "当前深挖包没有未补齐缺口。"))
    return gates


def _candidate_evidence_quality(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> CandidateEvidenceQuality:
    relevance = _news_topic_relevance(candidate, packet)
    status = "supporting"
    if relevance.matched_count == 0:
        status = "missing"
    elif relevance.reverse_count or relevance.neutral_count:
        status = "mixed" if relevance.support_count else "needs_review"
    return CandidateEvidenceQuality(
        status=status,
        support_count=relevance.support_count,
        reverse_count=relevance.reverse_count,
        neutral_count=relevance.neutral_count,
        off_topic_count=len(relevance.unmatched_rows),
    )


def _beginner_brief(status: str, candidates: list[CandidateCheck]) -> str:
    ready = [candidate for candidate in candidates if candidate.status == "ready"]
    blocked = [candidate for candidate in candidates if candidate.status == "blocked"]
    lines = [
        "AlphaDesk 研究工作台",
        "边界: 研究任务台，不给买卖建议。",
        (
            f"状态: {_brief_status(status)} | "
            f"可执行 {len(ready)} | 阻塞 {len(blocked)} | 总任务 {len(candidates)}"
        ),
    ]

    lines.append("")
    lines.append("今日研究任务")
    if ready:
        for candidate in ready:
            lines.append(
                f"- {candidate.display_name} "
                f"[{candidate.market}] | 入口: {candidate.observation_entry} | "
                f"优先级: {candidate.priority} | 证据状态: {candidate.evidence_status}"
            )
            lines.append(f"  排序理由: {candidate.ranking_reason}")
            lines.append(f"  研究问题: {candidate.beginner_question}")
            lines.append(f"  关键核验: {candidate.what_to_check}")
            lines.append(f"  下一步: {candidate.next_step}")
            if candidate.proxy_symbols:
                lines.append("  代理核验: 核对成分、费用、流动性和是否可交易。")
    else:
        lines.append("- 无可执行任务。")

    lines.append("")
    lines.append("下一步队列")
    if ready:
        for candidate in ready:
            lines.append(f"- {candidate.display_name}: {candidate.next_step}")
    else:
        lines.append("- 先解除阻塞任务。")

    lines.append("")
    lines.append("阻塞任务")
    if blocked:
        for candidate in blocked:
            lines.append(
                f"- {candidate.display_name} "
                f"[{candidate.market}] | 入口: {candidate.observation_entry}"
            )
            gap_text = _gap_text(candidate.data_gaps)
            lines.append(f"  优先级: {candidate.priority}")
            lines.append(f"  排序理由: {candidate.ranking_reason}")
            lines.append(f"  研究问题: {candidate.beginner_question}")
            lines.append(f"  缺口: {gap_text}。")
            lines.append(f"  处理动作: 先补齐 {gap_text}。")
    else:
        lines.append("- 无。")
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
    if candidate.evidence_quality in {"missing", "needs_review", "mixed"}:
        return _evidence_quality_signal_reading(candidate.evidence_quality)
    if not price and not candidate.proxy_symbols:
        return "证据不足: 缺少可观察行情，暂时只能保留在线索池。"
    if not evidence and not related_news and not filings:
        return "只具备行情: 还没有新闻、公告或财报佐证。"
    if price and (evidence or related_news):
        return "初步可研究: 已有行情和消息证据，下一步检查它们是否同向。"
    if candidate.proxy_symbols:
        return "代理观察: 先确认代理标的是否真的覆盖主题，再下钻。"
    return "待增强证据: 还需要补齐更多可核验材料。"


def _evidence_quality_signal_reading(evidence_quality: str) -> str:
    if evidence_quality == "missing":
        return "证据需复核: 当前新闻没有命中研究主题，不能把它当成支持材料。"
    if evidence_quality == "mixed":
        return "证据需复核: 支持和反向/待判定材料混在一起，先拆分方向。"
    return "证据需复核: 当前只有反向或待判定材料，先核验方向再继续。"


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


def _asset_type(packet: ResearchPacket | None) -> str:
    if packet is None:
        return ""
    candidate = _dict_value(packet.packet.get("candidate"))
    return _string_value(candidate.get("asset_type")).lower()


def _price_line(price: dict[str, object]) -> str:
    if not price:
        return "行情: 暂无本地行情。"
    symbol = _string_value(price.get("symbol")) or "-"
    close = _number_value(price.get("close"))
    currency = _string_value(price.get("currency"))
    date = _string_value(price.get("date"))
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
        headline = _string_value(row.get("headline")) or "未命名证据"
        source_url = _string_value(row.get("source_url"))
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
        form = _string_value(row.get("form")) or "公告"
        date = _string_value(row.get("date"))
        summary = _string_value(row.get("summary"))
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


def _brief_status(status: str) -> str:
    return "可执行研究" if status == "ready" else "存在阻塞"


def _priority(
    *,
    status: str,
    symbol: str | None,
    proxy_symbols: list[str],
    evidence_count: int,
    gap_count: int,
    evidence_quality: CandidateEvidenceQuality,
) -> str:
    if status == "blocked" or gap_count:
        return "P0 待补数据"
    if evidence_quality.needs_review:
        return "P2 先复核证据"
    if symbol and evidence_count >= 2:
        return "P1 直接下钻"
    if symbol:
        return "P2 待增强证据"
    if proxy_symbols:
        return "P2 代理核验"
    return "P3 待映射"


def _ranking_reason(
    *,
    status: str,
    symbol: str | None,
    proxy_symbols: list[str],
    evidence_count: int,
    gap_count: int,
    evidence_quality: CandidateEvidenceQuality,
) -> str:
    if gap_count:
        return f"还有 {gap_count} 个数据缺口，先补数据再研究。"
    if status == "blocked":
        return "当前未达到工作台自检门槛，先查看阻塞原因再继续。"
    if evidence_quality.status == "missing":
        return "还没有命中研究主题的新闻证据，先复核证据是否只是市场噪音。"
    if evidence_quality.status == "needs_review":
        return "只有反向或待判定证据，先运行下钻核验复核证据方向。"
    if evidence_quality.status == "mixed":
        return "支持证据和反向/待判定证据同时存在，先复核证据方向。"
    if symbol and evidence_count >= 2:
        return f"有直接代码和 {evidence_count} 条证据，且当前没有数据缺口。"
    if symbol:
        return "有直接代码且当前没有数据缺口，但证据还需要增强。"
    if proxy_symbols:
        return "已有代理标的且当前没有数据缺口，先核验代理是否覆盖主题。"
    return "还没有直接代码或代理标的，需要先完成入口映射。"


def _evidence_status(
    *,
    evidence_count: int,
    gap_count: int,
    proxy_symbols: list[str],
    evidence_quality: CandidateEvidenceQuality,
) -> str:
    parts = [f"证据 {evidence_count} 条", f"缺口 {gap_count} 个"]
    if proxy_symbols:
        parts.append("代理已映射")
    parts.append(
        "证据质量: "
        f"支持 {evidence_quality.support_count} | "
        f"反向 {evidence_quality.reverse_count} | "
        f"待判定 {evidence_quality.neutral_count} | "
        f"离题 {evidence_quality.off_topic_count}"
    )
    return "；".join(parts)


def _gap_text(data_gaps: list[str]) -> str:
    return "；".join(gap.rstrip("。；;,.， ") for gap in data_gaps if gap.strip())


def _beginner_question(
    *,
    display_name: str,
    symbol: str | None,
    market: str,
    asset_type: str,
    related_theme: str,
) -> str:
    text = " ".join(
        item.lower()
        for item in [display_name, symbol or "", market, asset_type, related_theme]
        if item
    )
    if "qqq" in text or "nasdaq" in text or "纳斯达克" in text:
        return "美股科技股现在是独立主线，还是只是跟着大盘一起反弹？"
    if "baba" in text or "alibaba" in text or "阿里" in text:
        return "阿里 AI 云线索是公司自身变化，还是只是 AI 概念带动？"
    if "nvda" in text or "nvidia" in text or "英伟达" in text:
        return "AI 算力需求是在继续扩散，还是只集中在最热门龙头？"
    if "tsla" in text or "tesla" in text or "特斯拉" in text:
        return "电动车、储能、自动驾驶和机器人叙事是否互相支持？"
    if "tencent" in text or "腾讯" in text:
        return "港股中国平台公司是基本面改善，还是短期情绪反弹？"
    if "半导体" in text or "数据中心" in text:
        return "AI 数据中心主题是否已经扩散到可观察的供应链？"
    if "恒生" in text or "港股" in text or market.upper() == "HK":
        return "港股变化是整个市场的问题，还是只集中在某个板块？"
    if market.upper() == "CN":
        return "A 股这条线索是整体市场变化，还是行业自身出现变化？"
    if related_theme:
        return f"{display_name} 对应的主题“{related_theme}”是真变化，还是只有新闻热度？"
    return f"{display_name} 这条线索是否值得继续花时间研究？"


def _why_it_matters(
    *,
    symbol: str | None,
    proxy_symbols: list[str],
    asset_type: str,
    why_watch: str,
) -> str:
    if proxy_symbols:
        proxy_text = ", ".join(proxy_symbols)
        base = (
            f"这不是单一股票，所以先用 {proxy_text} 这些代理观察工具，"
            "把大方向、成交量和市场情绪看清楚。"
        )
    elif symbol:
        kind = " ETF/指数入口" if asset_type.lower() in {"etf", "index"} else "证券代码"
        base = f"{symbol} 是这条线索当前最直接的{kind}，可用于验证主题是否反映到市场数据里。"
    else:
        base = "这条线索还缺少可直接观察的入口，所以只能先当作主题线索。"
    if why_watch:
        return f"{base}{why_watch}"
    return base


def _observation_entry(symbol: str | None, proxy_symbols: list[str]) -> str:
    if symbol:
        return symbol
    if proxy_symbols:
        return ", ".join(proxy_symbols)
    return "暂无，需要先映射到可观察的股票、ETF 或指数。"


def _what_to_check(
    *,
    market: str,
    asset_type: str,
    symbol: str | None,
    proxy_symbols: list[str],
) -> str:
    if proxy_symbols:
        return (
            "代理 ETF/指数方向、成交量、成分覆盖度和相关新闻要互相支持；"
            "只看一天涨跌没有意义。"
        )
    if asset_type.lower() in {"etf", "index"}:
        return (
            "把它和更宽的市场基准对比，看方向、成交量、前十大成分和相关新闻是否一致。"
        )
    if symbol and market.upper() == "US":
        return "行情方向、成交量、相关新闻、SEC 公告和财报线索要能互相印证。"
    if symbol:
        return "行情方向、成交量、公告、财报和行业新闻要能互相印证。"
    return "先找到可观察入口，再检查行情、新闻、公告和成分是否能互相印证。"


def _next_step(
    next_actions: list[str],
    data_gaps: list[str],
    evidence_quality: CandidateEvidenceQuality,
) -> str:
    if data_gaps:
        return f"下一步先补齐 {'；'.join(data_gaps)}。"
    if evidence_quality.needs_review:
        return "先运行下钻核验复核证据方向，再决定是否继续研究。"
    if next_actions:
        cleaned_actions = [
            action.rstrip("。；;,.， ")
            for action in next_actions[:2]
            if action.strip()
        ]
        return "；".join(cleaned_actions)
    return "先查看观察入口的最近行情、成交量、相关新闻和公开资料。"


def _write_workbench_check_artifact(
    *,
    output_dir: Path,
    created_at: str,
    status: str,
    candidates: list[CandidateCheck],
    gates: list[WorkbenchGate],
    proxy_price_count: int,
    proxy_total: int,
    beginner_brief: str,
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = research_dir / f"workbench-check-{_safe_timestamp(created_at)}.json"
    output_path.write_text(
        json.dumps(
            {
                "created_at": created_at,
                "status": status,
                "candidate_count": len(candidates),
                "ready_count": sum(
                    1 for candidate in candidates if candidate.status == "ready"
                ),
                "blocked_count": sum(
                    1 for candidate in candidates if candidate.status == "blocked"
                ),
                "proxy_price_coverage": f"{proxy_price_count}/{proxy_total}",
                "gates": [asdict(gate) for gate in gates],
                "candidates": [asdict(candidate) for candidate in candidates],
                "beginner_brief": beginner_brief,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _proxy_price_coverage(packets: list[ResearchPacket]) -> tuple[int, int]:
    priced = 0
    total = 0
    for packet in packets:
        for row in _symbol_mapping_rows(packet.packet):
            total += 1
            if isinstance(row.get("latest_price"), dict):
                priced += 1
    return priced, total


def _proxy_symbols(payload: dict[str, object]) -> list[str]:
    symbols: list[str] = []
    for row in _symbol_mapping_rows(payload):
        symbol = row.get("symbol")
        if isinstance(symbol, str):
            symbols.append(symbol)
    return symbols


def _symbol_mapping_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    local_data = _dict_value(payload.get("local_data"))
    symbol_mapping = local_data.get("symbol_mapping")
    if not isinstance(symbol_mapping, list):
        return []
    return [row for row in symbol_mapping if isinstance(row, dict)]


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number_value(value: object) -> object:
    if isinstance(value, float):
        return f"{value:.2f}"
    return value


def _safe_timestamp(value: str) -> str:
    normalized = value.replace("+00:00", "Z")
    return (
        normalized.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace(".", "")
        .replace("T", "-")
    )
