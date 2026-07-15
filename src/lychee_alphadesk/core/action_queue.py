import json
import shlex
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lychee_alphadesk.core.live_data import PullResult, pull_news_events
from lychee_alphadesk.core.opportunity_radar import (
    OpportunityDrilldownTarget,
    OpportunityRadarReport,
    OpportunitySignal,
    build_opportunity_radar,
)
from lychee_alphadesk.core.research_db import (
    ResearchDataRequestFulfillmentRecord,
    list_research_data_request_fulfillments,
    write_opportunity_radar_candidate,
)
from lychee_alphadesk.core.research_requests import (
    ProviderBacklogItem,
    ResearchDataRequest,
    ResearchDataRequestDiagnostic,
    ResearchDataRequestFulfillment,
    describe_research_data_request_failure,
    diagnose_research_data_request,
    fulfill_research_data_request,
    list_provider_backlog_items,
    list_research_data_requests,
    research_data_request_needs_manual_source,
)
from lychee_alphadesk.core.workbench import (
    PendingEvidenceReviewItem,
    WorkbenchCheckResult,
    list_pending_evidence_reviews,
    record_research_evidence_review,
    run_research_task,
    run_workbench_check,
)

WorkbenchRunner = Callable[..., WorkbenchCheckResult]
PendingReader = Callable[..., list[PendingEvidenceReviewItem]]
DataRequestReader = Callable[..., list[ResearchDataRequest]]
DataRequestFulfillmentReader = Callable[..., list[ResearchDataRequestFulfillmentRecord]]
ProviderBacklogReader = Callable[..., list[ProviderBacklogItem]]
RadarReader = Callable[..., OpportunityRadarReport]
PullNews = Callable[..., PullResult]
RunResearch = Callable[..., Any]
RecordEvidenceReview = Callable[..., Any]
FulfillDataRequest = Callable[..., ResearchDataRequestFulfillment]
DiagnoseDataRequest = Callable[..., ResearchDataRequestDiagnostic]
RadarCandidateWriter = Callable[..., Path]
ActionQueueBuilder = Callable[..., list["ActionQueueItem"]]

ACTION_NO_DATA_COOLDOWN_SECONDS = 60 * 60
RADAR_RESEARCH_FOLLOWUP_SECONDS = 24 * 60 * 60
RADAR_FOLLOWUP_WORKBENCH_PRIORITY = 25
DEFAULT_WORKBENCH_PRIORITY = 40
RADAR_RESEARCH_ADVANCED_STATUSES = {"completed", "partial", "cached"}


@dataclass(frozen=True)
class ActionQueueItem:
    priority: int
    area: str
    title: str
    detail: str
    command: str
    source: str


@dataclass(frozen=True)
class ActionQueueExecution:
    item: ActionQueueItem
    status: str
    message: str
    count: int
    output_path: Path | None
    next_command: str
    warnings: list[str]


@dataclass(frozen=True)
class ActionQueueBatchExecution:
    executions: list[ActionQueueExecution]
    status: str
    stop_reason: str


@dataclass(frozen=True)
class ActionCooldown:
    command: str
    area: str
    title: str
    source: str
    status: str
    message: str
    output_path: str
    warnings: list[str]
    created_at: datetime
    expires_at: datetime
    ttl_seconds: int
    next_command: str


def build_action_queue(
    output_dir: Path,
    *,
    limit: int = 12,
    workbench_runner: WorkbenchRunner = run_workbench_check,
    pending_reader: PendingReader = list_pending_evidence_reviews,
    data_request_reader: DataRequestReader = list_research_data_requests,
    data_request_fulfillment_reader: DataRequestFulfillmentReader = (
        list_research_data_request_fulfillments
    ),
    provider_backlog_reader: ProviderBacklogReader = list_provider_backlog_items,
    radar_reader: RadarReader = build_opportunity_radar,
) -> list[ActionQueueItem]:
    items: list[ActionQueueItem] = []
    workbench = workbench_runner(output_dir=output_dir, limit=limit)

    for pending in pending_reader(output_dir=output_dir, limit=limit):
        items.append(_pending_evidence_action(pending))

    for backlog in provider_backlog_reader(output_dir=output_dir, limit=limit):
        action = _provider_backlog_action(backlog)
        if action is not None:
            items.append(action)

    failed_fulfillments = _latest_failed_data_request_fulfillments(
        data_request_fulfillment_reader(output_dir=output_dir, limit=500)
    )
    request_indexes: dict[tuple[str, str], int] = {}
    concrete_data_request_keys: set[tuple[str, str]] = set()
    for request in data_request_reader(output_dir=output_dir, limit=limit):
        request_key = _data_request_key(request)
        request_indexes[request_key] = request_indexes.get(request_key, 0) + 1
        action = _data_request_action(
            request_indexes[request_key],
            request,
            failure=failed_fulfillments.get(request.request_id),
        )
        if action is not None:
            concrete_data_request_keys.add(request_key)
            items.append(action)

    radar = radar_reader(output_dir=output_dir, limit=limit)
    radar_actions: list[ActionQueueItem] = []
    for signal in radar.signals:
        radar_actions.extend(_radar_drilldown_actions(signal))
    items.extend(_active_radar_actions(output_dir, radar_actions)[: min(3, limit)])

    for candidate in workbench.candidates:
        if candidate.next_command:
            if _candidate_has_concrete_data_request(
                candidate,
                concrete_data_request_keys,
            ):
                continue
            items.append(_workbench_candidate_action(output_dir, candidate))

    return _dedupe_actions(sorted(items, key=lambda item: item.priority))[:limit]


def execute_action_queue_batch(
    output_dir: Path,
    *,
    max_actions: int = 3,
    limit: int = 10,
    force: bool = True,
    queue_builder: ActionQueueBuilder = build_action_queue,
    pull_news: PullNews = pull_news_events,
    run_research: RunResearch = run_research_task,
    record_evidence_review: RecordEvidenceReview = record_research_evidence_review,
    fulfill_data_request: FulfillDataRequest = fulfill_research_data_request,
    diagnose_data_request: DiagnoseDataRequest = diagnose_research_data_request,
    radar_candidate_writer: RadarCandidateWriter = write_opportunity_radar_candidate,
) -> ActionQueueBatchExecution:
    if max_actions < 1:
        raise ValueError("批量推进数量必须大于 0。")

    executions: list[ActionQueueExecution] = []
    attempted_commands: set[str] = set()
    stop_reason = ""

    for _ in range(max_actions):
        queue = queue_builder(output_dir, limit=limit)
        if not queue:
            stop_reason = "行动队列已清空。"
            break

        item = queue[0]
        if item.command in attempted_commands:
            stop_reason = "队列首项没有变化，停止批量推进，避免重复执行同一动作。"
            break
        attempted_commands.add(item.command)
        queue_snapshot = list(queue)

        execution = execute_action_queue_item(
            output_dir,
            action_index=1,
            limit=limit,
            force=force,
            queue_builder=lambda *args, queue=queue_snapshot, **kwargs: queue,
            pull_news=pull_news,
            run_research=run_research,
            record_evidence_review=record_evidence_review,
            fulfill_data_request=fulfill_data_request,
            diagnose_data_request=diagnose_data_request,
            radar_candidate_writer=radar_candidate_writer,
        )
        executions.append(execution)
        if execution.status == "failed":
            stop_reason = "遇到 failed，停止批量推进，避免重复消耗数据源。"
            break
        if execution.status in {"manual_required", "skipped"}:
            stop_reason = f"遇到 {execution.status}，需要人工处理后再继续。"
            break
    else:
        stop_reason = f"已达到本次批量上限 {max_actions}。"

    return ActionQueueBatchExecution(
        executions=executions,
        status=_batch_status(executions),
        stop_reason=stop_reason,
    )


def execute_action_queue_item(
    output_dir: Path,
    *,
    action_index: int,
    limit: int = 10,
    force: bool = True,
    queue_builder: ActionQueueBuilder = build_action_queue,
    pull_news: PullNews = pull_news_events,
    run_research: RunResearch = run_research_task,
    record_evidence_review: RecordEvidenceReview = record_research_evidence_review,
    fulfill_data_request: FulfillDataRequest = fulfill_research_data_request,
    diagnose_data_request: DiagnoseDataRequest = diagnose_research_data_request,
    radar_candidate_writer: RadarCandidateWriter = write_opportunity_radar_candidate,
) -> ActionQueueExecution:
    queue = queue_builder(output_dir, limit=limit)
    if not queue:
        raise ValueError(
            "下一步行动队列为空。请先运行 `lychee discover today` 或 "
            "`lychee research check`。"
        )
    if action_index < 1 or action_index > len(queue):
        raise ValueError(f"行动序号超出范围。请输入 1 到 {len(queue)} 之间的序号。")

    item = queue[action_index - 1]
    news_payload = _parse_pull_news_command(item.command)
    if news_payload is not None:
        return _execute_pull_news_action(
            output_dir=output_dir,
            item=item,
            payload=news_payload,
            force=force,
            pull_news=pull_news,
        )

    research_payload = _parse_research_run_command(item.command)
    if research_payload is not None:
        return _execute_research_run_action(
            output_dir=output_dir,
            item=item,
            payload=research_payload,
            limit=limit,
            force=force,
            run_research=run_research,
            radar_candidate_writer=radar_candidate_writer,
        )

    evidence_review_payload = _parse_evidence_review_command(item.command)
    if evidence_review_payload is not None:
        return _execute_evidence_review_action(
            output_dir=output_dir,
            item=item,
            payload=evidence_review_payload,
            limit=limit,
            record_evidence_review=record_evidence_review,
        )

    data_request_payload = _parse_run_data_request_command(item.command)
    if data_request_payload is not None:
        return _execute_research_data_request_action(
            output_dir=output_dir,
            item=item,
            payload=data_request_payload,
            limit=limit,
            force=force,
            fulfill_data_request=fulfill_data_request,
        )

    diagnostic_payload = _parse_data_request_diagnose_command(item.command)
    if diagnostic_payload is not None:
        return _execute_data_request_diagnostic_action(
            output_dir=output_dir,
            item=item,
            payload=diagnostic_payload,
            limit=limit,
            diagnose_data_request=diagnose_data_request,
        )

    raise ValueError(
        f"这条行动暂不支持自动执行: [{item.area}] {item.title}。"
        f"请复制命令手动执行: {item.command}"
    )


def _execute_pull_news_action(
    *,
    output_dir: Path,
    item: ActionQueueItem,
    payload: dict[str, object],
    force: bool,
    pull_news: PullNews,
) -> ActionQueueExecution:
    try:
        result = pull_news(
            symbols=payload["symbols"],
            query=payload["query"],
            output_dir=output_dir,
            provider_id="auto",
            force=force,
        )
    except (RuntimeError, ValueError) as error:
        return ActionQueueExecution(
            item=item,
            status="failed",
            message=str(error),
            count=0,
            output_path=None,
            next_command="",
            warnings=[],
        )
    if result.count == 0:
        _record_action_cooldown(
            output_dir=output_dir,
            item=item,
            status="no-data",
            message="没有获取到匹配新闻，暂不能进入研究核验。",
            output_path=result.output_path,
            warnings=result.warnings,
        )
        return ActionQueueExecution(
            item=item,
            status="no-data",
            message="没有获取到匹配新闻，暂不能进入研究核验。",
            count=0,
            output_path=result.output_path,
            next_command="",
            warnings=result.warnings,
        )
    status = "completed" if result.refreshed else "cached"
    next_command = _next_research_command(payload["symbols"])
    _record_action_cooldown(
        output_dir=output_dir,
        item=item,
        status=status,
        message="已执行机会雷达补新闻动作。" if result.refreshed else "已使用未过期新闻缓存。",
        output_path=result.output_path,
        warnings=result.warnings,
        next_command=next_command,
    )
    return ActionQueueExecution(
        item=item,
        status=status,
        message="已执行机会雷达补新闻动作。" if result.refreshed else "已使用未过期新闻缓存。",
        count=result.count,
        output_path=result.output_path,
        next_command=next_command,
        warnings=result.warnings,
    )


def _execute_research_run_action(
    *,
    output_dir: Path,
    item: ActionQueueItem,
    payload: dict[str, str | None],
    limit: int,
    force: bool,
    run_research: RunResearch,
    radar_candidate_writer: RadarCandidateWriter,
) -> ActionQueueExecution:
    if item.source == "opportunity-radar":
        display_name, related_theme = _radar_title_parts(item.title, payload)
        symbol = payload["symbol"] or ""
        radar_candidate_writer(
            output_dir=output_dir,
            display_name=display_name,
            symbol=symbol,
            market=_infer_symbol_market(symbol),
            related_theme=related_theme,
            why_watch=item.detail,
            next_actions=[item.command],
        )
    try:
        result = run_research(
            output_dir=output_dir,
            symbol=payload["symbol"],
            name=payload["name"],
            limit=limit,
            force=force,
        )
    except (RuntimeError, ValueError) as error:
        return ActionQueueExecution(
            item=item,
            status="failed",
            message=str(error),
            count=0,
            output_path=None,
            next_command="",
            warnings=[],
        )
    output_path = getattr(result, "artifact_path", None)
    actions = getattr(result, "actions", [])
    status = str(getattr(result, "status", "completed"))
    if item.source == "opportunity-radar":
        _record_action_cooldown(
            output_dir=output_dir,
            item=item,
            status=status,
            message="已执行机会雷达研究链。",
            output_path=output_path if isinstance(output_path, Path) else None,
            warnings=[],
        )
    return ActionQueueExecution(
        item=item,
        status=status,
        message="已执行研究任务刷新链。",
        count=len(actions),
        output_path=output_path if isinstance(output_path, Path) else None,
        next_command=_next_verify_command(payload["symbol"], payload["name"]),
        warnings=[],
    )


def _execute_evidence_review_action(
    *,
    output_dir: Path,
    item: ActionQueueItem,
    payload: dict[str, str | None],
    limit: int,
    record_evidence_review: RecordEvidenceReview,
) -> ActionQueueExecution:
    try:
        result = record_evidence_review(
            output_dir=output_dir,
            symbol=payload["symbol"],
            name=payload["name"],
            evidence_text=payload["evidence_text"] or "",
            verdict=payload["verdict"] or "",
            note=payload["note"] or "",
            limit=limit,
        )
    except (RuntimeError, ValueError) as error:
        return ActionQueueExecution(
            item=item,
            status="failed",
            message=str(error),
            count=0,
            output_path=None,
            next_command="",
            warnings=[],
        )
    output_path = getattr(result, "artifact_path", None)
    verdict_label = str(getattr(result, "verdict_label", payload["verdict"] or ""))
    return ActionQueueExecution(
        item=item,
        status="completed",
        message=f"已记录证据复核: {verdict_label}。",
        count=1,
        output_path=output_path if isinstance(output_path, Path) else None,
        next_command=_next_verify_command(payload["symbol"], payload["name"]),
        warnings=[],
    )


def _execute_research_data_request_action(
    *,
    output_dir: Path,
    item: ActionQueueItem,
    payload: dict[str, str | int | None],
    limit: int,
    force: bool,
    fulfill_data_request: FulfillDataRequest,
) -> ActionQueueExecution:
    try:
        result = fulfill_data_request(
            output_dir,
            request_index=payload["request_index"],
            symbol=payload["symbol"],
            name=payload["name"],
            limit=limit,
            force=force,
        )
    except (RuntimeError, ValueError) as error:
        return ActionQueueExecution(
            item=item,
            status="failed",
            message=str(error),
            count=0,
            output_path=None,
            next_command="",
            warnings=[],
        )
    status = _data_request_execution_status(result)
    return ActionQueueExecution(
        item=item,
        status=status,
        message=_data_request_execution_message(result),
        count=sum(execution.count for execution in result.executions),
        output_path=result.artifact_path or _latest_execution_output_path(result),
        next_command=_data_request_next_command(result, status),
        warnings=[
            warning
            for execution in result.executions
            for warning in execution.warnings
        ],
    )


def _execute_data_request_diagnostic_action(
    *,
    output_dir: Path,
    item: ActionQueueItem,
    payload: dict[str, str | int | None],
    limit: int,
    diagnose_data_request: DiagnoseDataRequest,
) -> ActionQueueExecution:
    try:
        diagnostic = diagnose_data_request(
            output_dir,
            request_index=payload["request_index"],
            symbol=payload["symbol"],
            name=payload["name"],
            limit=limit,
        )
    except (RuntimeError, ValueError) as error:
        return ActionQueueExecution(
            item=item,
            status="failed",
            message=str(error),
            count=0,
            output_path=None,
            next_command="",
            warnings=[],
        )
    return ActionQueueExecution(
        item=item,
        status="manual_required",
        message=(
            f"数据源诊断: {diagnostic.summary} "
            "请先完成恢复步骤，再手动确认重试。"
        ),
        count=len(diagnostic.failed_actions),
        output_path=diagnostic.failure_path,
        next_command=diagnostic.retry_command,
        warnings=diagnostic.recovery_steps,
    )


def _pending_evidence_action(item: PendingEvidenceReviewItem) -> ActionQueueItem:
    return ActionQueueItem(
        priority=10,
        area="待判定证据",
        title=f"复核 {item.display_name} 的待判定证据",
        detail=f"{item.primary_question} | 系统建议: {item.suggested_verdict_label}",
        command=item.review_command,
        source=item.artifact_path,
    )


def _workbench_candidate_action(output_dir: Path, candidate: Any) -> ActionQueueItem:
    recent_radar = _recent_radar_research_cooldown(output_dir, candidate)
    if recent_radar is not None:
        return ActionQueueItem(
            priority=RADAR_FOLLOWUP_WORKBENCH_PRIORITY,
            area="雷达跟进",
            title=f"继续研究: {candidate.display_name}",
            detail=(
                "机会雷达刚推进过这个候选。 "
                f"{_workbench_action_summary(candidate)}"
            ).strip(),
            command=candidate.next_command,
            source="workbench:opportunity-radar",
        )
    return ActionQueueItem(
        priority=DEFAULT_WORKBENCH_PRIORITY,
        area="研究任务",
        title=f"推进研究: {candidate.display_name}",
        detail=_workbench_action_summary(candidate),
        command=candidate.next_command,
        source="workbench",
    )


def _workbench_action_summary(candidate: Any) -> str:
    next_step = str(getattr(candidate, "next_step", "")).strip()
    ranking_reason = str(getattr(candidate, "ranking_reason", "")).strip()
    question = str(getattr(candidate, "beginner_question", "")).strip()
    gap_count = getattr(candidate, "gap_count", 0)
    status = ""
    if isinstance(gap_count, int) and gap_count > 0:
        status = f"当前状态: 需要补齐 {gap_count} 项基础数据。"
    elif str(getattr(candidate, "evidence_quality", "")).strip() in {
        "missing",
        "needs_review",
        "mixed",
    }:
        status = "当前状态: 证据尚待复核。"
    else:
        status = "当前状态: 可进入下钻核验。"
    parts = [
        f"当前动作: {next_step}" if next_step else "当前动作: 进入下钻核验。",
        status,
        f"研究问题: {question}" if question else "",
        f"排序原因: {ranking_reason}" if ranking_reason else "",
    ]
    return " ".join(part for part in parts if part)


def _provider_backlog_action(item: ProviderBacklogItem) -> ActionQueueItem | None:
    command = item.suggested_commands[0] if item.suggested_commands else ""
    if not command:
        return None
    return ActionQueueItem(
        priority=45,
        area="数据源缺口",
        title=f"补充 {item.display_name} 的{item.data_domain}",
        detail=f"{item.coverage_gap} 下一步: {item.next_step}",
        command=command,
        source=_research_request_source(item.memo_path, item.verification_path),
    )


def _data_request_action(
    index: int,
    item: ResearchDataRequest,
    *,
    failure: ResearchDataRequestFulfillmentRecord | None = None,
) -> ActionQueueItem | None:
    if research_data_request_needs_manual_source(item):
        return None
    command = f"lychee research run-data-request --request {index} {_research_selector(item)}"
    if failure is not None:
        diagnostic = describe_research_data_request_failure(failure)
        detail = (
            f"上次补数据失败: {diagnostic} "
            "先查看本地诊断并修复后再重试；失败不会推进研究结论。"
        )
        return ActionQueueItem(
            priority=18,
            area="数据源诊断",
            title=f"修复数据源后重试: {item.display_name}",
            detail=detail,
            command=(
                "lychee research data-request-diagnose "
                f"--request {index} {_research_selector(item)}"
            ),
            source=failure.fulfillment_path,
        )
    return ActionQueueItem(
        priority=30,
        area="研究数据请求",
        title=f"{_data_request_action_label(item)}: {item.display_name}",
        detail=item.request_text,
        command=command,
        source=_research_request_source(item.memo_path, item.verification_path),
    )


def _radar_drilldown_actions(signal: OpportunitySignal) -> list[ActionQueueItem]:
    actions: list[ActionQueueItem] = []
    for target in signal.drilldown_targets:
        command = _radar_target_command(target)
        if not command:
            continue
        actions.append(
            ActionQueueItem(
                priority=20,
                area="机会雷达",
                title=f"下钻 {target.display_name}: {signal.theme}",
                detail=(
                    f"来自 {signal.symbol} 雷达信号；{target.reason} "
                    f"证据缺口: {target.evidence_gap}"
                ),
                command=command,
                source="opportunity-radar",
            )
        )
    return actions


def _radar_target_command(target: OpportunityDrilldownTarget) -> str:
    if not target.next_steps:
        return ""
    if "缺少" in target.evidence_gap:
        return target.next_steps[0]
    return target.next_steps[-1]


def _parse_pull_news_command(command: str) -> dict[str, object] | None:
    parts = shlex.split(command)
    if parts[:4] != ["lychee", "data", "pull", "news"]:
        return None
    symbols = _option_value(parts, "--symbols")
    query = _option_value(parts, "--query")
    if not symbols or not query:
        return None
    return {
        "symbols": [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()],
        "query": query,
    }


def _parse_research_run_command(command: str) -> dict[str, str | None] | None:
    parts = shlex.split(command)
    if parts[:3] != ["lychee", "research", "run"]:
        return None
    symbol = _option_value(parts, "--symbol")
    name = _option_value(parts, "--name")
    if not symbol and not name:
        return None
    return {"symbol": symbol.upper() if symbol else None, "name": name or None}


def _parse_evidence_review_command(command: str) -> dict[str, str | None] | None:
    parts = shlex.split(command)
    if parts[:3] != ["lychee", "research", "evidence-review"]:
        return None
    symbol = _option_value(parts, "--symbol")
    name = _option_value(parts, "--name")
    evidence_text = _option_value(parts, "--text") or _option_value(parts, "--evidence")
    verdict = _option_value(parts, "--verdict")
    if (not symbol and not name) or not evidence_text or not verdict:
        return None
    return {
        "symbol": symbol.upper() if symbol else None,
        "name": name or None,
        "evidence_text": evidence_text,
        "verdict": verdict,
        "note": _option_value(parts, "--note") or None,
    }


def _parse_run_data_request_command(
    command: str,
) -> dict[str, str | int | None] | None:
    parts = shlex.split(command)
    if parts[:3] != ["lychee", "research", "run-data-request"]:
        return None
    raw_request_index = _option_value(parts, "--request") or "1"
    try:
        request_index = int(raw_request_index)
    except ValueError:
        return None
    symbol = _option_value(parts, "--symbol")
    name = _option_value(parts, "--name")
    if not symbol and not name:
        return None
    return {
        "request_index": request_index,
        "symbol": symbol.upper() if symbol else None,
        "name": name or None,
    }


def _parse_data_request_diagnose_command(
    command: str,
) -> dict[str, str | int | None] | None:
    parts = shlex.split(command)
    if parts[:3] != ["lychee", "research", "data-request-diagnose"]:
        return None
    raw_request_index = _option_value(parts, "--request") or "1"
    try:
        request_index = int(raw_request_index)
    except ValueError:
        return None
    symbol = _option_value(parts, "--symbol")
    name = _option_value(parts, "--name")
    if not symbol and not name:
        return None
    return {
        "request_index": request_index,
        "symbol": symbol.upper() if symbol else None,
        "name": name or None,
    }


def _option_value(parts: list[str], option: str) -> str:
    try:
        index = parts.index(option)
    except ValueError:
        return ""
    value_index = index + 1
    if value_index >= len(parts):
        return ""
    return parts[value_index]


def _next_research_command(symbols: object) -> str:
    if not isinstance(symbols, list) or not symbols:
        return ""
    first_symbol = str(symbols[0])
    return f"lychee research run --symbol {first_symbol} --force"


def _next_verify_command(symbol: str | None, name: str | None) -> str:
    if symbol:
        return f"lychee research verify --symbol {symbol}"
    if name:
        return f'lychee research verify --name "{name}"'
    return ""


def _batch_status(executions: list[ActionQueueExecution]) -> str:
    if not executions:
        return "empty"
    statuses = [execution.status for execution in executions]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "manual_required" for status in statuses):
        return "manual_required"
    if any(status == "completed" for status in statuses):
        return "completed"
    if any(status == "partial" for status in statuses):
        return "partial"
    if any(status == "cached" for status in statuses):
        return "cached"
    if any(status == "no-data" for status in statuses):
        return "no-data"
    return statuses[-1]


def _data_request_execution_status(result: ResearchDataRequestFulfillment) -> str:
    if result.status:
        return result.status
    statuses = [execution.status for execution in result.executions]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "completed" for status in statuses):
        return "completed"
    if any(status == "cached" for status in statuses):
        return "cached"
    if any(status == "no-data" for status in statuses):
        return "no-data"
    if any(status == "manual_required" for status in statuses):
        return "manual_required"
    return "skipped"


def _data_request_execution_message(result: ResearchDataRequestFulfillment) -> str:
    messages = [
        execution.message
        for execution in result.executions
        if execution.message
        and execution.status not in {"skipped"}
    ]
    if not messages:
        messages = [execution.message for execution in result.executions if execution.message]
    if not messages:
        return "已执行研究数据请求。"
    return "已执行研究数据请求: " + "；".join(messages)


def _latest_execution_output_path(
    result: ResearchDataRequestFulfillment,
) -> Path | None:
    for execution in reversed(result.executions):
        if execution.output_path is not None:
            return execution.output_path
    return None


def _data_request_next_command(
    result: ResearchDataRequestFulfillment,
    status: str,
) -> str:
    if status in {"failed", "no-data", "manual_required", "skipped"}:
        return ""
    for execution in result.executions:
        if execution.action_type == "verify" and execution.status == "completed":
            return ""
    return _next_verify_command(
        result.request.symbol,
        None if result.request.symbol else result.request.display_name,
    )


def _radar_title_parts(
    title: str,
    payload: dict[str, str | None],
) -> tuple[str, str]:
    if title.startswith("下钻 ") and ":" in title:
        display_name, related_theme = title.removeprefix("下钻 ").split(":", 1)
        return display_name.strip(), related_theme.strip()
    fallback = payload["symbol"] or payload["name"] or "机会雷达候选"
    return fallback, "机会雷达下钻"


def _infer_symbol_market(symbol: str) -> str:
    if symbol.endswith(".HK"):
        return "HK"
    if symbol.endswith((".SH", ".SZ")):
        return "CN"
    return "US"


def _research_selector(item: ResearchDataRequest) -> str:
    if item.symbol:
        return f"--symbol {item.symbol}"
    return f'--name "{item.display_name}"'


def _research_request_source(memo_path: str, verification_path: str) -> str:
    return memo_path or verification_path


def _latest_failed_data_request_fulfillments(
    records: list[ResearchDataRequestFulfillmentRecord],
) -> dict[str, ResearchDataRequestFulfillmentRecord]:
    failed: dict[str, ResearchDataRequestFulfillmentRecord] = {}
    for record in records:
        if record.status != "failed" or record.request_id in failed:
            continue
        failed[record.request_id] = record
    return failed


def _data_request_key(item: ResearchDataRequest) -> tuple[str, str]:
    if item.symbol:
        return ("symbol", item.symbol.upper())
    return ("name", item.display_name.casefold())


def _candidate_has_concrete_data_request(
    candidate: Any,
    data_request_keys: set[tuple[str, str]],
) -> bool:
    symbol = getattr(candidate, "symbol", None)
    if isinstance(symbol, str) and symbol:
        return ("symbol", symbol.upper()) in data_request_keys
    display_name = getattr(candidate, "display_name", "")
    if isinstance(display_name, str) and display_name:
        return ("name", display_name.casefold()) in data_request_keys
    return False


def _data_request_action_label(item: ResearchDataRequest) -> str:
    action_types = {
        action.action_type
        for action in item.suggested_actions
        if action.action_type not in {"verify", "fund_metadata_import"}
    }
    if "fund_metadata_guide" in action_types:
        return "补基金资料"
    if "filings" in action_types:
        return "补公告财报"
    if action_types == {"market", "news"}:
        return "补行情和新闻"
    if "market" in action_types:
        return "补行情数据"
    if "news" in action_types:
        return "补新闻资料"
    return "执行补数据请求"


def _dedupe_actions(items: list[ActionQueueItem]) -> list[ActionQueueItem]:
    deduped: list[ActionQueueItem] = []
    seen_commands: set[str] = set()
    for item in items:
        if item.command in seen_commands:
            continue
        seen_commands.add(item.command)
        deduped.append(item)
    return deduped


def _active_radar_actions(
    output_dir: Path,
    radar_actions: list[ActionQueueItem],
) -> list[ActionQueueItem]:
    active: list[ActionQueueItem] = []
    for action in radar_actions:
        cooldown = _fresh_action_cooldown(output_dir, action.command)
        if cooldown is None:
            active.append(action)
            continue
        if cooldown.status in {"completed", "cached"} and cooldown.next_command:
            if _fresh_action_cooldown(output_dir, cooldown.next_command) is None:
                active.append(_radar_followup_action(action, cooldown))
    return active


def _recent_radar_research_cooldown(
    output_dir: Path,
    candidate: Any,
) -> ActionCooldown | None:
    symbol = str(getattr(candidate, "symbol", "") or "").strip().upper()
    if not symbol:
        return None
    cooldown = _get_action_cooldown(
        output_dir,
        f"lychee research run --symbol {symbol} --force",
    )
    if cooldown is None:
        return None
    if cooldown.source != "opportunity-radar":
        return None
    if cooldown.status not in RADAR_RESEARCH_ADVANCED_STATUSES:
        return None
    if datetime.now(UTC) - cooldown.created_at > timedelta(
        seconds=RADAR_RESEARCH_FOLLOWUP_SECONDS
    ):
        return None
    return cooldown


def _action_is_in_cooldown(output_dir: Path, item: ActionQueueItem) -> bool:
    if item.source != "opportunity-radar":
        return False
    cooldown = _fresh_action_cooldown(output_dir, item.command)
    if cooldown is None:
        return False
    return cooldown.status in {"no-data", "completed", "cached"}


def _fresh_action_cooldown(output_dir: Path, command: str) -> ActionCooldown | None:
    cooldown = _get_action_cooldown(output_dir, command)
    if cooldown is None:
        return None
    if datetime.now(UTC) >= cooldown.expires_at:
        return None
    return cooldown


def _radar_followup_action(
    action: ActionQueueItem,
    cooldown: ActionCooldown,
) -> ActionQueueItem:
    return ActionQueueItem(
        priority=action.priority,
        area=action.area,
        title=_radar_followup_title(action.title),
        detail=(
            f"{cooldown.message} 下一步进入研究链；"
            f"原始原因: {action.detail}"
        ),
        command=cooldown.next_command,
        source="opportunity-radar",
    )


def _radar_followup_title(title: str) -> str:
    if title.startswith("下钻 "):
        return "继续研究 " + title.removeprefix("下钻 ")
    return "继续研究 " + title


def _record_action_cooldown(
    *,
    output_dir: Path,
    item: ActionQueueItem,
    status: str,
    message: str,
    output_path: Path | None,
    warnings: list[str],
    next_command: str = "",
    now: datetime | None = None,
) -> None:
    current = _ensure_aware(now or datetime.now(UTC))
    expires_at = current + timedelta(seconds=ACTION_NO_DATA_COOLDOWN_SECONDS)
    _init_action_queue_db(output_dir)
    with sqlite3.connect(_action_queue_db_path(output_dir)) as connection:
        connection.execute(
            """
            INSERT INTO action_queue_cooldowns (
                command,
                area,
                title,
                source,
                status,
                message,
                output_path,
                warnings_json,
                created_at,
                expires_at,
                ttl_seconds,
                next_command
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(command) DO UPDATE SET
                area = excluded.area,
                title = excluded.title,
                source = excluded.source,
                status = excluded.status,
                message = excluded.message,
                output_path = excluded.output_path,
                warnings_json = excluded.warnings_json,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at,
                ttl_seconds = excluded.ttl_seconds,
                next_command = excluded.next_command
            """,
            (
                item.command,
                item.area,
                item.title,
                item.source,
                status,
                message,
                str(output_path) if output_path else "",
                _warnings_json(warnings),
                current.isoformat(timespec="seconds"),
                expires_at.isoformat(timespec="seconds"),
                ACTION_NO_DATA_COOLDOWN_SECONDS,
                next_command,
            ),
        )


def _get_action_cooldown(
    output_dir: Path,
    command: str,
) -> ActionCooldown | None:
    db_path = _action_queue_db_path(output_dir)
    if not db_path.exists():
        return None
    _init_action_queue_db(output_dir)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                command,
                area,
                title,
                source,
                status,
                message,
                output_path,
                warnings_json,
                created_at,
                expires_at,
                ttl_seconds,
                next_command
            FROM action_queue_cooldowns
            WHERE command = ?
            """,
            (command,),
        ).fetchone()
    if row is None:
        return None
    return ActionCooldown(
        command=str(row[0]),
        area=str(row[1]),
        title=str(row[2]),
        source=str(row[3]),
        status=str(row[4]),
        message=str(row[5]),
        output_path=str(row[6]),
        warnings=_parse_warnings(str(row[7])),
        created_at=_parse_datetime(str(row[8])),
        expires_at=_parse_datetime(str(row[9])),
        ttl_seconds=int(str(row[10])),
        next_command=str(row[11] or ""),
    )


def _init_action_queue_db(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_action_queue_db_path(output_dir)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS action_queue_cooldowns (
                command TEXT PRIMARY KEY,
                area TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                output_path TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                ttl_seconds INTEGER NOT NULL,
                next_command TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(action_queue_cooldowns)")
        }
        if "next_command" not in columns:
            connection.execute(
                """
                ALTER TABLE action_queue_cooldowns
                ADD COLUMN next_command TEXT NOT NULL DEFAULT ''
                """
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_action_queue_cooldowns_next_command
            ON action_queue_cooldowns(next_command)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_action_queue_cooldowns_expires
            ON action_queue_cooldowns(expires_at)
            """
        )


def _action_queue_db_path(output_dir: Path) -> Path:
    return output_dir / "research.sqlite3"


def _warnings_json(warnings: list[str]) -> str:
    return json.dumps(warnings, ensure_ascii=False)


def _parse_warnings(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _ensure_aware(parsed)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
