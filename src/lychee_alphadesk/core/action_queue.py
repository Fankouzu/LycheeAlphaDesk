from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
    run_workbench_check,
)

WorkbenchRunner = Callable[..., WorkbenchCheckResult]
PendingReader = Callable[..., list[PendingEvidenceReviewItem]]
DataRequestReader = Callable[..., list[ResearchDataRequest]]
ProviderBacklogReader = Callable[..., list[ProviderBacklogItem]]


@dataclass(frozen=True)
class ActionQueueItem:
    priority: int
    area: str
    title: str
    detail: str
    command: str
    source: str


def build_action_queue(
    output_dir: Path,
    *,
    limit: int = 12,
    workbench_runner: WorkbenchRunner = run_workbench_check,
    pending_reader: PendingReader = list_pending_evidence_reviews,
    data_request_reader: DataRequestReader = list_research_data_requests,
    provider_backlog_reader: ProviderBacklogReader = list_provider_backlog_items,
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
        priority=20,
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
