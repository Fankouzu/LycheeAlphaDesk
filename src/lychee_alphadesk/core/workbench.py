import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import (
    cache_entry_status,
    list_cache_entries,
)
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
from lychee_alphadesk.core.research_db import (
    ResearchDataRequestFulfillmentRecord,
    ResearchEvidenceReviewRecord,
    list_research_data_request_fulfillments,
    list_research_evidence_reviews,
    write_research_evidence_review_record,
    write_research_review_record,
)

PullMarket = Callable[..., PullResult]
PullNews = Callable[..., PullResult]
PullFilings = Callable[..., PullResult]

DEFAULT_RESEARCH_SELECTION_LIMIT = 5


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
    next_command: str = ""
    topic_news_exhausted: bool = False
    topic_news_review_ready: bool = False
    command_limit: int = DEFAULT_RESEARCH_SELECTION_LIMIT


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
    next_commands: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchEvidenceChange:
    status: str
    status_label: str
    summary: str
    support_delta: int
    risk_delta: int
    missing_delta: int
    off_topic_delta: int = 0
    added: dict[str, list[str]] = field(
        default_factory=lambda: _empty_evidence_change_items()
    )
    removed: dict[str, list[str]] = field(
        default_factory=lambda: _empty_evidence_change_items()
    )
    previous_artifact_path: str | None = None
    previous_created_at: str | None = None


@dataclass(frozen=True)
class ResearchAnalystReadout:
    title: str
    signal: str
    pressure: str
    gap: str
    evidence_change: str
    next_action: str
    next_command: str


@dataclass(frozen=True)
class ResearchHypothesisPanel:
    title: str
    core_question: str
    working_hypothesis: str
    support_chain: list[str]
    counter_chain: list[str]
    gap_priorities: list[str]
    next_data_requests: list[str]


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
    analyst_readout: ResearchAnalystReadout = field(
        default_factory=lambda: _empty_research_analyst_readout()
    )
    hypothesis_panel: ResearchHypothesisPanel = field(
        default_factory=lambda: _empty_research_hypothesis_panel()
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
class ResearchEvidenceReviewResult:
    created_at: str
    verdict: str
    verdict_label: str
    evidence_text: str
    note: str
    candidate: CandidateCheck
    artifact_path: Path
    db_path: Path


@dataclass(frozen=True)
class PendingEvidenceReviewItem:
    created_at: str
    display_name: str
    symbol: str | None
    market: str
    primary_question: str
    evidence_text: str
    raw_evidence: str
    suggested_verdict: str
    suggested_verdict_label: str
    suggested_reason: str
    artifact_path: str
    review_command: str


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

RESEARCH_EVIDENCE_REVIEW_VERDICTS = {
    "support": "支持证据",
    "reverse": "风险/反向待查",
    "irrelevant": "无关/排除",
}


def run_workbench_check(
    *,
    output_dir: Path,
    status: str | None = "new",
    limit: int = 5,
    force: bool = False,
    fill_news: bool = True,
    now: datetime | None = None,
    pull_market: PullMarket | None = None,
    pull_news: PullNews | None = None,
    pull_filings: PullFilings | None = None,
) -> WorkbenchCheckResult:
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    fill_result = fill_research_data_gaps(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=force,
        fill_news=fill_news,
        pull_market=pull_market or pull_market_prices,
        pull_news=pull_news or pull_news_events,
        pull_filings=pull_filings or pull_sec_filings,
    )
    deepen_result = deepen_research_queue(
        output_dir=output_dir,
        status=status,
        limit=limit,
        now=now,
    )
    candidates = _candidate_checks(
        output_dir,
        deepen_result.packets,
        command_limit=limit,
        now=now,
    )
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
        fill_result=fill_result,
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
    candidate_limit = limit
    workbench = run_workbench_check(
        output_dir=output_dir,
        status=status,
        limit=candidate_limit,
        force=False,
        fill_news=False,
        now=now,
        pull_market=pull_market,
        pull_filings=pull_filings,
    )
    selected_index = select_research_candidate_index(
        workbench,
        symbol=symbol,
        name=name,
    )
    if selected_index is None and (symbol or name):
        candidate_limit = max(limit, 50)
        workbench = run_workbench_check(
            output_dir=output_dir,
            status=status,
            limit=candidate_limit,
            force=False,
            fill_news=False,
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
    evidence_reviews = _candidate_evidence_reviews(output_dir, candidate)
    checks = build_research_verification_checks(
        candidate,
        packet,
        evidence_reviews=evidence_reviews,
    )
    evidence_board = build_research_evidence_board(
        candidate,
        packet,
        checks,
        evidence_reviews=evidence_reviews,
    )
    decision_board = build_research_decision_board(
        candidate,
        packet,
        checks,
        evidence_board,
        evidence_reviews=evidence_reviews,
    )
    evidence_change = build_research_evidence_change(
        output_dir=output_dir,
        candidate=candidate,
        evidence_board=evidence_board,
    )
    analyst_readout = build_research_analyst_readout(
        evidence_board=evidence_board,
        decision_board=decision_board,
        evidence_change=evidence_change,
    )
    hypothesis_panel = build_research_hypothesis_panel(
        candidate=candidate,
        evidence_board=evidence_board,
        decision_board=decision_board,
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
        analyst_readout=analyst_readout,
        hypothesis_panel=hypothesis_panel,
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
        analyst_readout=analyst_readout,
        hypothesis_panel=hypothesis_panel,
        conclusion=conclusion,
        next_actions=next_actions,
        artifact_path=artifact_path,
        workbench_result=workbench,
    )


def list_pending_evidence_reviews(
    output_dir: Path,
    *,
    limit: int = 50,
) -> list[PendingEvidenceReviewItem]:
    latest_artifacts = _latest_verification_artifacts(output_dir)
    items: list[PendingEvidenceReviewItem] = []
    for payload, artifact_path in latest_artifacts:
        candidate = _dict_value(payload.get("candidate"))
        display_name = _string_value(candidate.get("display_name")) or "未知研究任务"
        symbol = _string_value(candidate.get("symbol")) or None
        market = _string_value(candidate.get("market")) or "-"
        command_limit = (
            _int_value(candidate.get("command_limit"))
            or DEFAULT_RESEARCH_SELECTION_LIMIT
        )
        decision_board = _dict_value(payload.get("decision_board"))
        primary_question = (
            _string_value(decision_board.get("primary_question"))
            or "先判断这条新闻能否回答当前研究问题。"
        )
        evidence_reviews = list_research_evidence_reviews(
            output_dir,
            symbol=symbol,
            name=None if symbol else display_name,
            limit=100,
        )
        evidence_board = _dict_value(payload.get("evidence_board"))
        seen: set[str] = set()
        for raw_evidence in _text_list(evidence_board.get("risk")):
            evidence_text = _pending_news_evidence_text(raw_evidence)
            if evidence_text is None:
                continue
            normalized = evidence_text.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            if _reviewed_evidence_verdict(normalized, evidence_reviews):
                continue
            suggestion = suggest_pending_evidence_review(
                evidence_text,
                primary_question=primary_question,
            )
            items.append(
                PendingEvidenceReviewItem(
                    created_at=_string_value(payload.get("created_at")),
                    display_name=display_name,
                    symbol=symbol,
                    market=market,
                    primary_question=primary_question,
                    evidence_text=evidence_text,
                    raw_evidence=raw_evidence,
                    suggested_verdict=suggestion[0],
                    suggested_verdict_label=RESEARCH_EVIDENCE_REVIEW_VERDICTS[
                        suggestion[0]
                    ],
                    suggested_reason=suggestion[1],
                    artifact_path=str(artifact_path),
                    review_command=_pending_evidence_review_command(
                        symbol=symbol,
                        display_name=display_name,
                        evidence_text=evidence_text,
                        verdict=suggestion[0],
                        note=suggestion[1],
                        command_limit=command_limit,
                    ),
                )
            )
            if len(items) >= limit:
                return items
    return items


def record_research_evidence_review(
    *,
    output_dir: Path,
    evidence_text: str,
    verdict: str,
    note: str = "",
    symbol: str | None = None,
    name: str | None = None,
    status: str | None = "new",
    limit: int = 5,
    now: datetime | None = None,
) -> ResearchEvidenceReviewResult:
    if verdict not in RESEARCH_EVIDENCE_REVIEW_VERDICTS:
        allowed = ", ".join(RESEARCH_EVIDENCE_REVIEW_VERDICTS)
        raise ValueError(f"未知证据复核方向: {verdict}。可选值: {allowed}")
    cleaned_text = evidence_text.strip()
    if not cleaned_text:
        raise ValueError("请提供要复核的证据文本。")

    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    candidate_limit = limit
    workbench = run_workbench_check(
        output_dir=output_dir,
        status=status,
        limit=candidate_limit,
        force=False,
        now=now,
    )
    selected_index = select_research_candidate_index(
        workbench,
        symbol=symbol,
        name=name,
    )
    if selected_index is None and (symbol or name):
        candidate_limit = max(limit, 50)
        workbench = run_workbench_check(
            output_dir=output_dir,
            status=status,
            limit=candidate_limit,
            force=False,
            fill_news=False,
            now=now,
        )
        selected_index = select_research_candidate_index(
            workbench,
            symbol=symbol,
            name=name,
        )
    if selected_index is None:
        raise ValueError("没有找到匹配的研究任务。")
    candidate = workbench.candidates[selected_index]
    cleaned_note = note.strip() or "未填写证据复核备注。"
    verdict_label = RESEARCH_EVIDENCE_REVIEW_VERDICTS[verdict]
    payload = _research_evidence_review_payload(
        created_at=created_at,
        verdict=verdict,
        verdict_label=verdict_label,
        evidence_text=cleaned_text,
        note=cleaned_note,
        candidate=candidate,
    )
    artifact_path = _write_research_evidence_review_artifact(
        output_dir=output_dir,
        created_at=created_at,
        payload=payload,
    )
    db_path = write_research_evidence_review_record(
        output_dir=output_dir,
        review_id=str(payload["review_id"]),
        created_at=created_at,
        display_name=candidate.display_name,
        symbol=candidate.symbol,
        market=candidate.market,
        evidence_text=cleaned_text,
        verdict=verdict,
        verdict_label=verdict_label,
        note=cleaned_note,
        review_path=artifact_path,
        payload=payload,
    )
    return ResearchEvidenceReviewResult(
        created_at=created_at,
        verdict=verdict,
        verdict_label=verdict_label,
        evidence_text=cleaned_text,
        note=cleaned_note,
        candidate=candidate,
        artifact_path=artifact_path,
        db_path=db_path,
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
        "off_topic": len(verification.evidence_board.get("off_topic", [])),
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
    candidate_limit = limit
    initial = run_workbench_check(
        output_dir=output_dir,
        status=status,
        limit=candidate_limit,
        force=False,
        fill_news=False,
        now=now,
        pull_market=pull_market,
        pull_news=pull_news,
        pull_filings=pull_filings,
    )
    selected_index = select_research_candidate_index(
        initial,
        symbol=symbol,
        name=name,
    )
    if selected_index is None and (symbol or name):
        candidate_limit = max(limit, 50)
        initial = run_workbench_check(
            output_dir=output_dir,
            status=status,
            limit=candidate_limit,
            force=False,
            fill_news=False,
            now=now,
            pull_market=pull_market,
            pull_news=pull_news,
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
        limit=candidate_limit,
        force=False,
        fill_news=False,
        now=now,
        pull_market=pull_market,
        pull_news=pull_news,
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
    if _actions_exhausted_topic_news(actions, refreshed_candidate, refreshed_packet):
        refreshed_candidate = _mark_topic_news_exhausted(refreshed_candidate)
    elif _actions_refreshed_topic_news_for_review(
        actions,
        refreshed_candidate,
        refreshed_packet,
    ):
        refreshed_candidate = _mark_topic_news_review_ready(refreshed_candidate)
    action_status = _research_run_action_status(
        actions,
        related_news_count=_packet_related_news_count(
            refreshed_candidate,
            refreshed_packet,
        ),
    )
    evidence_incomplete = (
        bool(refreshed_candidate.data_gaps)
        or refreshed_candidate.topic_news_exhausted
    )
    detail_commands = research_action_commands(refreshed_candidate, refreshed_packet)
    if refreshed_candidate.data_gaps:
        detail_commands = [
            command
            for command in detail_commands
            if not command.startswith(("下钻核验:", "研究备忘录:"))
        ]
    elif evidence_incomplete:
        detail_commands = [
            command
            for command in detail_commands
            if not command.startswith("研究备忘录:")
        ]
    detail = render_research_task_detail(
        refreshed_candidate,
        refreshed_packet,
        action_status=action_status,
        commands=detail_commands,
    )
    assessment = build_research_assessment(refreshed_candidate, refreshed_packet)
    run_status = (
        "completed"
        if all(action.status not in {"failed", "no_data"} for action in actions)
        and not evidence_incomplete
        else "partial"
    )
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
    *,
    evidence_reviews: list[ResearchEvidenceReviewRecord] | None = None,
) -> list[ResearchVerificationCheck]:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    prices = _verification_price_rows(packet_payload, local_data)
    evidence = _dict_list(packet_payload.get("evidence"))
    raw_related_news = _dict_list(local_data.get("related_news"))
    related_news, off_topic_news = _detail_related_news(
        candidate,
        packet,
        raw_related_news,
    )
    filings = _dict_list(local_data.get("filings"))
    financials = _dict_list(local_data.get("financials"))
    forecast = _dict_value(local_data.get("forecast"))
    research_metrics = _dict_list(local_data.get("research_metrics"))
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
    if prices:
        checks.append(
            ResearchVerificationCheck(
                name="行情核验",
                status="pass",
                detail=_price_check_detail(prices),
            )
        )
        volume_ok = all(_price_volume_ok(price) for price in prices)
        checks.append(
            ResearchVerificationCheck(
                name="成交量核验",
                status="pass" if volume_ok else "warn",
                detail=_volume_check_detail(prices),
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
    topic_relevance = _news_topic_relevance(
        candidate,
        packet,
        evidence_reviews=evidence_reviews,
    )
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
    filings_required = candidate.market.upper() in {"US", "HK", "CN"} and asset_type == "stock"
    if filings_required:
        filing_source = {
            "US": "SEC 公告/财报",
            "HK": "HKEX 公司公告",
            "CN": "巨潮公司公告",
        }[candidate.market.upper()]
        checks.append(
            ResearchVerificationCheck(
                name="公告/财报核验",
                status="pass" if filings else "fail",
                detail=f"可核验 {filing_source} {len(filings)} 条。"
                if filings
                else f"{candidate.market.upper()} 股票缺少 {filing_source}线索。",
            )
        )
    else:
        checks.append(
            ResearchVerificationCheck(
                name="公告/财报核验",
                status="na",
                detail="当前任务不要求公司公告/财报核验。",
            )
        )
    if candidate.market.upper() == "US" and asset_type == "stock":
        checks.append(
            ResearchVerificationCheck(
                name="财务快照核验",
                status="pass" if financials else "warn",
                detail=_financial_snapshot_check_detail(financials)
                if financials
                else "尚无可核验的 SEC XBRL 财务快照；可补齐后再读取营收、利润和经营现金流。",
            )
        )
    else:
        checks.append(
            ResearchVerificationCheck(
                name="财务快照核验",
                status="na",
                detail="当前任务不适用 SEC XBRL 财务快照。",
            )
        )
    checks.append(
        ResearchVerificationCheck(
            name="预测参考",
            status="warn" if forecast else "na",
            detail=_forecast_check_detail(forecast)
            if forecast
            else "暂无 TimesFM 预测区间；预测不是研究核验必需项。",
        )
    )
    if research_metrics:
        checks.append(
            ResearchVerificationCheck(
                name="研究指标核验",
                status="pass",
                detail=_research_metric_check_detail(research_metrics),
            )
        )
        benchmark_metrics = [
            row
            for row in research_metrics
            if _string_value(row.get("domain")).casefold()
            == "benchmark_comparison"
        ]
        if benchmark_metrics:
            checks.append(
                ResearchVerificationCheck(
                    name="基准比较核验",
                    status="pass" if len(benchmark_metrics) >= 3 else "warn",
                    detail=_research_metric_check_detail(benchmark_metrics),
                )
            )
    if _requires_direct_fund_metadata(candidate, packet):
        has_metadata = _direct_fund_metadata_is_complete(candidate, packet_payload)
        checks.append(
            ResearchVerificationCheck(
                name="基金资料核验",
                status="pass" if has_metadata else "warn",
                detail=_direct_fund_metadata_check_detail(candidate, packet_payload),
            )
        )
    if candidate.proxy_symbols:
        checks.append(
            ResearchVerificationCheck(
                name="代理标的核验",
                status="warn",
                detail=_proxy_mapping_check_detail(packet_payload),
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
    *,
    evidence_reviews: list[ResearchEvidenceReviewRecord] | None = None,
) -> dict[str, list[str]]:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    prices = _verification_price_rows(packet_payload, local_data)
    filings = _dict_list(local_data.get("filings"))
    financials = _dict_list(local_data.get("financials"))
    research_metrics = _dict_list(local_data.get("research_metrics"))
    support: list[str] = []
    risk: list[str] = []
    off_topic: list[str] = []
    missing: list[str] = []
    for price in prices[:3]:
        support.append(_price_line(price).removeprefix("行情: "))
    if _requires_direct_fund_metadata(candidate, packet):
        support.extend(_direct_fund_metadata_support_lines(candidate, packet_payload))
        missing.extend(_direct_fund_metadata_missing_data_lines(candidate, packet_payload))
    support.extend(_proxy_mapping_support_lines(packet_payload))
    support.extend(_proxy_operability_support_lines(packet_payload))
    support.extend(_proxy_fund_metadata_support_lines(packet_payload))
    support.extend(_research_metric_support_lines(research_metrics))
    support.extend(
        _research_metric_support_lines(
            [
                row
                for row in research_metrics
                if _string_value(row.get("domain")).casefold()
                == "benchmark_comparison"
            ]
        )
    )
    missing.extend(_market_breadth_missing_lines(candidate, research_metrics))
    missing.extend(_proxy_missing_data_lines(packet_payload))
    topic_relevance = _news_topic_relevance(
        candidate,
        packet,
        evidence_reviews=evidence_reviews,
    )
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
        off_topic.append(f"新闻待查: {headline} 未命中研究主题关键词。")
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
    support.extend(_financial_snapshot_support_lines(financials))
    for check in checks:
        if check.status == "fail":
            missing.append(f"{check.name}: {check.detail}")
        elif check.status == "warn":
            risk.append(f"{check.name}: {check.detail}")
    return {
        "support": support,
        "risk": risk,
        "off_topic": off_topic,
        "missing": missing,
    }


def _verification_price_rows(
    packet_payload: dict[str, object],
    local_data: dict[str, object],
) -> list[dict[str, object]]:
    price = _dict_value(local_data.get("price"))
    if price:
        return [price]
    prices: list[dict[str, object]] = []
    for row in _symbol_mapping_rows(packet_payload):
        latest_price = _dict_value(row.get("latest_price"))
        if not latest_price:
            continue
        if not _string_value(latest_price.get("symbol")):
            symbol = _string_value(row.get("symbol"))
            latest_price = {**latest_price, "symbol": symbol}
        prices.append(latest_price)
    return prices


def _proxy_mapping_support_lines(packet_payload: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for row in _symbol_mapping_rows(packet_payload):
        symbol = _string_value(row.get("symbol")) or "-"
        display_name = _string_value(row.get("display_name")) or "-"
        confidence = _string_value(row.get("confidence")) or "-"
        reason = _string_value(row.get("reason"))
        line = f"代理映射: {symbol} {display_name} | 置信度 {confidence}"
        if reason:
            line += f" | {reason}"
        lines.append(line)
    return lines


def _proxy_operability_support_lines(packet_payload: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for row in _symbol_mapping_rows(packet_payload):
        symbol = _string_value(row.get("symbol"))
        if not symbol:
            continue
        market = (_string_value(row.get("market")) or "-").upper()
        asset_type = (_string_value(row.get("asset_type")) or "证券").upper()
        lines.append(f"可交易性: {symbol} 为 {market} {asset_type} 代理")
        latest_price = _dict_value(row.get("latest_price"))
        volume = latest_price.get("volume")
        if isinstance(volume, int | float) and volume > 0:
            lines.append(f"流动性: {symbol} 成交量 {volume}")
    return lines


def _requires_direct_fund_metadata(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> bool:
    return bool(candidate.symbol) and _asset_type(packet) in {"etf", "fund"}


def _direct_fund_metadata_support_lines(
    candidate: CandidateCheck,
    packet_payload: dict[str, object],
) -> list[str]:
    metadata = _dict_value(_dict_value(packet_payload.get("local_data")).get("fund_metadata"))
    if not candidate.symbol or not metadata:
        return []
    return [_fund_metadata_line("基金资料", candidate.symbol, metadata)]


def _direct_fund_metadata_missing_data_lines(
    candidate: CandidateCheck,
    packet_payload: dict[str, object],
) -> list[str]:
    if not candidate.symbol:
        return []
    metadata = _dict_value(_dict_value(packet_payload.get("local_data")).get("fund_metadata"))
    return _fund_metadata_missing_lines("基金资料", candidate.symbol, metadata)


def _direct_fund_metadata_is_complete(
    candidate: CandidateCheck,
    packet_payload: dict[str, object],
) -> bool:
    if not candidate.symbol:
        return False
    metadata = _dict_value(_dict_value(packet_payload.get("local_data")).get("fund_metadata"))
    return bool(metadata) and not _fund_metadata_missing_lines(
        "基金资料",
        candidate.symbol,
        metadata,
    )


def _direct_fund_metadata_check_detail(
    candidate: CandidateCheck,
    packet_payload: dict[str, object],
) -> str:
    if not candidate.symbol:
        return "缺少基金或 ETF 代码，无法核验成分和费用。"
    metadata = _dict_value(_dict_value(packet_payload.get("local_data")).get("fund_metadata"))
    missing = _fund_metadata_missing_lines("基金资料", candidate.symbol, metadata)
    if missing:
        return "；".join(missing)
    return _fund_metadata_line("基金资料", candidate.symbol, metadata)


def _proxy_fund_metadata_support_lines(packet_payload: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for row in _symbol_mapping_rows(packet_payload):
        symbol = _string_value(row.get("symbol"))
        metadata = _dict_value(row.get("fund_metadata"))
        if not symbol or not metadata:
            continue
        lines.append(_fund_metadata_line("代理资料", symbol, metadata))
    return lines


def _research_metric_check_detail(rows: list[dict[str, object]]) -> str:
    return "；".join(_research_metric_line(row) for row in rows[:3])


def _financial_snapshot_check_detail(rows: list[dict[str, object]]) -> str:
    return "；".join(_financial_snapshot_line(row) for row in rows[:2])


def _forecast_check_detail(row: dict[str, object]) -> str:
    symbol = _string_value(row.get("symbol")) or "当前标的"
    method = _string_value(row.get("method")) or "TimesFM"
    horizon = row.get("horizon_days")
    horizon_text = f"未来 {horizon} 个交易日" if isinstance(horizon, int) else "未来区间"
    return f"{symbol} 已有 {method} {horizon_text}预测；必须回测后再解释，不作为支持证据。"


def _financial_snapshot_support_lines(rows: list[dict[str, object]]) -> list[str]:
    return [f"财务快照: {_financial_snapshot_line(row)}" for row in rows[:2]]


def _financial_snapshot_line(row: dict[str, object]) -> str:
    symbol = _string_value(row.get("symbol")) or "美股"
    fiscal_year = row.get("fiscal_year")
    year = str(fiscal_year) if isinstance(fiscal_year, int) else ""
    period = _string_value(row.get("fiscal_period"))
    form = _string_value(row.get("form"))
    label = " ".join(part for part in [symbol, year, period, form] if part)
    currency = _string_value(row.get("currency")) or "USD"
    values = [
        _financial_snapshot_value(
            "营收",
            row.get("revenue"),
            currency,
            row.get("revenue_period_start"),
            row.get("revenue_period_end"),
            row.get("revenue_prior"),
            row.get("revenue_prior_period_start"),
            row.get("revenue_prior_period_end"),
        ),
        _financial_snapshot_value(
            "净利润",
            row.get("net_income"),
            currency,
            row.get("net_income_period_start"),
            row.get("net_income_period_end"),
            row.get("net_income_prior"),
            row.get("net_income_prior_period_start"),
            row.get("net_income_prior_period_end"),
        ),
        _financial_snapshot_value(
            "经营现金流",
            row.get("operating_cash_flow"),
            currency,
            row.get("operating_cash_flow_period_start"),
            row.get("operating_cash_flow_period_end"),
            row.get("operating_cash_flow_prior"),
            row.get("operating_cash_flow_prior_period_start"),
            row.get("operating_cash_flow_prior_period_end"),
        ),
    ]
    source_url = _string_value(row.get("source_url"))
    text = f"{label}: " + "，".join(value for value in values if value)
    if source_url:
        text += f"，来源 {source_url}"
    return text


def _financial_snapshot_value(
    label: str,
    value: object,
    currency: str,
    period_start: object,
    period_end: object,
    prior_value: object,
    prior_period_start: object,
    prior_period_end: object,
) -> str:
    if isinstance(value, int | float):
        text = f"{label} {value:,.0f} {currency}"
        start = _string_value(period_start)
        end = _string_value(period_end)
        if start and end:
            text += f" ({start} 至 {end})"
        elif end:
            text += f" (截至 {end})"
        comparison = _financial_snapshot_comparison(
            value,
            prior_value,
            currency,
            prior_period_start,
            prior_period_end,
        )
        if comparison:
            text += f"，{comparison}"
        return text
    return ""


def _financial_snapshot_comparison(
    value: int | float,
    prior_value: object,
    currency: str,
    prior_period_start: object,
    prior_period_end: object,
) -> str:
    if not isinstance(prior_value, int | float):
        return ""
    prior_text = f"上年同期 {prior_value:,.0f} {currency}"
    prior_start = _string_value(prior_period_start)
    prior_end = _string_value(prior_period_end)
    if prior_start and prior_end:
        prior_text += f" ({prior_start} 至 {prior_end})"
    elif prior_end:
        prior_text += f" (截至 {prior_end})"
    if value < 0 or prior_value < 0:
        return f"{prior_text}（当前或上年同期为负值，未计算同比）"
    if prior_value == 0:
        return f"{prior_text}（上年同期为零，未计算同比）"
    percentage = (value - prior_value) / abs(prior_value) * 100
    return f"同比 {percentage:+.1f}% ({prior_text})"


def _research_metric_support_lines(rows: list[dict[str, object]]) -> list[str]:
    return [f"研究指标: {_research_metric_line(row)}" for row in rows[:3]]


def _research_metric_line(row: dict[str, object]) -> str:
    symbol = _string_value(row.get("symbol")) or "-"
    domain = _research_metric_domain_label(_string_value(row.get("domain")))
    name = _string_value(row.get("name")) or "未命名指标"
    value = _string_value(row.get("value")) or "-"
    parts = [f"{symbol} {domain} {name} = {value}"]
    as_of = _string_value(row.get("as_of"))
    source_url = _string_value(row.get("source_url"))
    note = _string_value(row.get("note"))
    if as_of:
        parts.append(f"日期 {as_of}")
    if source_url:
        parts.append(f"来源 {source_url}")
    if note:
        parts.append(note)
    return " | ".join(parts)


def _research_metric_domain_label(domain: str) -> str:
    return {
        "market_breadth": "市场广度",
        "volatility_metrics": "波动率指标",
        "benchmark_comparison": "基准比较",
        "fund_flows": "资金流",
        "sector_performance": "行业表现",
    }.get(domain.strip().lower(), domain or "研究指标")


def _market_breadth_missing_lines(
    candidate: CandidateCheck,
    rows: list[dict[str, object]],
) -> list[str]:
    if (candidate.symbol or "").upper() != "QQQ":
        return []
    breadth_rows = [
        row
        for row in rows
        if _string_value(row.get("domain")).casefold() == "market_breadth"
    ]
    if not breadth_rows:
        return []
    has_advancer_count = any(
        any(
            keyword in _string_value(row.get("name")).casefold()
            for keyword in ("上涨家数", "下跌家数", "advancer", "decliner")
        )
        for row in breadth_rows
    )
    if has_advancer_count:
        return []
    return [
        "已补齐 Nasdaq NDX/NDXE 等权扩散代理，但仍缺真实上涨家数/下跌家数；"
        "代理不能替代成分级市场广度。"
    ]


def _proxy_missing_data_lines(packet_payload: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for row in _symbol_mapping_rows(packet_payload):
        symbol = _string_value(row.get("symbol"))
        metadata = _dict_value(row.get("fund_metadata"))
        if symbol:
            lines.extend(_fund_metadata_missing_lines("代理资料", symbol, metadata))
    return lines


def _fund_metadata_line(
    label: str,
    symbol: str,
    metadata: dict[str, object],
) -> str:
    parts = [f"{label}: {symbol}"]
    display_name = _string_value(metadata.get("display_name"))
    tracking_index = _string_value(metadata.get("tracking_index"))
    expense_ratio = _string_value(metadata.get("expense_ratio"))
    holdings_summary = _string_value(metadata.get("holdings_summary"))
    source_url = _string_value(metadata.get("source_url"))
    as_of = _string_value(metadata.get("as_of"))
    if display_name:
        parts.append(f"名称 {display_name}")
    if tracking_index:
        parts.append(f"跟踪指数 {tracking_index}")
    if expense_ratio:
        parts.append(f"费用 {expense_ratio}")
    if holdings_summary:
        parts.append(f"成分 {holdings_summary}")
    if source_url:
        parts.append(f"来源 {source_url}")
    if as_of:
        parts.append(f"截止 {as_of}")
    return " | ".join(parts)


def _fund_metadata_missing_lines(
    label: str,
    symbol: str,
    metadata: dict[str, object],
) -> list[str]:
    if not metadata:
        return [f"{label}: 缺少 {symbol} 成分/费用缓存"]
    missing_parts: list[str] = []
    if not (
        _string_value(metadata.get("tracking_index"))
        or _string_value(metadata.get("holdings_summary"))
    ):
        missing_parts.append("成分/跟踪指数")
    if not _string_value(metadata.get("expense_ratio")):
        missing_parts.append("费用")
    if not _string_value(metadata.get("source_url")):
        missing_parts.append("来源")
    if not missing_parts:
        return []
    return [f"{label}: {symbol} 缺少{'、'.join(missing_parts)}缓存"]


def _proxy_mapping_check_detail(packet_payload: dict[str, object]) -> str:
    lines = [
        *_proxy_mapping_support_lines(packet_payload),
        *_proxy_operability_support_lines(packet_payload),
        *_proxy_fund_metadata_support_lines(packet_payload),
        *_proxy_missing_data_lines(packet_payload),
    ]
    if not lines:
        return "代理标的需要人工核对成分、费用、流动性和可交易性。"
    return "；".join(lines)


def _price_check_detail(prices: list[dict[str, object]]) -> str:
    return "；".join(_price_line(price).removeprefix("行情: ") for price in prices)


def _price_volume_ok(price: dict[str, object]) -> bool:
    volume = price.get("volume")
    return isinstance(volume, int | float) and volume > 0


def _volume_check_detail(prices: list[dict[str, object]]) -> str:
    details: list[str] = []
    for price in prices:
        symbol = _string_value(price.get("symbol")) or "-"
        volume = price.get("volume")
        if volume is None:
            details.append(f"{symbol} 缺少成交量字段")
        else:
            details.append(f"{symbol} 成交量 {volume}")
    return "；".join(details)


def build_research_decision_board(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    checks: list[ResearchVerificationCheck],
    evidence_board: dict[str, list[str]],
    *,
    evidence_reviews: list[ResearchEvidenceReviewRecord] | None = None,
) -> ResearchDecisionBoard:
    failed = [check for check in checks if check.status == "fail"]
    topic_relevance = _news_topic_relevance(
        candidate,
        packet,
        evidence_reviews=evidence_reviews,
    )
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
            candidate=candidate,
        )
    fund_metadata_check = _warning_check(checks, "基金资料核验")
    if fund_metadata_check is not None:
        return _research_decision_board(
            workflow_state="fund_metadata_review",
            workflow_label="先补 ETF/基金资料",
            primary_question=question,
            decision_rule="ETF/基金必须先核对成分、跟踪指数、费用和来源，否则不能判断它是否覆盖研究主题。",
            suggested_verdict="needs_more_evidence",
            next_steps=[
                "先生成基金资料补齐向导，再从基金公司、交易所或券商页面补齐成分/跟踪指数、费用和来源。",
                "补齐后重新运行下钻核验，再决定是否生成研究备忘录。",
            ],
            candidate=candidate,
            next_commands=[
                _fund_metadata_guide_command(candidate),
                _fund_metadata_set_command(candidate),
                _research_review_command(candidate, "needs_more_evidence"),
            ],
        )
    if topic_relevance.matched_count == 0:
        next_steps = [
            "刷新主题新闻，并确认风险栏新闻是否真的回答研究问题。",
            "如果仍无主题证据，先记录需要补证据，暂不生成研究备忘录。",
        ]
        if candidate.topic_news_exhausted:
            next_steps = [
                "主题新闻已刷新但没有留下可用证据，不要重复刷新同一查询。",
                "记录需要更高质量数据源、调整研究问题，或暂时把线索放回观察池。",
            ]
        return _research_decision_board(
            workflow_state="evidence_review",
            workflow_label="先复核主题相关性",
            primary_question=question,
            decision_rule="现有新闻或证据没有命中研究主题关键词，不能把市场噪音当成研究依据。",
            suggested_verdict="needs_more_evidence",
            next_steps=next_steps,
            candidate=candidate,
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
            candidate=candidate,
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
            candidate=candidate,
        )
    if candidate.proxy_symbols:
        return _research_decision_board(
            workflow_state="proxy_review",
            workflow_label="先核对代理标的",
            primary_question=question,
            decision_rule="当前任务依赖代理标的，必须先确认代理是否覆盖原主题。",
            suggested_verdict="needs_more_evidence",
            next_steps=[
                "补代理标的成分/费用资料，并查看下钻核验里的可交易性和成交量。",
                "代理通过后再把代理行情和主题新闻放到同一证据板复核。",
            ],
            candidate=candidate,
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
            candidate=candidate,
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
        candidate=candidate,
    )


def _research_decision_board(
    *,
    workflow_state: str,
    workflow_label: str,
    primary_question: str,
    decision_rule: str,
    suggested_verdict: str,
    next_steps: list[str],
    candidate: CandidateCheck,
    next_commands: list[str] | None = None,
) -> ResearchDecisionBoard:
    return ResearchDecisionBoard(
        workflow_state=workflow_state,
        workflow_label=workflow_label,
        primary_question=primary_question,
        decision_rule=decision_rule,
        suggested_verdict=suggested_verdict,
        suggested_verdict_label=RESEARCH_REVIEW_VERDICTS[suggested_verdict],
        next_steps=next_steps,
        next_commands=next_commands
        if next_commands is not None
        else _decision_board_next_commands(candidate, suggested_verdict),
    )


def _warning_check(
    checks: list[ResearchVerificationCheck],
    name: str,
) -> ResearchVerificationCheck | None:
    return next((check for check in checks if check.name == name and check.status == "warn"), None)


def _decision_board_next_commands(
    candidate: CandidateCheck,
    suggested_verdict: str,
) -> list[str]:
    selector = _research_selector(candidate.symbol, candidate.display_name)
    limit_arg = _research_limit_arg(candidate.command_limit)
    commands: list[str] = []
    should_refresh = suggested_verdict in {"needs_more_evidence", "blocked"}
    if suggested_verdict == "needs_more_evidence" and (
        candidate.topic_news_exhausted or candidate.topic_news_review_ready
    ):
        should_refresh = False
    if should_refresh:
        commands.append(f"lychee research run {selector}{limit_arg} --force")
    if suggested_verdict == "needs_more_evidence" and candidate.topic_news_review_ready:
        commands.append(f"lychee research pending-evidence {selector}")
    if suggested_verdict == "continue_research":
        commands.append(f"lychee research memo {selector}{limit_arg}")
    note = _quote_cli_value("证据仍需补强，继续研究流程复核。")
    if suggested_verdict == "continue_research":
        note = _quote_cli_value("证据通过下钻核验，进入人工一致性复核。")
    elif suggested_verdict == "blocked":
        note = _quote_cli_value("存在阻塞，先记录阻塞并补齐数据。")
    commands.append(
        f"lychee research review {selector}{limit_arg} "
        f"--verdict {suggested_verdict} --note {note}"
    )
    return commands


def _research_review_command(
    candidate: CandidateCheck,
    verdict: str,
) -> str:
    selector = _research_selector(candidate.symbol, candidate.display_name)
    limit_arg = _research_limit_arg(candidate.command_limit)
    note = _quote_cli_value("证据仍需补强，继续研究流程复核。")
    return (
        f"lychee research review {selector}{limit_arg} "
        f"--verdict {verdict} --note {note}"
    )


def _fund_metadata_set_command(candidate: CandidateCheck) -> str:
    symbol = candidate.symbol or "<SYMBOL>"
    return (
        f"lychee data set fund --symbol {symbol} "
        f"--name {_quote_cli_value(candidate.display_name)} "
        f"--market {candidate.market.upper() or '<MARKET>'} "
        '--tracking-index "<填写跟踪指数>" '
        '--expense-ratio "<填写费用率>" '
        '--holdings-summary "<填写成分摘要>" '
        '--source-url "<填写资料来源URL>"'
    )


def _fund_metadata_guide_command(candidate: CandidateCheck) -> str:
    symbol = candidate.symbol or "<SYMBOL>"
    return (
        f"lychee data guide fund --symbol {symbol} "
        f"--name {_quote_cli_value(candidate.display_name)} "
        f"--market {candidate.market.upper() or '<MARKET>'}"
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
    has_item_changes = _has_evidence_item_changes(added, removed)
    support_delta = current_counts["support"] - previous_counts["support"]
    risk_delta = current_counts["risk"] - previous_counts["risk"]
    off_topic_delta = current_counts["off_topic"] - previous_counts["off_topic"]
    missing_delta = current_counts["missing"] - previous_counts["missing"]
    status, status_label = _evidence_change_status(
        support_delta,
        risk_delta,
        missing_delta,
        off_topic_delta,
        has_item_changes,
    )
    return ResearchEvidenceChange(
        status=status,
        status_label=status_label,
        summary=_evidence_change_summary(
            support_delta,
            risk_delta,
            missing_delta,
            off_topic_delta,
            has_item_changes,
        ),
        support_delta=support_delta,
        risk_delta=risk_delta,
        missing_delta=missing_delta,
        off_topic_delta=off_topic_delta,
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
        off_topic_delta=0,
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
        ("新增离题/已过滤", change.added.get("off_topic", [])),
        ("已移除离题/已过滤", change.removed.get("off_topic", [])),
        ("新增待补证据", change.added["missing"]),
        ("已补掉待补证据", change.removed["missing"]),
    ]


def build_research_analyst_readout(
    *,
    evidence_board: dict[str, list[str]],
    decision_board: ResearchDecisionBoard,
    evidence_change: ResearchEvidenceChange,
) -> ResearchAnalystReadout:
    support_count = len(evidence_board.get("support", []))
    risk_count = len(evidence_board.get("risk", []))
    off_topic_count = len(evidence_board.get("off_topic", []))
    missing_count = len(evidence_board.get("missing", []))
    first_step = decision_board.next_steps[0] if decision_board.next_steps else "继续复核证据板。"
    next_command = decision_board.next_commands[0] if decision_board.next_commands else ""
    return ResearchAnalystReadout(
        title="分析师读数",
        signal=_analyst_signal_line(support_count),
        pressure=_analyst_pressure_line(risk_count, off_topic_count),
        gap=_analyst_gap_line(missing_count),
        evidence_change=f"证据变化: {evidence_change.status_label}；{evidence_change.summary}",
        next_action=f"工作台动作: {decision_board.workflow_label}；{first_step}",
        next_command=next_command,
    )


def _empty_research_analyst_readout() -> ResearchAnalystReadout:
    return ResearchAnalystReadout(
        title="分析师读数",
        signal="当前信号: 尚未生成证据读数。",
        pressure="反向压力: 尚未生成风险读数。",
        gap="证据缺口: 尚未生成缺口读数。",
        evidence_change="证据变化: 尚未生成变化读数。",
        next_action="工作台动作: 尚未生成下一步动作。",
        next_command="",
    )


def _analyst_signal_line(support_count: int) -> str:
    if support_count:
        return f"当前信号: 支持证据 {support_count} 条，先判断它们是否回答同一个研究问题。"
    return "当前信号: 暂无可用支持证据，不应把这条线索推进到研究结论。"


def _analyst_pressure_line(risk_count: int, off_topic_count: int) -> str:
    if risk_count:
        return (
            f"反向压力: 风险/反向待查 {risk_count} 条，先解释压力来源，"
            f"并剔除离题噪音 {off_topic_count} 条。"
        )
    if off_topic_count:
        return f"反向压力: 暂无明确反向证据，但已过滤离题噪音 {off_topic_count} 条。"
    return "反向压力: 当前证据板暂无反向证据或离题噪音。"


def _analyst_gap_line(missing_count: int) -> str:
    if missing_count:
        return f"证据缺口: 待补 {missing_count} 条，先补数据或记录阻塞，不生成买卖结论。"
    return "证据缺口: 暂无待补证据，下一步只做一致性复核，不生成买卖结论。"


def build_research_hypothesis_panel(
    *,
    candidate: CandidateCheck,
    evidence_board: dict[str, list[str]],
    decision_board: ResearchDecisionBoard,
) -> ResearchHypothesisPanel:
    support = evidence_board.get("support", [])
    risk = evidence_board.get("risk", [])
    missing = evidence_board.get("missing", [])
    off_topic = evidence_board.get("off_topic", [])
    return ResearchHypothesisPanel(
        title="研究假设面板",
        core_question=f"核心问题: {decision_board.primary_question}",
        working_hypothesis=_research_working_hypothesis(candidate, decision_board),
        support_chain=_first_or_placeholder(
            support,
            "暂无支持链；先补行情、成交量、主题新闻或公告/财报证据。",
        ),
        counter_chain=_counter_chain(risk, off_topic),
        gap_priorities=_gap_priorities(missing, decision_board),
        next_data_requests=_next_hypothesis_data_requests(
            candidate=candidate,
            evidence_board=evidence_board,
            decision_board=decision_board,
        ),
    )


def _empty_research_hypothesis_panel() -> ResearchHypothesisPanel:
    return ResearchHypothesisPanel(
        title="研究假设面板",
        core_question="核心问题: 尚未生成。",
        working_hypothesis="工作假设: 尚未生成。",
        support_chain=["暂无支持链。"],
        counter_chain=["暂无反证链。"],
        gap_priorities=["暂无缺口优先级。"],
        next_data_requests=["暂无下一批数据请求。"],
    )


def _research_working_hypothesis(
    candidate: CandidateCheck,
    decision_board: ResearchDecisionBoard,
) -> str:
    if decision_board.suggested_verdict == "blocked":
        return (
            f"工作假设: {candidate.display_name} 当前仍被关键缺口阻塞；"
            "只有先补齐阻塞证据，才能判断这条线索是否值得继续研究。"
        )
    if decision_board.suggested_verdict == "continue_research":
        return (
            f"工作假设: 如果 {candidate.display_name} 这条线索值得继续研究，"
            "支持链应持续回答核心问题，且反证链不能推翻同一主题。"
        )
    return (
        f"工作假设: {candidate.display_name} 仍处于证据建设阶段；"
        "下一步要把支持证据、反向证据和待补缺口放到同一个核心问题下核对。"
    )


def _first_or_placeholder(rows: list[str], placeholder: str) -> list[str]:
    return rows[:3] if rows else [placeholder]


def _counter_chain(risk: list[str], off_topic: list[str]) -> list[str]:
    if risk:
        return risk[:3]
    if off_topic:
        return [f"离题噪音已过滤: {item}" for item in off_topic[:2]]
    return ["暂无明确反证；继续监控风险栏和离题噪音。"]


def _gap_priorities(
    missing: list[str],
    decision_board: ResearchDecisionBoard,
) -> list[str]:
    if missing:
        return missing[:3]
    if decision_board.next_steps:
        return [f"流程缺口: {decision_board.next_steps[0]}"]
    return ["暂无待补证据；优先做人工一致性复核。"]


def _next_hypothesis_data_requests(
    *,
    candidate: CandidateCheck,
    evidence_board: dict[str, list[str]],
    decision_board: ResearchDecisionBoard,
) -> list[str]:
    requests: list[str] = []
    support = evidence_board.get("support", [])
    risk = evidence_board.get("risk", [])
    missing = evidence_board.get("missing", [])
    for index, gap in enumerate(missing[:3]):
        prefix = "补齐最高优先级缺口" if index == 0 else "继续补齐缺口"
        requests.append(f"{prefix}: {gap}")
    if risk:
        requests.append(f"复核最强反证来源: {risk[0]}")
    if support and risk:
        requests.append("对照支持链和反证链是否回答同一个核心问题。")
    if not requests:
        requests.append(
            f"补充 {candidate.display_name} 的最新行情、成交量、主题新闻或公告/财报，"
            "验证支持链是否延续。"
        )
    if decision_board.next_commands:
        requests.append(f"执行工作台下一步命令: {decision_board.next_commands[0]}")
    return requests[:4]


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


def _latest_verification_artifacts(output_dir: Path) -> list[tuple[dict[str, object], Path]]:
    research_dir = output_dir / "research"
    if not research_dir.exists():
        return []
    latest: dict[str, tuple[str, dict[str, object], Path]] = {}
    for path in sorted(research_dir.glob("research-verification-*.json")):
        payload = _read_json_dict(path)
        candidate = _dict_value(payload.get("candidate"))
        key = _verification_candidate_key(candidate)
        if not key:
            continue
        created_at = _string_value(payload.get("created_at"))
        current = latest.get(key)
        if current is None or created_at >= current[0]:
            latest[key] = (created_at, payload, path)
    return [
        (payload, path)
        for created_at, payload, path in sorted(
            latest.values(),
            key=lambda item: item[0],
            reverse=True,
        )
    ]


def _verification_candidate_key(candidate: dict[str, object]) -> str:
    market = (_string_value(candidate.get("market")) or "").upper()
    symbol = (_string_value(candidate.get("symbol")) or "").upper()
    proxy_symbols = [
        symbol.upper()
        for symbol in _text_list(candidate.get("proxy_symbols"))
        if symbol.strip()
    ]
    if symbol:
        return f"{market}:symbol:{symbol}"
    if proxy_symbols:
        return f"{market}:proxy:{','.join(sorted(proxy_symbols))}"
    display_name = (_string_value(candidate.get("display_name")) or "").lower()
    if display_name:
        return f"{market}:name:{display_name}"
    return ""


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


def _pending_news_evidence_text(raw_evidence: str) -> str | None:
    prefix = "新闻待判定: "
    if not raw_evidence.startswith(prefix):
        return None
    evidence_text = raw_evidence.removeprefix(prefix)
    suffix = " 命中主题但方向未明。"
    if evidence_text.endswith(suffix):
        evidence_text = evidence_text.removesuffix(suffix)
    evidence_text = evidence_text.strip()
    return evidence_text or None


def _pending_evidence_review_command(
    *,
    symbol: str | None,
    display_name: str,
    evidence_text: str,
    verdict: str,
    note: str,
    command_limit: int = DEFAULT_RESEARCH_SELECTION_LIMIT,
) -> str:
    selector = (
        f"--symbol {symbol}"
        if symbol
        else f"--name {_quote_cli_value(display_name)}"
    )
    limit_arg = _research_limit_arg(command_limit)
    return (
        f"lychee research evidence-review {selector}{limit_arg} "
        f"--text {_quote_cli_value(evidence_text)} "
        f"--verdict {verdict} --note {_quote_cli_value(note)}"
    )


def suggest_pending_evidence_review(
    evidence_text: str,
    *,
    primary_question: str | None = None,
) -> tuple[str, str]:
    text = evidence_text.lower()
    question = (primary_question or "").lower()
    if _looks_like_benchmark_comparison(text, question):
        return "support", "新闻在对比主题入口和宽基基准，适合先作为回答研究问题的支持证据。"
    direction = _news_evidence_direction(text)
    if direction == "reverse":
        return "reverse", "系统检测到压力、波动或走弱语义，建议先按风险/反向待查处理。"
    if direction == "support":
        return "support", "系统检测到上涨、改善或反弹语义，建议先按支持证据处理。"
    return "irrelevant", "系统暂未识别明确方向，建议先按无关/排除处理，确认相关时再覆盖。"


def _looks_like_benchmark_comparison(text: str, question: str) -> bool:
    if not question:
        return False
    benchmark_question = any(
        term in question
        for term in ["大盘", "宽基", "broad market", "benchmark", "s&p", "sp 500"]
    )
    if not benchmark_question:
        return False
    has_theme_entry = any(term in text for term in ["qqq", "nasdaq", "纳斯达克"])
    has_benchmark = any(term in text for term in ["voo", "spy", "s&p", "sp 500"])
    has_comparison = any(term in text for term in [" vs", "vs.", "versus", "对比"])
    return has_theme_entry and has_benchmark and has_comparison


def _quote_cli_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
        "off_topic": len(_text_list(board.get("off_topic"))),
        "missing": len(_text_list(board.get("missing"))),
    }


def _empty_evidence_change_items() -> dict[str, list[str]]:
    return {
        "support": [],
        "risk": [],
        "off_topic": [],
        "missing": [],
    }


def _evidence_board_diff(
    previous_board: Mapping[str, object],
    current_board: Mapping[str, object],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    added = _empty_evidence_change_items()
    removed = _empty_evidence_change_items()
    for key in ("support", "risk", "off_topic", "missing"):
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
    off_topic_delta: int,
    has_item_changes: bool,
) -> tuple[str, str]:
    if (
        support_delta == 0
        and risk_delta == 0
        and missing_delta == 0
        and off_topic_delta == 0
    ):
        if has_item_changes:
            return "replaced", "证据内容已替换"
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
    off_topic_delta: int,
    has_item_changes: bool,
) -> str:
    if (
        has_item_changes
        and support_delta == 0
        and risk_delta == 0
        and missing_delta == 0
        and off_topic_delta == 0
    ):
        return "证据数量无变化，但具体内容有替换，需要重新核验。"
    parts = [
        _delta_phrase("支持证据", support_delta),
        _delta_phrase("风险/反向待查", risk_delta),
        _delta_phrase("离题/已过滤", off_topic_delta),
        _delta_phrase("待补证据", missing_delta),
    ]
    return "；".join(parts) + "。"


def _has_evidence_item_changes(
    added: Mapping[str, list[str]],
    removed: Mapping[str, list[str]],
) -> bool:
    return any(
        added.get(key) or removed.get(key)
        for key in ("support", "risk", "off_topic", "missing")
    )


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
    *,
    evidence_reviews: list[ResearchEvidenceReviewRecord] | None = None,
) -> NewsTopicRelevance:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    rows = [
        *_dict_list(packet_payload.get("evidence")),
        *_dict_list(local_data.get("related_news")),
    ]
    terms = _research_theme_terms(candidate, packet)
    market_terms = _market_context_terms(candidate.market)
    asset_type = _asset_type(packet)
    matched_rows: list[dict[str, object]] = []
    support_rows: list[dict[str, object]] = []
    reverse_rows: list[dict[str, object]] = []
    neutral_rows: list[dict[str, object]] = []
    unmatched_rows: list[dict[str, object]] = []
    for row in rows:
        text = _news_text(row)
        reviewed_verdict = _reviewed_evidence_verdict(text, evidence_reviews or [])
        if reviewed_verdict == "support":
            matched_rows.append(row)
            support_rows.append(row)
            continue
        if reviewed_verdict == "reverse":
            matched_rows.append(row)
            reverse_rows.append(row)
            continue
        if reviewed_verdict == "irrelevant":
            unmatched_rows.append(row)
            continue
        if (
            _text_matches_any_topic_term(text, terms)
            and (
                _is_symbol_scoped_news_for_candidate(row, candidate)
                or _matches_market_context(text, market_terms)
            )
            and _matches_financial_context_for_asset(text, asset_type)
            and _matches_direct_fund_context(candidate, packet, text, asset_type)
        ):
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


def _candidate_evidence_reviews(
    output_dir: Path,
    candidate: CandidateCheck,
) -> list[ResearchEvidenceReviewRecord]:
    return list_research_evidence_reviews(
        output_dir,
        symbol=candidate.symbol,
        name=None if candidate.symbol else candidate.display_name,
        limit=100,
    )


def _reviewed_evidence_verdict(
    text: str,
    evidence_reviews: list[ResearchEvidenceReviewRecord],
) -> str | None:
    for review in evidence_reviews:
        reviewed_text = review.evidence_text.strip().lower()
        if not reviewed_text:
            continue
        if reviewed_text in text or text in reviewed_text:
            return review.verdict
    return None


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
        terms.extend(_query_alias_terms(value))
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
    "rebound",
    "rebounds",
    "outperform",
    "outperforms",
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
    "volatility",
    "volatile",
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
        if re.fullmatch(r"[a-z0-9][a-z0-9 .+-]*", term):
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
                return True
        elif term and term in text:
            return True
    return False


def _market_context_terms(market: str) -> list[str]:
    normalized = market.strip().upper()
    if normalized == "HK":
        return ["hong kong", "hang seng", "港股", "香港", ".hk"]
    if normalized == "CN":
        return [
            "china",
            "chinese",
            "a-share",
            "a shares",
            "a股",
            "中国",
            "沪深",
            "上证",
            "深证",
            ".sh",
            ".sz",
        ]
    return []


def _matches_market_context(text: str, market_terms: list[str]) -> bool:
    if not market_terms:
        return True
    return any(term in text for term in market_terms)


def select_research_candidate_index(
    result: WorkbenchCheckResult,
    *,
    symbol: str | None,
    name: str | None,
) -> int | None:
    if symbol:
        target = symbol.strip().upper()
        for index, candidate in enumerate(result.candidates):
            if (candidate.symbol or "").upper() == target:
                return index
        for index, candidate in enumerate(result.candidates):
            if any(item.upper() == target for item in candidate.proxy_symbols):
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
    candidates = _candidate_checks(None, packets)
    status = "blocked" if any(candidate.data_gaps for candidate in candidates) else "ready"
    return _beginner_brief(status, candidates)


def render_research_task_detail(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    *,
    action_status: str = "",
    commands: list[str] | None = None,
) -> str:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    prices = _verification_price_rows(packet_payload, local_data)
    price = prices[0] if prices else {}
    evidence = _dict_list(packet_payload.get("evidence"))
    raw_related_news = _dict_list(local_data.get("related_news"))
    related_news, off_topic_news = _detail_related_news(
        candidate,
        packet,
        raw_related_news,
    )
    filings = _dict_list(local_data.get("filings"))
    financials = _dict_list(local_data.get("financials"))
    forecast = _dict_value(local_data.get("forecast"))
    research_metrics = _dict_list(local_data.get("research_metrics"))
    data_gaps = _text_list(packet_payload.get("data_gaps")) or candidate.data_gaps
    detail_commands = (
        commands if commands is not None else research_action_commands(candidate, packet)
    )
    assessment = build_research_assessment(candidate, packet)
    lines = [
        "研究任务面板",
        f"任务: {candidate.display_name} [{candidate.market}]",
        f"入口: {candidate.observation_entry}",
        f"优先级: {candidate.priority}",
        f"排序理由: {candidate.ranking_reason}",
        f"证据状态: {candidate.evidence_status}",
        "",
        *_research_start_lines(candidate, has_data_gaps=bool(data_gaps)),
        "",
        "研究状态",
        f"- 阶段: {assessment.stage_label}",
        f"- 一致性: {assessment.consistency_label}",
        f"- 证据读数: {assessment.evidence_reading}",
        f"- 下一步判断: {assessment.next_decision}",
        "",
        "信号读数: "
        + _signal_reading(
            candidate,
            price,
            evidence,
            related_news,
            filings,
            financials,
            research_metrics,
            data_gaps,
        ),
        *_price_lines(prices),
        "",
        "研究指标",
        *_research_metric_lines(research_metrics),
        "",
        "证据矩阵",
        *_evidence_matrix_lines(
            candidate=candidate,
            packet=packet,
            prices=prices,
            evidence=evidence,
            related_news=related_news,
            filings=filings,
            financials=financials,
            research_metrics=research_metrics,
            data_gaps=data_gaps,
        ),
        "",
        "已采集证据",
        *_headline_lines(evidence, empty="暂无 discovery 证据。"),
        "",
        "相关新闻",
        *_headline_lines(related_news, empty="暂无匹配新闻。"),
        "",
        "离题/已过滤",
        *_headline_lines(off_topic_news, empty="无。"),
        "",
        "公告/财报线索",
        *_filing_lines(filings),
        "",
        "财务快照",
        *_financial_snapshot_lines(financials),
        "",
        "模型预测参考",
        *_forecast_lines(forecast),
        "",
        f"数据完整性: {_gap_summary(data_gaps)}",
        f"研究缺口: {_research_gap_summary(candidate, data_gaps)}",
        f"下一步动作: {candidate.next_step}",
    ]
    if candidate.proxy_symbols:
        lines.append(_proxy_followup_line())
    lines.extend(["", "可执行动作"])
    if action_status:
        lines.append(action_status)
    lines.extend(f"- {command}" for command in detail_commands)
    lines.append("")
    lines.append("边界: 这是研究工作台快照，不是买卖建议。")
    return "\n".join(lines)


def _detail_related_news(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    related_news: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    terms = _research_theme_terms(candidate, packet)
    market_terms = _market_context_terms(candidate.market)
    asset_type = _asset_type(packet)
    matched: list[dict[str, object]] = []
    off_topic: list[dict[str, object]] = []
    for row in related_news:
        text = _news_text(row)
        if (
            _text_matches_any_topic_term(text, terms)
            and (
                _is_symbol_scoped_news_for_candidate(row, candidate)
                or _matches_market_context(text, market_terms)
            )
            and _matches_financial_context_for_asset(text, asset_type)
            and _matches_direct_fund_context(candidate, packet, text, asset_type)
        ):
            matched.append(row)
        else:
            off_topic.append(row)
    return matched, off_topic


def _matches_financial_context_for_asset(text: str, asset_type: str) -> bool:
    if asset_type.strip().lower() not in {"etf", "fund", "index", "sector"}:
        return True
    return _text_matches_any_topic_term(text, _FINANCIAL_MARKET_CONTEXT_TERMS)


def _is_symbol_scoped_news_for_candidate(
    row: dict[str, object],
    candidate: CandidateCheck,
) -> bool:
    if row.get("is_symbol_scoped") is not True or not candidate.symbol:
        return False
    symbols = row.get("symbols")
    if not isinstance(symbols, list):
        return False
    return candidate.symbol.upper() in {str(symbol).upper() for symbol in symbols}


def _matches_direct_fund_context(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    text: str,
    asset_type: str,
) -> bool:
    if asset_type.strip().lower() not in {"etf", "fund"} or not candidate.symbol:
        return True
    identity_phrases = _direct_fund_identity_phrases(candidate, packet)
    normalized_text = _normalized_identity_text(text)
    if any(phrase and phrase in normalized_text for phrase in identity_phrases):
        return True
    return _matches_direct_fund_theme_context(candidate, packet, text)


def _direct_fund_identity_phrases(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    packet_payload = packet.packet if packet is not None else {}
    local_data = _dict_value(packet_payload.get("local_data"))
    metadata = _dict_value(local_data.get("fund_metadata"))
    raw_values = [
        candidate.symbol or "",
        _symbol_without_suffix(candidate.symbol),
        candidate.display_name,
        _string_value(metadata.get("display_name")),
        _string_value(metadata.get("tracking_index")),
    ]
    phrases: list[str] = []
    for value in raw_values:
        phrases.extend(_identity_phrases(value))
    return _dedupe_terms(phrases)


def _identity_phrases(value: str | None) -> list[str]:
    normalized = _normalized_identity_text(value or "")
    if not normalized:
        return []
    phrases = [normalized]
    tokens = [
        token
        for token in normalized.split()
        if token not in _DIRECT_FUND_GENERIC_IDENTITY_TOKENS
    ]
    for size in range(2, min(len(tokens), 5) + 1):
        phrases.extend(
            " ".join(tokens[index : index + size])
            for index in range(len(tokens) - size + 1)
        )
    return phrases


def _matches_direct_fund_theme_context(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
    text: str,
) -> bool:
    terms = _direct_fund_theme_terms(candidate, packet)
    return _text_matches_any_topic_term(text, terms)


def _direct_fund_theme_terms(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    terms = _topic_terms(candidate, packet)
    return [
        term
        for term in terms
        if term.lower() not in _DIRECT_FUND_GENERIC_CONTEXT_TERMS
    ]


def _symbol_without_suffix(symbol: str | None) -> str:
    if not symbol or "." not in symbol:
        return ""
    return symbol.split(".", 1)[0]


def _normalized_identity_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.lower()).strip()


_DIRECT_FUND_GENERIC_IDENTITY_TOKENS = {
    "e",
    "fund",
    "etf",
    "trust",
    "index",
}


_DIRECT_FUND_GENERIC_CONTEXT_TERMS = {
    "etf",
    "fund",
    "funds",
    "index",
    "indices",
    "trust",
    "market",
    "markets",
    "stock",
    "stocks",
    "share",
    "shares",
    "hong kong stocks",
    "港股",
    "基金",
    "指数",
    "观察",
}


_FINANCIAL_MARKET_CONTEXT_TERMS = [
    "stock",
    "stocks",
    "share",
    "shares",
    "market",
    "markets",
    "index",
    "indices",
    "etf",
    "fund",
    "exchange",
    "turnover",
    "liquidity",
    "hang seng",
    "hkex",
    "港股",
    "股票",
    "股份",
    "指数",
    "基金",
    "交易所",
    "成交",
    "流动性",
    "恒生",
    "大盘",
]


def _research_start_lines(
    candidate: CandidateCheck,
    *,
    has_data_gaps: bool = False,
) -> list[str]:
    selector = _research_selector_arg(candidate)
    limit_arg = _research_limit_arg(candidate.command_limit)
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
    if has_data_gaps:
        lines.append("- 第一步: 先运行下方刷新数据动作，补齐当前数据完整性问题。")
        return lines
    lines.extend(
        [
            f"- 第一步: lychee research verify {selector}{limit_arg}",
            "- 看证据板: 支持证据 / 风险或反向待查 / 待补证据 / 离题或已过滤",
            (
                f"- 记录判断: lychee research review {selector}{limit_arg} "
                '--verdict needs_more_evidence --note "写下还缺什么"'
            ),
            f"- 可选 LLM: lychee research memo {selector}{limit_arg}",
        ]
    )
    return lines


def _research_selector_arg(candidate: CandidateCheck) -> str:
    if candidate.symbol:
        return f"--symbol {candidate.symbol}"
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
    if candidate.topic_news_exhausted:
        return ResearchAssessment(
            stage="evidence_review",
            stage_label="证据耗尽待复核",
            consistency="topic_news_exhausted",
            consistency_label="待复核",
            evidence_reading="主题新闻已刷新，但过滤后没有形成可用相关新闻，不能继续把刷新当作下一步。",
            next_decision="不要重复刷新同一主题新闻；先下钻核验证据板，处理待判定/反向证据，必要时更换数据源。",
        )
    if candidate.topic_news_review_ready:
        return ResearchAssessment(
            stage="evidence_review",
            stage_label="证据待分类",
            consistency="topic_news_review_ready",
            consistency_label="待分类",
            evidence_reading="主题新闻已刷新并形成可用相关新闻，下一步要拆分支持、反向和待判定证据。",
            next_decision="不要重复刷新同一主题新闻；先下钻核验证据板，处理支持、反向和待判定证据。",
        )
    if candidate.evidence_quality in {"missing", "needs_review", "mixed"}:
        return ResearchAssessment(
            stage="evidence_review",
            stage_label="先复核证据",
            consistency="pending_evidence_direction_review",
            consistency_label="待核验",
            evidence_reading=_evidence_review_reading(candidate.evidence_quality),
            next_decision="先刷新主题新闻并重新下钻核验，再决定是否继续研究。",
        )
    if candidate.proxy_symbols and not candidate.symbol:
        return ResearchAssessment(
            stage="proxy_review",
            stage_label="代理核验",
            consistency="pending_proxy_review",
            consistency_label="待核验",
            evidence_reading="这条线索通过代理标的观察，必须先确认代理是否覆盖主题。",
            next_decision="先补代理成分/费用资料，再结合下钻核验里的可交易性和成交量决定是否继续。",
        )
    has_news = bool(evidence or related_news)
    filings_required = candidate.market.upper() in {"US", "HK"} and asset_type == "stock"
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
        ("start_research", "开始/继续研究"),
        ("refresh_market", "刷新行情"),
        ("refresh_news", "刷新新闻"),
    ]
    if _needs_fund_metadata_guide(candidate):
        actions.append(("fund_metadata_guide", "补基金资料向导"))
    if _needs_topic_news_refresh(candidate) and topic_news_query(candidate, packet):
        actions.append(("refresh_topic_news", "刷新主题新闻"))
    if research_filing_symbols(candidate, packet):
        actions.append(("refresh_filings", "刷新公司公告"))
    actions.append(("verify_research", "下钻核验"))
    actions.append(("generate_memo", "生成研究备忘录"))
    actions.append(("back_tasks", "返回研究任务列表"))
    return actions


def research_action_commands(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    symbols = research_action_symbols(candidate)
    limit_arg = _research_limit_arg(candidate.command_limit)
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
            f"刷新公司公告: lychee data pull filings --symbols "
            f"{','.join(filing_symbols)}"
        )
        if candidate.market.upper() == "US":
            commands.append(
                f"刷新财务快照: lychee data pull financials --symbols "
                f"{','.join(filing_symbols)}"
            )
    if candidate.symbol:
        commands.append(
            f"下钻核验: lychee research verify --symbol {candidate.symbol}{limit_arg}"
        )
        commands.append(
            f"研究备忘录: lychee research memo --symbol {candidate.symbol}{limit_arg}"
        )
    else:
        selector = _research_selector(candidate.symbol, candidate.display_name)
        commands.append(f"下钻核验: lychee research verify {selector}{limit_arg}")
        commands.append(
            f"研究备忘录: lychee research memo {selector}{limit_arg}"
        )
    return commands


def research_action_symbols(candidate: CandidateCheck) -> list[str]:
    if candidate.symbol:
        return [candidate.symbol]
    return candidate.proxy_symbols


def _research_candidate_news_query(candidate: CandidateCheck) -> str | None:
    symbol = candidate.symbol or ""
    if candidate.market.upper() != "CN" or not symbol.upper().endswith((".SH", ".SZ")):
        return None
    return candidate.display_name.strip() or None


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
    "流动性": ["liquidity", "turnover"],
    "A股": ["China A shares"],
    "半导体": ["semiconductor", "chip"],
    "利率": ["interest rates", "yields"],
    "大盘": ["broad market"],
    "消费": ["consumer spending"],
    "政策": ["policy"],
}


def _needs_topic_news_refresh(candidate: CandidateCheck) -> bool:
    if candidate.topic_news_exhausted or candidate.topic_news_review_ready:
        return False
    return candidate.evidence_quality in {"missing", "needs_review", "mixed"}


def _escape_command_arg(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def research_filing_symbols(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    if candidate.market.upper() not in {"US", "HK", "CN"} or not candidate.symbol:
        return []
    if _asset_type(packet) != "stock":
        return []
    return [candidate.symbol]


def research_action_name(action: str) -> str:
    return {
        "start_research": "开始/继续研究",
        "refresh_market": "刷新行情",
        "refresh_news": "刷新新闻",
        "fund_metadata_guide": "补基金资料向导",
        "refresh_topic_news": "刷新主题新闻",
        "refresh_filings": "刷新公司公告",
        "verify_research": "下钻核验",
        "generate_memo": "生成研究备忘录",
    }.get(action, action)


def _needs_fund_metadata_guide(candidate: CandidateCheck) -> bool:
    return bool(candidate.symbol) and candidate.next_command.startswith(
        "lychee data guide fund"
    )


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
                    query=_research_candidate_news_query(candidate),
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
    if result.count == 0:
        status = "failed" if result.refreshed and result.warnings else "no_data"
        message = (
            f"{research_action_name(action_type)}未完成。"
            if status == "failed"
            else f"{research_action_name(action_type)}没有获取到匹配数据。"
        )
    elif result.refreshed:
        status = "pulled"
        message = f"{research_action_name(action_type)}完成。"
    else:
        status = "cached"
        message = f"{research_action_name(action_type)}使用本地缓存。"
    return ResearchRunAction(
        action_type=action_type,
        status=status,
        symbols=symbols,
        count=result.count,
        output_path=result.output_path,
        warnings=result.warnings,
        message=message,
    )


def _research_run_action_status(
    actions: list[ResearchRunAction],
    *,
    related_news_count: int | None = None,
) -> str:
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
    topic_action = next(
        (action for action in actions if action.action_type == "refresh_topic_news"),
        None,
    )
    if topic_action is not None and related_news_count is not None:
        lines.append(
            f"主题新闻过滤: 本次拉取 {topic_action.count} 条，"
            f"{related_news_count} 条进入相关新闻。"
        )
    return "\n".join(lines)


def _packet_related_news_count(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> int:
    if packet is None:
        return 0
    local_data = _dict_value(packet.packet.get("local_data"))
    related_news, _off_topic_news = _detail_related_news(
        candidate,
        packet,
        _dict_list(local_data.get("related_news")),
    )
    return len(related_news)


def _packet_topic_news_count(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> int:
    if packet is None:
        return 0
    packet_payload = packet.packet
    local_data = _dict_value(packet_payload.get("local_data"))
    theme_terms = _research_theme_terms(candidate, packet)
    if not theme_terms:
        return 0
    market_terms = _market_context_terms(candidate.market)
    asset_type = _asset_type(packet)
    return sum(
        1
        for row in _dict_list(local_data.get("related_news"))
        if _text_matches_any_topic_term(_news_text(row), theme_terms)
        and (
            _is_symbol_scoped_news_for_candidate(row, candidate)
            or _matches_market_context(_news_text(row), market_terms)
        )
        and _matches_financial_context_for_asset(_news_text(row), asset_type)
        and _matches_direct_fund_context(candidate, packet, _news_text(row), asset_type)
    )


def _research_theme_terms(
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> list[str]:
    identity_terms = {
        term.lower()
        for term in [candidate.symbol or "", candidate.display_name]
        if term.strip()
    }
    return [
        term
        for term in _topic_terms(candidate, packet)
        if term.lower() not in identity_terms
    ]


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
    output_path = _unique_artifact_path(research_dir, "research-run", created_at)
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
    analyst_readout: ResearchAnalystReadout,
    hypothesis_panel: ResearchHypothesisPanel,
    conclusion: str,
    next_actions: list[str],
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = _unique_artifact_path(
        research_dir,
        "research-verification",
        created_at,
    )
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
                "analyst_readout": asdict(analyst_readout),
                "hypothesis_panel": asdict(hypothesis_panel),
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


def _research_evidence_review_payload(
    *,
    created_at: str,
    verdict: str,
    verdict_label: str,
    evidence_text: str,
    note: str,
    candidate: CandidateCheck,
) -> dict[str, object]:
    candidate_key = candidate.symbol or candidate.display_name
    return {
        "review_id": (
            f"research-evidence-review:{created_at}:{candidate.market}:{candidate_key}"
        ),
        "created_at": created_at,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "evidence_text": evidence_text,
        "note": note,
        "candidate": asdict(candidate),
        "disclaimer": "单条证据复核只记录证据方向，不是买卖建议。",
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
        "analyst_readout": asdict(result.analyst_readout),
        "hypothesis_panel": asdict(result.hypothesis_panel),
        "conclusion": result.conclusion,
        "next_actions": result.next_actions,
        "artifact_path": str(result.artifact_path),
    }


def _write_research_evidence_review_artifact(
    *,
    output_dir: Path,
    created_at: str,
    payload: dict[str, object],
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = _unique_artifact_path(
        research_dir,
        "research-evidence-review",
        created_at,
    )
    payload["review_path"] = str(output_path)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _write_research_review_artifact(
    *,
    output_dir: Path,
    created_at: str,
    payload: dict[str, object],
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = _unique_artifact_path(research_dir, "research-review", created_at)
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


def _candidate_checks(
    output_dir: Path | None,
    packets: list[ResearchPacket],
    *,
    command_limit: int = DEFAULT_RESEARCH_SELECTION_LIMIT,
    now: datetime | None = None,
) -> list[CandidateCheck]:
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
            command_limit=command_limit,
        )
        evidence_quality = _candidate_evidence_quality(base_candidate, packet)
        candidate_check = replace(
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
            next_command=_next_command(
                status=status,
                symbol=packet.symbol,
                display_name=packet.display_name,
                data_gaps=data_gaps,
                evidence_quality=evidence_quality,
                command_limit=command_limit,
            ),
        )
        data_request_news_state = (
            _latest_data_request_topic_news_state(output_dir, candidate_check, packet)
            if output_dir is not None
            else ""
        )
        if output_dir is not None and _latest_verification_requires_fund_metadata(
            output_dir,
            candidate_check,
        ):
            candidate_check = _mark_fund_metadata_review(candidate_check)
        elif data_request_news_state == "exhausted":
            candidate_check = _mark_topic_news_exhausted(candidate_check)
        elif data_request_news_state == "review_ready":
            candidate_check = _mark_topic_news_review_ready(candidate_check)
        elif output_dir is not None and _latest_research_run_exhausted_topic_news(
            output_dir,
            candidate_check,
            packet,
        ):
            candidate_check = _mark_topic_news_exhausted(candidate_check)
        elif output_dir is not None and _latest_research_run_refreshed_topic_news(
            output_dir,
            candidate_check,
            packet,
        ):
            candidate_check = _mark_topic_news_review_ready(candidate_check)
        elif output_dir is not None and (
            ready_for_review := _latest_verification_ready_for_review(
                output_dir,
                candidate_check,
            )
        ) is not None:
            candidate_check = _mark_ready_for_review(candidate_check, ready_for_review)
        if output_dir is not None and _candidate_market_is_in_no_data_cooldown(
            output_dir,
            candidate_check,
            now=now,
        ):
            candidate_check = _mark_market_no_data_cooldown(candidate_check)
        checks.append(candidate_check)
    return checks


def _candidate_market_is_in_no_data_cooldown(
    output_dir: Path,
    candidate: CandidateCheck,
    *,
    now: datetime | None,
) -> bool:
    if not any(
        "本地行情" in gap or "代理标的行情" in gap
        for gap in candidate.data_gaps
    ):
        return False
    symbols = {
        symbol.upper()
        for symbol in [candidate.symbol, *candidate.proxy_symbols]
        if symbol
    }
    if not symbols:
        return False
    for entry in list_cache_entries(output_dir, layer="market"):
        if cache_entry_status(entry, now=now) != "no_data":
            continue
        cached_symbols = set(entry.cache_key.rsplit(":", 1)[-1].upper().split(","))
        if symbols.intersection(cached_symbols):
            return True
    return False


def _mark_market_no_data_cooldown(candidate: CandidateCheck) -> CandidateCheck:
    return replace(
        candidate,
        next_step="行情数据暂不可用，先检查数据健康或更新 provider 权限。",
        next_command="lychee data health",
    )


def _latest_verification_requires_fund_metadata(
    output_dir: Path,
    candidate: CandidateCheck,
) -> bool:
    path = _latest_matching_verification_artifact(output_dir, candidate)
    if path is None:
        return False
    payload = _read_json_dict(path)
    decision_board = _dict_value(payload.get("decision_board"))
    return _string_value(decision_board.get("workflow_state")) == "fund_metadata_review"


def _mark_fund_metadata_review(candidate: CandidateCheck) -> CandidateCheck:
    return replace(
        candidate,
        status="blocked",
        gap_count=max(1, candidate.gap_count),
        priority="P2 待补基金资料",
        ranking_reason="最近一次下钻核验要求先补 ETF/基金成分、跟踪指数、费用和来源。",
        next_step="先生成 ETF/基金资料补齐向导；补齐后重新运行下钻核验。",
        evidence_status=candidate.evidence_status + "；核验阻塞: 待补基金资料",
        next_command=_fund_metadata_guide_command(candidate),
    )


def _latest_verification_ready_for_review(
    output_dir: Path,
    candidate: CandidateCheck,
) -> tuple[str, str] | None:
    path = _latest_matching_verification_artifact(output_dir, candidate)
    if path is None:
        return None
    payload = _read_json_dict(path)
    decision_board = _dict_value(payload.get("decision_board"))
    if _string_value(decision_board.get("workflow_state")) != "ready_for_review":
        return None
    commands = _text_list(decision_board.get("next_commands"))
    memo_command = next(
        (command for command in commands if command.startswith("lychee research memo ")),
        "",
    )
    if not memo_command:
        return None
    steps = _text_list(decision_board.get("next_steps"))
    step = steps[0] if steps else "记录继续研究，并进入人工一致性复核。"
    return step, memo_command


def _mark_ready_for_review(
    candidate: CandidateCheck,
    ready_for_review: tuple[str, str],
) -> CandidateCheck:
    next_step, next_command = ready_for_review
    return replace(
        candidate,
        priority="P1 一致性复核",
        ranking_reason="最近一次下钻核验已完成基础证据检查，下一步进入人工一致性复核。",
        next_step=next_step,
        next_command=next_command,
    )


def _actions_exhausted_topic_news(
    actions: list[ResearchRunAction],
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> bool:
    return _has_successful_topic_news_refresh(actions) and (
        _packet_topic_news_count(candidate, packet) == 0
    )


def _actions_refreshed_topic_news_for_review(
    actions: list[ResearchRunAction],
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> bool:
    if candidate.evidence_quality not in {"missing", "needs_review", "mixed"}:
        return False
    return _has_successful_topic_news_refresh(actions) and (
        _packet_topic_news_count(candidate, packet) > 0
    )


def _has_successful_topic_news_refresh(actions: list[ResearchRunAction]) -> bool:
    topic_refreshes = [
        action
        for action in actions
        if action.action_type == "refresh_topic_news"
        and action.status != "failed"
        and action.count > 0
    ]
    return bool(topic_refreshes)


def _latest_data_request_topic_news_state(
    output_dir: Path,
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> str:
    for record in list_research_data_request_fulfillments(output_dir, limit=500):
        if not _fulfillment_matches_candidate(record, candidate):
            continue
        if not _fulfillment_refreshed_news(record):
            return ""
        return "review_ready" if _packet_topic_news_count(candidate, packet) else "exhausted"
    return ""


def _fulfillment_matches_candidate(
    record: ResearchDataRequestFulfillmentRecord,
    candidate: CandidateCheck,
) -> bool:
    if record.market.strip().upper() != candidate.market.strip().upper():
        return False
    if candidate.symbol and record.symbol:
        return candidate.symbol.strip().upper() == record.symbol.strip().upper()
    return record.display_name.strip().casefold() == candidate.display_name.strip().casefold()


def _fulfillment_refreshed_news(record: ResearchDataRequestFulfillmentRecord) -> bool:
    for execution in _dict_list(record.payload.get("executions")):
        if _string_value(execution.get("action_type")) != "news":
            continue
        if _string_value(execution.get("status")) not in {"completed", "cached"}:
            continue
        if _int_value(execution.get("count")) > 0:
            return True
    return False


def _latest_research_run_exhausted_topic_news(
    output_dir: Path,
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> bool:
    research_dir = output_dir / "research"
    if not research_dir.exists():
        return False
    for path in sorted(research_dir.glob("research-run-*.json"), reverse=True):
        payload = _read_json_dict(path)
        payload_candidate = _dict_value(payload.get("candidate"))
        if not _verification_candidate_matches(payload_candidate, candidate):
            continue
        if payload_candidate.get("topic_news_exhausted") is True:
            return True
        assessment = _dict_value(payload.get("assessment"))
        if _string_value(assessment.get("consistency")) == "topic_news_exhausted":
            return True
        actions = [
            ResearchRunAction(
                action_type=_string_value(row.get("action_type")),
                status=_string_value(row.get("status")),
                symbols=_text_list(row.get("symbols")),
                count=_int_value(row.get("count")),
                output_path=None,
                message=_string_value(row.get("message")),
                warnings=_text_list(row.get("warnings")),
            )
            for row in _dict_list(payload.get("actions"))
        ]
        return _actions_exhausted_topic_news(actions, candidate, packet)
    return False


def _latest_research_run_refreshed_topic_news(
    output_dir: Path,
    candidate: CandidateCheck,
    packet: ResearchPacket | None,
) -> bool:
    research_dir = output_dir / "research"
    if not research_dir.exists():
        return False
    for path in sorted(research_dir.glob("research-run-*.json"), reverse=True):
        payload = _read_json_dict(path)
        payload_candidate = _dict_value(payload.get("candidate"))
        if not _verification_candidate_matches(payload_candidate, candidate):
            continue
        if payload_candidate.get("topic_news_exhausted") is True:
            return False
        assessment = _dict_value(payload.get("assessment"))
        if _string_value(assessment.get("consistency")) == "topic_news_exhausted":
            return False
        if payload_candidate.get("topic_news_review_ready") is True:
            return True
        if _string_value(assessment.get("consistency")) == "topic_news_review_ready":
            return True
        actions = [
            ResearchRunAction(
                action_type=_string_value(row.get("action_type")),
                status=_string_value(row.get("status")),
                symbols=_text_list(row.get("symbols")),
                count=_int_value(row.get("count")),
                output_path=None,
                message=_string_value(row.get("message")),
                warnings=_text_list(row.get("warnings")),
            )
            for row in _dict_list(payload.get("actions"))
        ]
        return _actions_refreshed_topic_news_for_review(actions, candidate, packet)
    return False


def _mark_topic_news_exhausted(candidate: CandidateCheck) -> CandidateCheck:
    selector = _research_selector(candidate.symbol, candidate.display_name)
    limit_arg = _research_limit_arg(candidate.command_limit)
    return replace(
        candidate,
        topic_news_exhausted=True,
        priority="P2 证据耗尽待复核",
        ranking_reason="主题新闻已刷新但没有可用相关新闻，避免重复刷新同一查询。",
        next_step=(
            "主题新闻已刷新但没有可用材料；先下钻核验证据板，"
            "处理待判定/反向证据，必要时更换数据源。"
        ),
        next_command=f"lychee research verify {selector}{limit_arg}",
    )


def _mark_topic_news_review_ready(candidate: CandidateCheck) -> CandidateCheck:
    selector = _research_selector(candidate.symbol, candidate.display_name)
    limit_arg = _research_limit_arg(candidate.command_limit)
    return replace(
        candidate,
        topic_news_review_ready=True,
        priority="P2 证据待分类",
        ranking_reason="主题新闻已刷新并形成相关新闻，下一步是下钻核验证据方向，不再重复刷新。",
        next_step=(
            "主题新闻已刷新；先下钻核验证据板，处理支持、反向和待判定证据。"
        ),
        next_command=f"lychee research verify {selector}{limit_arg}",
    )


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

    blocked = [
        candidate
        for candidate in candidates
        if candidate.status == "blocked" or candidate.data_gaps
    ]
    if blocked:
        gates.append(
            WorkbenchGate(
                "数据缺口",
                "fail",
                f"{len(blocked)} 个任务仍缺少数据；请查看下方阻塞任务的处理动作。",
            )
        )
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
    lines.append("现在先做")
    if candidates:
        first = candidates[0]
        lines.append(f"- {first.display_name}: {first.next_step}")
        lines.append(f"  为什么先做: {first.ranking_reason}")
        lines.append(f"  你要回答: {first.beginner_question}")
        if first.next_command:
            lines.append(f"  只需要执行: {first.next_command}")
    else:
        lines.append("- 先运行今日市场发现，建立第一批研究任务。")

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
            if candidate.next_command:
                lines.append(f"  执行命令: {candidate.next_command}")
            if candidate.proxy_symbols:
                lines.append(f"  {_proxy_followup_line()}")
    else:
        lines.append("- 无可执行任务。")

    lines.append("")
    lines.append("下一步队列")
    if ready:
        for candidate in ready:
            lines.append(f"- {candidate.display_name}: {candidate.next_step}")
            if candidate.next_command:
                lines.append(f"  执行: {candidate.next_command}")
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
            lines.append(f"  优先级: {candidate.priority}")
            lines.append(f"  排序理由: {candidate.ranking_reason}")
            lines.append(f"  研究问题: {candidate.beginner_question}")
            lines.append("  当前状态: 数据尚未齐备，暂不进入下钻研究。")
            lines.append(f"  处理动作: {candidate.next_step}")
            if candidate.next_command:
                lines.append(f"  处理命令: {candidate.next_command}")
    else:
        lines.append("- 无。")
    return "\n".join(lines)


def _proxy_followup_line() -> str:
    return "代理核验: 查看下钻核验证据中的成分/费用、可交易性和成交量；缺什么按待补证据处理。"


def _signal_reading(
    candidate: CandidateCheck,
    price: dict[str, object],
    evidence: list[dict[str, object]],
    related_news: list[dict[str, object]],
    filings: list[dict[str, object]],
    financials: list[dict[str, object]],
    research_metrics: list[dict[str, object]],
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
    if price and (evidence or related_news) and (research_metrics or financials):
        return "证据增强: 已有行情、消息和补充财务/研究指标，下一步检查它们是否同向。"
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
    prices: list[dict[str, object]],
    evidence: list[dict[str, object]],
    related_news: list[dict[str, object]],
    filings: list[dict[str, object]],
    financials: list[dict[str, object]],
    research_metrics: list[dict[str, object]],
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
        f"- 行情: {'已采集' if prices else '缺失'}",
        f"- Discovery 证据: {len(evidence)} 条",
        f"- 相关新闻: {len(related_news)} 条",
        f"- 公告/财报: {filing_status}",
        f"- 财务快照: {len(financials)} 条",
        f"- 研究指标: {len(research_metrics)} 条",
        f"- 代理标的: {proxy_status}",
        f"- 数据完整性: {_gap_summary(data_gaps)}",
        f"- 研究缺口: {_research_gap_summary(candidate, data_gaps)}",
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


def _price_lines(prices: list[dict[str, object]]) -> list[str]:
    if not prices:
        return [_price_line({})]
    return [_price_line(price) for price in prices[:3]]


def _research_metric_lines(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return ["- 暂无补充研究指标。"]
    return [f"- {_research_metric_line(row)}" for row in rows[:3]]


def _headline_lines(rows: list[dict[str, object]], *, empty: str) -> list[str]:
    if not rows:
        return [f"- {empty}"]
    lines: list[str] = []
    for row in rows[:3]:
        headline = _string_value(row.get("headline")) or "未命名证据"
        source_label = _news_source_label(row)
        source_url = _string_value(row.get("source_url"))
        if source_url:
            lines.append(f"- [{source_label}] {headline} ({source_url})")
        else:
            lines.append(f"- [{source_label}] {headline}")
    return lines


def _news_source_label(row: dict[str, object]) -> str:
    source_url = _string_value(row.get("source_url")).casefold()
    if "tencent.com" in source_url:
        return "公司官方"
    if "hkexnews.hk" in source_url:
        return "交易所公告"
    if "sec.gov" in source_url:
        return "监管披露"
    if "prnewswire.com" in source_url:
        return "公司新闻稿"
    return "外部来源"


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


def _financial_snapshot_lines(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return ["- 暂无 SEC XBRL 财务快照。"]
    return [f"- {_financial_snapshot_line(row)}" for row in rows[:3]]


def _forecast_lines(row: dict[str, object]) -> list[str]:
    if not row:
        return ["- 暂无 TimesFM 预测区间。"]
    symbol = _string_value(row.get("symbol")) or "当前标的"
    method = _string_value(row.get("method")) or "TimesFM"
    horizon = row.get("horizon_days")
    horizon_text = f"{horizon} 日" if isinstance(horizon, int) else "未知 horizon"
    lower = _forecast_number(row.get("lower"))
    midpoint = _forecast_number(row.get("midpoint"))
    upper = _forecast_number(row.get("upper"))
    if lower is None or midpoint is None or upper is None:
        return ["- 预测缓存字段不完整，不能显示。"]
    return [
        f"- {symbol} {method} / horizon {horizon_text}: "
        f"{lower:.2f} - {upper:.2f}；中位读数 {midpoint:.2f}",
        "- 边界：预测需用历史回测验证，不作为买卖信号。",
    ]


def _forecast_number(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _gap_summary(data_gaps: list[str]) -> str:
    if not data_gaps:
        return "无"
    return "；".join(gap.rstrip("。；;,.， ") for gap in data_gaps if gap.strip())


def _research_gap_summary(candidate: CandidateCheck, data_gaps: list[str]) -> str:
    if data_gaps:
        return "先处理数据完整性问题。"
    if candidate.topic_news_exhausted:
        return "主题新闻已刷新，但没有回答当前研究问题的材料。"
    if candidate.topic_news_review_ready:
        return "已有主题材料，等待区分支持、反向或待判定证据。"
    return "无"


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


def _research_selector(symbol: str | None, display_name: str) -> str:
    if symbol:
        return f"--symbol {symbol}"
    return f"--name {_quote_cli_value(display_name)}"


def _research_limit_arg(limit: int) -> str:
    if limit > DEFAULT_RESEARCH_SELECTION_LIMIT:
        return f" --limit {limit}"
    return ""


def _next_command(
    *,
    status: str,
    symbol: str | None,
    display_name: str,
    data_gaps: list[str],
    evidence_quality: CandidateEvidenceQuality,
    command_limit: int = DEFAULT_RESEARCH_SELECTION_LIMIT,
) -> str:
    selector = _research_selector(symbol, display_name)
    limit_arg = _research_limit_arg(command_limit)
    if status == "blocked" or data_gaps or evidence_quality.needs_review:
        return f"lychee research run {selector}{limit_arg} --force"
    return f"lychee research verify {selector}{limit_arg}"


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
        return _data_gap_action_summary(data_gaps)
    if evidence_quality.needs_review:
        return "先刷新主题新闻并重新下钻核验，确认现有证据是否只是市场噪音。"
    if next_actions:
        cleaned_actions = [
            action.rstrip("。；;,.， ")
            for action in next_actions[:2]
            if action.strip()
        ]
        return "；".join(cleaned_actions)
    return "先查看观察入口的最近行情、成交量、相关新闻和公开资料。"


def _data_gap_action_summary(data_gaps: list[str]) -> str:
    normalized = " ".join(gap.casefold() for gap in data_gaps if gap.strip())
    if "可直接拉取的证券代码" in normalized:
        return "先建立可观察的证券或指数入口，再重新核验。"
    if "代理标的行情" in normalized and "本地行情" not in normalized:
        return "先补齐代理标的行情，再重新核验。"

    labels: list[str] = []
    if "本地行情" in normalized:
        labels.append("行情")
    if "新闻" in normalized or "discovery" in normalized:
        labels.append("新闻")
    if "sec" in normalized or "公告" in normalized or "财报" in normalized:
        labels.append("公告/财报")
    if not labels:
        return "先补齐基础研究数据，再重新核验。"
    return f"先补齐{'、'.join(labels)}数据，再重新核验。"


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
    fill_result: ResearchGapFillResult,
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = _unique_artifact_path(research_dir, "workbench-check", created_at)
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
                "auto_fill": _auto_fill_payload(fill_result),
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


def _auto_fill_payload(result: ResearchGapFillResult) -> dict[str, object]:
    return {
        "candidates_checked": result.candidates_checked,
        "market_symbols": result.market_symbols,
        "news_symbols": result.news_symbols,
        "unresolved_news_symbols": result.unresolved_news_symbols,
        "filing_symbols": result.filing_symbols,
        "symbol_mapping_candidates": result.symbol_mapping_candidates,
        "actions": [
            {
                "action_type": action.action_type,
                "status": action.status,
                "symbols": action.symbols,
                "count": action.count,
                "output_path": str(action.output_path) if action.output_path else None,
                "warnings": action.warnings,
                "message": action.message,
            }
            for action in result.actions
        ],
    }


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


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0


def _safe_timestamp(value: str) -> str:
    normalized = value.replace("+00:00", "Z")
    return (
        normalized.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace(".", "")
        .replace("T", "-")
    )


def _unique_artifact_path(directory: Path, prefix: str, created_at: str) -> Path:
    timestamp = _safe_timestamp(created_at)
    output_path = directory / f"{prefix}-{timestamp}.json"
    if not output_path.exists():
        return output_path
    for index in range(1, 1000):
        candidate = directory / f"{prefix}-{timestamp}~{index:02d}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法为 {prefix}-{timestamp} 生成唯一审计文件名。")
