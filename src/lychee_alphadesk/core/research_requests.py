import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.live_data import (
    FundMetadataGuide,
    PullResult,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    write_fund_metadata_guide,
)
from lychee_alphadesk.core.research_db import (
    ResearchMemoRecord,
    list_research_data_request_fulfillments,
    list_research_memos,
    write_research_data_request_fulfillment_record,
)
from lychee_alphadesk.core.workbench import verify_research_task

PullMarket = Callable[..., PullResult]
PullNews = Callable[..., PullResult]
PullFilings = Callable[..., PullResult]
WriteFundGuide = Callable[..., FundMetadataGuide]
VerifyTask = Callable[..., object]


@dataclass(frozen=True)
class ResearchDataRequestAction:
    action_type: str
    command: str
    auto_executable: bool = True


@dataclass(frozen=True)
class ResearchDataRequest:
    request_id: str
    created_at: str
    display_name: str
    symbol: str | None
    market: str
    confidence: str
    request_text: str
    suggested_commands: list[str]
    memo_path: str
    verification_path: str
    suggested_actions: list[ResearchDataRequestAction] = field(default_factory=list)
    source_type: str = "memo"


@dataclass(frozen=True)
class ProviderBacklogItem:
    request_id: str
    created_at: str
    display_name: str
    symbol: str | None
    market: str
    confidence: str
    request_text: str
    data_domain: str
    plugin_type: str
    coverage_gap: str
    suggested_provider_examples: list[str]
    suggested_commands: list[str]
    next_step: str
    memo_path: str
    verification_path: str


@dataclass(frozen=True)
class ResearchDataRequestExecution:
    action_type: str
    status: str
    command: str
    count: int
    output_path: Path | None
    message: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchDataRequestFulfillment:
    request: ResearchDataRequest
    executions: list[ResearchDataRequestExecution]
    status: str = ""
    artifact_path: Path | None = None


def list_research_data_requests(
    output_dir: Path,
    *,
    symbol: str | None = None,
    name: str | None = None,
    limit: int = 20,
    latest_per_task: bool = True,
) -> list[ResearchDataRequest]:
    records = list_research_memos(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    requests: list[ResearchDataRequest] = []
    seen_tasks: set[tuple[str, str, str]] = set()
    handled_request_ids = _handled_data_request_ids(output_dir)
    for record in records:
        task_key = _memo_task_key(record)
        if latest_per_task and task_key in seen_tasks:
            continue
        seen_tasks.add(task_key)
        request_texts = _memo_data_requests(record)
        for index, request_text in enumerate(request_texts, start=1):
            request_id = f"{record.memo_id}:data-request:{index}"
            if request_id in handled_request_ids:
                continue
            suggested_actions = _suggest_data_request_actions(record, request_text)
            requests.append(
                ResearchDataRequest(
                    request_id=request_id,
                    created_at=record.created_at,
                    display_name=record.display_name,
                    symbol=record.symbol,
                    market=record.market,
                    confidence=record.confidence,
                    request_text=request_text,
                    suggested_commands=[action.command for action in suggested_actions],
                    memo_path=record.memo_path,
                    verification_path=record.verification_path,
                    suggested_actions=suggested_actions,
                    source_type="memo",
                )
            )
    requests.extend(
        _verification_hypothesis_data_requests(
            output_dir,
            symbol=symbol,
            name=name,
            limit=limit,
            latest_per_task=latest_per_task,
            seen_tasks=seen_tasks,
            handled_request_ids=handled_request_ids,
        )
    )
    return requests


def fulfill_research_data_request(
    output_dir: Path,
    *,
    request_index: int = 1,
    request_id: str | None = None,
    symbol: str | None = None,
    name: str | None = None,
    limit: int = 20,
    force: bool = True,
    pull_market: PullMarket = pull_market_prices,
    pull_news: PullNews = pull_news_events,
    pull_filings: PullFilings = pull_sec_filings,
    write_fund_guide: WriteFundGuide = write_fund_metadata_guide,
    verify_task: VerifyTask = verify_research_task,
) -> ResearchDataRequestFulfillment:
    requests = list_research_data_requests(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    request = _select_request(requests, request_index=request_index, request_id=request_id)
    executions: list[ResearchDataRequestExecution] = []
    data_changed = False
    needs_manual_step = False
    verify_action = _verify_action(request)

    for action in request.suggested_actions:
        if action.action_type == "verify":
            continue
        if action.action_type == "fund_metadata_import":
            needs_manual_step = True
            executions.append(
                ResearchDataRequestExecution(
                    action_type=action.action_type,
                    status="manual_required",
                    command=action.command,
                    count=0,
                    output_path=None,
                    message="需要先人工填写基金资料模板，再导入缓存。",
                )
            )
            continue
        execution = _execute_data_request_action(
            output_dir=output_dir,
            request=request,
            action=action,
            force=force,
            pull_market=pull_market,
            pull_news=pull_news,
            pull_filings=pull_filings,
            write_fund_guide=write_fund_guide,
        )
        executions.append(execution)
        if (
            execution.status == "completed"
            and execution.count > 0
            and action.action_type
            in {
                "market",
                "news",
                "filings",
            }
        ):
            data_changed = True

    if data_changed and verify_action is not None:
        executions.append(
            _execute_verify_action(
                output_dir=output_dir,
                request=request,
                action=verify_action,
                verify_task=verify_task,
            )
        )
    elif verify_action is not None:
        message = (
            "等待人工补来源或填写模板后再重新核验。"
            if needs_manual_step or research_data_request_needs_manual_source(request)
            else "本次没有改变本地数据，未重新核验。"
        )
        executions.append(
            ResearchDataRequestExecution(
                action_type="verify",
                status="skipped",
                command=verify_action.command,
                count=0,
                output_path=None,
                message=message,
            )
        )
    status = _fulfillment_status(executions)
    artifact_path = _write_fulfillment_artifact(
        output_dir=output_dir,
        request=request,
        executions=executions,
        status=status,
    )
    return ResearchDataRequestFulfillment(
        request=request,
        executions=executions,
        status=status,
        artifact_path=artifact_path,
    )


def research_data_request_needs_manual_source(item: ResearchDataRequest) -> bool:
    executable_data_actions = [
        action
        for action in item.suggested_actions
        if action.auto_executable and action.action_type != "verify"
    ]
    if item.suggested_actions:
        return not executable_data_actions
    return (
        len(item.suggested_commands) == 1
        and item.suggested_commands[0].startswith("lychee research verify ")
    )


def _handled_data_request_ids(output_dir: Path) -> set[str]:
    handled_statuses = {"completed", "cached", "manual_required"}
    return {
        record.request_id
        for record in list_research_data_request_fulfillments(output_dir, limit=500)
        if record.status in handled_statuses
    }


def _fulfillment_status(executions: list[ResearchDataRequestExecution]) -> str:
    statuses = [execution.status for execution in executions]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "no-data" for status in statuses):
        return "no-data"
    if any(status == "manual_required" for status in statuses):
        return "manual_required"
    if any(status == "completed" for status in statuses):
        return "completed"
    if any(status == "cached" for status in statuses):
        return "cached"
    return "skipped"


def _write_fulfillment_artifact(
    *,
    output_dir: Path,
    request: ResearchDataRequest,
    executions: list[ResearchDataRequestExecution],
    status: str,
) -> Path:
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    fulfillment_id = f"research-data-request-fulfillment:{created_at}:{request.request_id}"
    output_path = _latest_output_path(executions)
    payload: dict[str, object] = {
        "fulfillment_id": fulfillment_id,
        "created_at": created_at,
        "request_id": request.request_id,
        "display_name": request.display_name,
        "symbol": request.symbol,
        "market": request.market,
        "status": status,
        "request_text": request.request_text,
        "memo_path": request.memo_path,
        "verification_path": request.verification_path,
        "output_path": str(output_path or ""),
        "executions": [
            {
                "action_type": execution.action_type,
                "status": execution.status,
                "command": execution.command,
                "count": execution.count,
                "output_path": str(execution.output_path or ""),
                "message": execution.message,
                "warnings": execution.warnings,
            }
            for execution in executions
        ],
    }
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = _unique_artifact_path(
        research_dir,
        "research-data-request-fulfillment",
        created_at,
    )
    payload["fulfillment_path"] = str(artifact_path)
    artifact_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_research_data_request_fulfillment_record(
        output_dir=output_dir,
        fulfillment_id=fulfillment_id,
        created_at=created_at,
        request_id=request.request_id,
        display_name=request.display_name,
        symbol=request.symbol,
        market=request.market,
        status=status,
        action_count=len(executions),
        fulfillment_path=artifact_path,
        output_path=output_path,
        payload=payload,
    )
    return artifact_path


def _latest_output_path(
    executions: list[ResearchDataRequestExecution],
) -> Path | None:
    for execution in reversed(executions):
        if execution.output_path is not None:
            return execution.output_path
    return None


def _unique_artifact_path(directory: Path, prefix: str, created_at: str) -> Path:
    safe_timestamp = created_at.replace(":", "").replace("+", "Z")
    candidate = directory / f"{prefix}-{safe_timestamp}.json"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{prefix}-{safe_timestamp}-{counter}.json"
        counter += 1
    return candidate


def list_provider_backlog_items(
    output_dir: Path,
    *,
    symbol: str | None = None,
    name: str | None = None,
    limit: int = 20,
) -> list[ProviderBacklogItem]:
    backlog: list[ProviderBacklogItem] = []
    for request in list_research_data_requests(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    ):
        gap = _classify_provider_gap(request.request_text)
        if not research_data_request_needs_manual_source(request) and not gap.always_backlog:
            continue
        backlog.append(
            ProviderBacklogItem(
                request_id=request.request_id,
                created_at=request.created_at,
                display_name=request.display_name,
                symbol=request.symbol,
                market=request.market,
                confidence=request.confidence,
                request_text=request.request_text,
                data_domain=gap.data_domain,
                plugin_type=gap.plugin_type,
                coverage_gap=gap.coverage_gap,
                suggested_provider_examples=gap.suggested_provider_examples,
                suggested_commands=_provider_gap_commands(request, gap),
                next_step=gap.next_step,
                memo_path=request.memo_path,
                verification_path=request.verification_path,
            )
        )
    return backlog


def _memo_task_key(record: ResearchMemoRecord) -> tuple[str, str, str]:
    return (
        (record.symbol or "").strip().upper(),
        record.display_name.strip().casefold(),
        record.market.strip().upper(),
    )


def _memo_data_requests(record: ResearchMemoRecord) -> list[str]:
    memo = record.payload.get("memo")
    if not isinstance(memo, dict):
        return []
    raw_requests = memo.get("next_data_requests")
    if not isinstance(raw_requests, list):
        return []
    return [item.strip() for item in raw_requests if isinstance(item, str) and item.strip()]


def _verification_hypothesis_data_requests(
    output_dir: Path,
    *,
    symbol: str | None,
    name: str | None,
    limit: int,
    latest_per_task: bool,
    seen_tasks: set[tuple[str, str, str]],
    handled_request_ids: set[str],
) -> list[ResearchDataRequest]:
    requests: list[ResearchDataRequest] = []
    for payload, path in _latest_verification_artifacts(output_dir, limit=limit):
        record = _verification_artifact_record(payload, path)
        if record is None:
            continue
        if not _record_matches_filters(record, symbol=symbol, name=name):
            continue
        task_key = _memo_task_key(record)
        if latest_per_task and task_key in seen_tasks:
            continue
        seen_tasks.add(task_key)
        for index, request_text in enumerate(
            _verification_hypothesis_request_texts(payload),
            start=1,
        ):
            request_id = f"{path.stem}:hypothesis-data-request:{index}"
            if request_id in handled_request_ids:
                continue
            suggested_actions = _suggest_data_request_actions(record, request_text)
            requests.append(
                ResearchDataRequest(
                    request_id=request_id,
                    created_at=record.created_at,
                    display_name=record.display_name,
                    symbol=record.symbol,
                    market=record.market,
                    confidence=record.confidence,
                    request_text=request_text,
                    suggested_commands=[action.command for action in suggested_actions],
                    memo_path="",
                    verification_path=str(path),
                    suggested_actions=suggested_actions,
                    source_type="verification",
                )
            )
    return requests


def _latest_verification_artifacts(
    output_dir: Path,
    *,
    limit: int,
) -> list[tuple[dict[str, object], Path]]:
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
        )[:limit]
    ]


def _verification_artifact_record(
    payload: dict[str, object],
    path: Path,
) -> ResearchMemoRecord | None:
    candidate = _dict_value(payload.get("candidate"))
    display_name = _string_value(candidate.get("display_name"))
    market = _string_value(candidate.get("market")).upper()
    if not display_name or not market:
        return None
    symbol = _string_value(candidate.get("symbol")) or None
    created_at = _string_value(payload.get("created_at")) or path.stem
    confidence = _string_value(payload.get("status_label")) or "待补证据"
    request_texts = _verification_hypothesis_request_texts(payload)
    return ResearchMemoRecord(
        memo_id=f"{path.stem}:hypothesis",
        created_at=created_at,
        display_name=display_name,
        symbol=symbol,
        market=market,
        confidence=confidence,
        summary="下钻核验假设面板提出的下一批数据请求。",
        support_count=0,
        skeptic_count=0,
        missing_count=len(request_texts),
        next_step_count=len(request_texts),
        memo_path="",
        verification_path=str(path),
        payload={},
    )


def _verification_hypothesis_request_texts(payload: dict[str, object]) -> list[str]:
    hypothesis_panel = _dict_value(payload.get("hypothesis_panel"))
    raw_requests = hypothesis_panel.get("next_data_requests")
    if not isinstance(raw_requests, list):
        return []
    return [
        item.strip()
        for item in raw_requests
        if isinstance(item, str) and _is_verification_data_request_text(item.strip())
    ]


def _is_verification_data_request_text(text: str) -> bool:
    if not text or text == "暂无下一批数据请求。":
        return False
    if text.startswith("执行工作台下一步命令"):
        return False
    return True


def _record_matches_filters(
    record: ResearchMemoRecord,
    *,
    symbol: str | None,
    name: str | None,
) -> bool:
    if symbol and (record.symbol or "").strip().upper() != symbol.strip().upper():
        return False
    if name and name.strip().lower() not in record.display_name.strip().lower():
        return False
    return True


def _read_json_dict(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _verification_candidate_key(candidate: dict[str, object]) -> str:
    market = _string_value(candidate.get("market")).upper()
    symbol = _string_value(candidate.get("symbol")).upper()
    if symbol:
        return f"{market}:symbol:{symbol}"
    display_name = _string_value(candidate.get("display_name")).lower()
    if display_name:
        return f"{market}:name:{display_name}"
    return ""


def _select_request(
    requests: list[ResearchDataRequest],
    *,
    request_index: int,
    request_id: str | None,
) -> ResearchDataRequest:
    if not requests:
        raise ValueError("暂无研究数据请求。请先运行 `lychee research memo`。")
    if request_id:
        for request in requests:
            if request.request_id == request_id:
                return request
        raise ValueError("没有找到匹配的数据请求。")
    if request_index < 1 or request_index > len(requests):
        raise ValueError(f"数据请求序号必须在 1 到 {len(requests)} 之间。")
    return requests[request_index - 1]


def _suggest_data_request_actions(
    record: ResearchMemoRecord,
    request_text: str,
) -> list[ResearchDataRequestAction]:
    actions: list[ResearchDataRequestAction] = []
    selector = _research_selector(record)
    lowered = request_text.casefold()
    if _looks_like_fund_metadata_request(lowered) and record.symbol:
        actions.extend(
            [
                ResearchDataRequestAction(
                    "fund_metadata_guide",
                    (
                        f"lychee data guide fund --symbol {record.symbol} "
                        f"--name {_quote_cli_value(record.display_name)} "
                        f"--market {record.market.upper() or '<MARKET>'}"
                    ),
                ),
                ResearchDataRequestAction(
                    "fund_metadata_import",
                    (
                        "lychee data set fund --from-file "
                        f".alphadesk/data/fund-metadata-guide-{record.symbol.upper()}.json"
                    ),
                    auto_executable=False,
                ),
            ]
        )
    if _looks_like_market_request(lowered) and record.symbol:
        actions.append(
            ResearchDataRequestAction(
                "market",
                f"lychee data pull market --symbols {record.symbol} --provider auto --force",
            )
        )
    if _looks_like_news_request(lowered):
        query = _quote_cli_value(record.display_name)
        if record.symbol:
            actions.append(
                ResearchDataRequestAction(
                    "news",
                    "lychee data pull news "
                    f"--symbols {record.symbol} --query {query} --force",
                )
            )
        else:
            actions.append(
                ResearchDataRequestAction(
                    "news",
                    f"lychee data pull news --query {query} --force",
                )
            )
    if _looks_like_filing_request(lowered) and record.symbol and record.market.upper() == "US":
        actions.append(
            ResearchDataRequestAction(
                "filings",
                f"lychee data pull filings --symbols {record.symbol}",
            )
        )
    actions.append(ResearchDataRequestAction("verify", f"lychee research verify {selector}"))
    return _dedupe_actions(actions)


def _execute_data_request_action(
    *,
    output_dir: Path,
    request: ResearchDataRequest,
    action: ResearchDataRequestAction,
    force: bool,
    pull_market: PullMarket,
    pull_news: PullNews,
    pull_filings: PullFilings,
    write_fund_guide: WriteFundGuide,
) -> ResearchDataRequestExecution:
    try:
        if action.action_type == "fund_metadata_guide":
            if not request.symbol:
                raise ValueError("基金资料向导需要证券代码。")
            guide = write_fund_guide(
                output_dir=output_dir,
                symbol=request.symbol,
                display_name=request.display_name,
                market=request.market,
            )
            return ResearchDataRequestExecution(
                action_type=action.action_type,
                status="completed",
                command=action.command,
                count=1,
                output_path=guide.output_path,
                message="已生成基金资料模板；填写并导入后再重新核验。",
            )
        if action.action_type == "market":
            if not request.symbol:
                raise ValueError("行情刷新需要证券代码。")
            result = pull_market(
                symbols=[request.symbol],
                output_dir=output_dir,
                provider_id="auto",
                force=force,
            )
            return _pull_execution(action, result, "行情已刷新。")
        if action.action_type == "news":
            result = pull_news(
                symbols=[request.symbol] if request.symbol else [],
                query=request.display_name,
                output_dir=output_dir,
                provider_id="auto",
                force=force,
            )
            return _pull_execution(action, result, "新闻已刷新。")
        if action.action_type == "filings":
            if not request.symbol:
                raise ValueError("公告刷新需要证券代码。")
            result = pull_filings(symbols=[request.symbol], output_dir=output_dir)
            return _pull_execution(action, result, "SEC 公告已刷新。")
    except (RuntimeError, ValueError) as error:
        return ResearchDataRequestExecution(
            action_type=action.action_type,
            status="failed",
            command=action.command,
            count=0,
            output_path=None,
            message=str(error),
        )
    return ResearchDataRequestExecution(
        action_type=action.action_type,
        status="skipped",
        command=action.command,
        count=0,
        output_path=None,
        message="这个数据请求动作暂不支持自动执行。",
    )


def _execute_verify_action(
    *,
    output_dir: Path,
    request: ResearchDataRequest,
    action: ResearchDataRequestAction,
    verify_task: VerifyTask,
) -> ResearchDataRequestExecution:
    try:
        verification = verify_task(
            output_dir=output_dir,
            symbol=request.symbol,
            name=None if request.symbol else request.display_name,
        )
    except (RuntimeError, ValueError) as error:
        return ResearchDataRequestExecution(
            action_type="verify",
            status="failed",
            command=action.command,
            count=0,
            output_path=None,
            message=str(error),
        )
    output_path = getattr(verification, "artifact_path", None)
    return ResearchDataRequestExecution(
        action_type="verify",
        status="completed",
        command=action.command,
        count=1,
        output_path=output_path if isinstance(output_path, Path) else None,
        message="已重新下钻核验。",
    )


def _pull_execution(
    action: ResearchDataRequestAction,
    result: PullResult,
    message: str,
) -> ResearchDataRequestExecution:
    if result.count == 0:
        return ResearchDataRequestExecution(
            action_type=action.action_type,
            status="no-data",
            command=action.command,
            count=0,
            output_path=result.output_path,
            message="没有获取到匹配数据，未改变本地研究证据。",
            warnings=result.warnings,
        )
    return ResearchDataRequestExecution(
        action_type=action.action_type,
        status="completed" if result.refreshed else "cached",
        command=action.command,
        count=result.count,
        output_path=result.output_path,
        message=message if result.refreshed else "已使用未过期缓存。",
        warnings=result.warnings,
    )


def _verify_action(request: ResearchDataRequest) -> ResearchDataRequestAction | None:
    for action in request.suggested_actions:
        if action.action_type == "verify":
            return action
    for command in request.suggested_commands:
        if "research verify" in command:
            return ResearchDataRequestAction("verify", command)
    return None


def _looks_like_fund_metadata_request(text: str) -> bool:
    keywords = (
        "etf",
        "基金",
        "费用",
        "费率",
        "跟踪指数",
        "持仓",
        "成分摘要",
        "成分或持仓",
        "tracking index",
        "expense",
        "holdings",
    )
    return any(keyword in text for keyword in keywords)


def _looks_like_market_request(text: str) -> bool:
    keywords = (
        "行情",
        "成交量",
        "价格",
        "相对强弱",
        "波动",
        "近 20 日",
        "volume",
        "price",
        "relative strength",
    )
    return any(keyword in text for keyword in keywords)


def _looks_like_news_request(text: str) -> bool:
    keywords = (
        "新闻",
        "报道",
        "原文",
        "headline",
        "article",
    )
    return any(keyword in text for keyword in keywords)


def _looks_like_filing_request(text: str) -> bool:
    keywords = (
        "sec",
        "公告",
        "财报",
        "10-q",
        "10-k",
        "8-k",
        "filing",
        "利润",
        "毛利率",
    )
    return any(keyword in text for keyword in keywords)


def _research_selector(record: ResearchMemoRecord) -> str:
    if record.symbol:
        return f"--symbol {record.symbol}"
    return f"--name {_quote_cli_value(record.display_name)}"


def _quote_cli_value(value: str) -> str:
    return shlex.quote(value)


def _dedupe_actions(
    actions: list[ResearchDataRequestAction],
) -> list[ResearchDataRequestAction]:
    seen: set[str] = set()
    unique_actions: list[ResearchDataRequestAction] = []
    for action in actions:
        if action.command in seen:
            continue
        seen.add(action.command)
        unique_actions.append(action)
    return unique_actions


@dataclass(frozen=True)
class _ProviderGap:
    data_domain: str
    plugin_type: str
    coverage_gap: str
    suggested_provider_examples: list[str]
    next_step: str
    always_backlog: bool = False


def _classify_provider_gap(request_text: str) -> _ProviderGap:
    text = request_text.casefold()
    if _has_any(
        text,
        (
            "广度",
            "上涨家数",
            "下跌家数",
            "等权",
            "成分股",
            "breadth",
            "advancer",
            "decliner",
            "equal-weight",
        ),
    ):
        return _ProviderGap(
            data_domain="市场广度",
            plugin_type="market_breadth",
            coverage_gap=(
                "当前 provider 只能补行情、新闻、公告和基金资料，"
                "缺少指数成分、上涨家数、等权指数或板块扩散数据。"
            ),
            suggested_provider_examples=[
                "指数成分数据源",
                "等权指数或市场广度数据源",
                "行业/子行业表现数据源",
            ],
            next_step="接入可审计的市场广度 provider 后，再重新运行研究数据请求。",
            always_backlog=True,
        )
    if _has_any(text, ("波动率", "隐含波动", "期权", "vix", "volatility", "option")):
        return _ProviderGap(
            data_domain="波动率指标",
            plugin_type="volatility_metrics",
            coverage_gap=(
                "当前 provider 只能补基础行情，缺少波动率、期权或风险情绪指标。"
            ),
            suggested_provider_examples=[
                "波动率指数数据源",
                "期权链或隐含波动率数据源",
                "风险情绪指标数据源",
            ],
            next_step="接入可审计的波动率 provider 后，再重新运行研究数据请求。",
            always_backlog=True,
        )
    if _has_any(text, ("南向", "北向", "资金流", "流入", "流出", "fund flow", "flow")):
        return _ProviderGap(
            data_domain="资金流",
            plugin_type="fund_flows",
            coverage_gap="当前 provider 缺少跨市场资金流、ETF 资金流或成交结构数据。",
            suggested_provider_examples=[
                "交易所资金流数据源",
                "ETF 资金流数据源",
                "沪深港通资金数据源",
            ],
            next_step="接入可审计的资金流 provider 后，再重新运行研究数据请求。",
            always_backlog=True,
        )
    if _has_any(text, ("行业", "子行业", "板块", "sector", "industry")):
        return _ProviderGap(
            data_domain="行业表现",
            plugin_type="sector_performance",
            coverage_gap="当前 provider 缺少行业/板块分类、成分和相对表现数据。",
            suggested_provider_examples=[
                "行业分类数据源",
                "板块行情数据源",
                "主题指数成分数据源",
            ],
            next_step="接入可审计的行业表现 provider 后，再重新运行研究数据请求。",
        )
    return _ProviderGap(
        data_domain="未覆盖数据",
        plugin_type="custom_research_data",
        coverage_gap="当前 provider 还不能自动补齐这类数据，需要新增插件或人工来源。",
        suggested_provider_examples=[
            "官方披露或交易所数据源",
            "可授权转载的市场数据源",
            "本地 CSV/SQLite 导入插件",
        ],
        next_step="先明确可审计来源，再把它封装成 provider 插件。",
    )


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _provider_gap_commands(
    request: ResearchDataRequest,
    gap: _ProviderGap,
) -> list[str]:
    symbol = request.symbol or "<SYMBOL>"
    return [
        (
            f"lychee data set metric --symbol {symbol} "
            f"--domain {gap.plugin_type} "
            '--name "<填入指标名称>" --value "<填入核验后的读数>" '
            '--as-of YYYY-MM-DD --source-url "<资料来源URL>"'
        )
    ]
