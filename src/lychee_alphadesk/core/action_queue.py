import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lychee_alphadesk.core.live_data import PullResult, pull_news_events
from lychee_alphadesk.core.opportunity_radar import (
    OpportunityDrilldownTarget,
    OpportunityRadarReport,
    OpportunitySignal,
    build_opportunity_radar,
)
from lychee_alphadesk.core.research_db import write_opportunity_radar_candidate
from lychee_alphadesk.core.research_requests import (
    ProviderBacklogItem,
    ResearchDataRequest,
    list_provider_backlog_items,
    list_research_data_requests,
    research_data_request_needs_manual_source,
)
from lychee_alphadesk.core.workbench import (
    PendingEvidenceReviewItem,
    WorkbenchCheckResult,
    list_pending_evidence_reviews,
    run_research_task,
    run_workbench_check,
)

WorkbenchRunner = Callable[..., WorkbenchCheckResult]
PendingReader = Callable[..., list[PendingEvidenceReviewItem]]
DataRequestReader = Callable[..., list[ResearchDataRequest]]
ProviderBacklogReader = Callable[..., list[ProviderBacklogItem]]
RadarReader = Callable[..., OpportunityRadarReport]
PullNews = Callable[..., PullResult]
RunResearch = Callable[..., Any]
RadarCandidateWriter = Callable[..., Path]
ActionQueueBuilder = Callable[..., list["ActionQueueItem"]]


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


def build_action_queue(
    output_dir: Path,
    *,
    limit: int = 12,
    workbench_runner: WorkbenchRunner = run_workbench_check,
    pending_reader: PendingReader = list_pending_evidence_reviews,
    data_request_reader: DataRequestReader = list_research_data_requests,
    provider_backlog_reader: ProviderBacklogReader = list_provider_backlog_items,
    radar_reader: RadarReader = build_opportunity_radar,
) -> list[ActionQueueItem]:
    items: list[ActionQueueItem] = []
    workbench = workbench_runner(output_dir=output_dir)

    for pending in pending_reader(output_dir=output_dir, limit=limit):
        items.append(_pending_evidence_action(pending))

    for backlog in provider_backlog_reader(output_dir=output_dir, limit=limit):
        action = _provider_backlog_action(backlog)
        if action is not None:
            items.append(action)

    request_indexes: dict[tuple[str, str], int] = {}
    for request in data_request_reader(output_dir=output_dir, limit=limit):
        request_key = _data_request_key(request)
        request_indexes[request_key] = request_indexes.get(request_key, 0) + 1
        action = _data_request_action(request_indexes[request_key], request)
        if action is not None:
            items.append(action)

    radar = radar_reader(output_dir=output_dir, limit=limit)
    radar_actions: list[ActionQueueItem] = []
    for signal in radar.signals:
        radar_actions.extend(_radar_drilldown_actions(signal))
    items.extend(radar_actions[: min(3, limit)])

    for candidate in workbench.candidates:
        if candidate.next_command:
            items.append(
                ActionQueueItem(
                    priority=40,
                    area="研究任务",
                    title=f"{candidate.display_name}: {candidate.next_step}",
                    detail=(
                        f"{candidate.ranking_reason or candidate.evidence_status} "
                        f"研究问题: {candidate.beginner_question}"
                    ).strip(),
                    command=candidate.next_command,
                    source="workbench",
                )
            )

    return _dedupe_actions(sorted(items, key=lambda item: item.priority))[:limit]


def execute_action_queue_item(
    output_dir: Path,
    *,
    action_index: int,
    limit: int = 10,
    force: bool = True,
    queue_builder: ActionQueueBuilder = build_action_queue,
    pull_news: PullNews = pull_news_events,
    run_research: RunResearch = run_research_task,
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
        return ActionQueueExecution(
            item=item,
            status="no-data",
            message="没有获取到匹配新闻，暂不能进入研究核验。",
            count=0,
            output_path=result.output_path,
            next_command="",
            warnings=result.warnings,
        )
    return ActionQueueExecution(
        item=item,
        status="completed" if result.refreshed else "cached",
        message="已执行机会雷达补新闻动作。" if result.refreshed else "已使用未过期新闻缓存。",
        count=result.count,
        output_path=result.output_path,
        next_command=_next_research_command(payload["symbols"]),
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
    return ActionQueueExecution(
        item=item,
        status=str(getattr(result, "status", "completed")),
        message="已执行研究任务刷新链。",
        count=len(actions),
        output_path=output_path if isinstance(output_path, Path) else None,
        next_command=_next_verify_command(payload["symbol"], payload["name"]),
        warnings=[],
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
        source=item.memo_path,
    )


def _data_request_action(index: int, item: ResearchDataRequest) -> ActionQueueItem | None:
    if research_data_request_needs_manual_source(item):
        return None
    return ActionQueueItem(
        priority=30,
        area="研究数据请求",
        title=f"执行 {item.display_name} 的补数据请求",
        detail=item.request_text,
        command=f"lychee research run-data-request --request {index} {_research_selector(item)}",
        source=item.memo_path,
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


def _data_request_key(item: ResearchDataRequest) -> tuple[str, str]:
    if item.symbol:
        return ("symbol", item.symbol.upper())
    return ("name", item.display_name.casefold())


def _dedupe_actions(items: list[ActionQueueItem]) -> list[ActionQueueItem]:
    deduped: list[ActionQueueItem] = []
    seen_commands: set[str] = set()
    for item in items:
        if item.command in seen_commands:
            continue
        seen_commands.add(item.command)
        deduped.append(item)
    return deduped
