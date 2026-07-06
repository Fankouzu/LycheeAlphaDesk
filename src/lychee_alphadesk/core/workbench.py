import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
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
class ResearchVerificationResult:
    created_at: str
    status: str
    status_label: str
    candidate: CandidateCheck
    packet: ResearchPacket | None
    checks: list[ResearchVerificationCheck]
    evidence_board: dict[str, list[str]]
    conclusion: str
    next_actions: list[str]
    artifact_path: Path
    workbench_result: WorkbenchCheckResult


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
    checks.append(
        ResearchVerificationCheck(
            name="新闻核验",
            status="pass" if news_count else "fail",
            detail=f"可核验新闻/证据 {news_count} 条。"
            if news_count
            else "缺少 discovery 证据或相关新闻。",
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
    evidence = _dict_list(packet_payload.get("evidence"))
    related_news = _dict_list(local_data.get("related_news"))
    filings = _dict_list(local_data.get("filings"))
    support: list[str] = []
    risk: list[str] = []
    missing: list[str] = []
    if price:
        support.append(_price_line(price).removeprefix("行情: "))
    for row in [*evidence, *related_news][:3]:
        headline = _string_value(row.get("headline")) or "未命名新闻证据"
        support.append(f"新闻: {headline}")
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
        "研究结果",
        f"任务: {candidate.display_name} [{candidate.market}]",
        f"入口: {candidate.observation_entry}",
        f"优先级: {candidate.priority}",
        f"证据状态: {candidate.evidence_status}",
        "",
        "研究状态",
        f"- 阶段: {assessment.stage_label}",
        f"- 一致性: {assessment.consistency_label}",
        f"- 证据读数: {assessment.evidence_reading}",
        f"- 下一步判断: {assessment.next_decision}",
        "",
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
        checks.append(
            CandidateCheck(
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
                next_step=_next_step(next_actions, data_gaps),
                priority=_priority(
                    status=status,
                    symbol=packet.symbol,
                    proxy_symbols=proxy_symbols,
                    evidence_count=len(evidence_ids),
                    gap_count=len(data_gaps),
                ),
                evidence_status=_evidence_status(
                    evidence_count=len(evidence_ids),
                    gap_count=len(data_gaps),
                    proxy_symbols=proxy_symbols,
                ),
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
) -> str:
    if status == "blocked" or gap_count:
        return "P0 待补数据"
    if symbol and evidence_count >= 2:
        return "P1 直接下钻"
    if symbol:
        return "P2 待增强证据"
    if proxy_symbols:
        return "P2 代理核验"
    return "P3 待映射"


def _evidence_status(
    *,
    evidence_count: int,
    gap_count: int,
    proxy_symbols: list[str],
) -> str:
    parts = [f"证据 {evidence_count} 条", f"缺口 {gap_count} 个"]
    if proxy_symbols:
        parts.append("代理已映射")
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


def _next_step(next_actions: list[str], data_gaps: list[str]) -> str:
    if data_gaps:
        return f"下一步先补齐 {'；'.join(data_gaps)}。"
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
