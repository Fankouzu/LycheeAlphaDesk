import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.live_data import (
    FundMetadataGuide,
    PullResult,
    ResearchMetric,
    pull_fund_metadata,
    pull_market_breadth_metrics,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    pull_sec_financials,
    pull_volatility_metrics,
    read_fund_metadata_cache,
    read_research_metric_cache,
    write_fund_metadata_guide,
)
from lychee_alphadesk.core.research_db import (
    ResearchDataRequestFulfillmentRecord,
    ResearchMemoRecord,
    list_research_data_request_fulfillments,
    list_research_memos,
    write_research_data_request_fulfillment_record,
)
from lychee_alphadesk.core.workbench import verify_research_task

PullMarket = Callable[..., PullResult]
PullNews = Callable[..., PullResult]
PullFilings = Callable[..., PullResult]
PullFinancials = Callable[..., PullResult]
PullVolatility = Callable[..., PullResult]
PullBreadth = Callable[..., PullResult]
PullFundMetadata = Callable[..., PullResult]
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


@dataclass(frozen=True)
class ResearchDataRequestFailedAction:
    action_type: str
    message: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchDataRequestDiagnostic:
    request: ResearchDataRequest
    attempted_at: str
    summary: str
    recovery_steps: list[str]
    retry_command: str
    failure_path: Path
    failed_actions: list[ResearchDataRequestFailedAction]


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
            if _research_request_is_covered(output_dir, record, request_text):
                continue
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


def _research_request_is_covered(
    output_dir: Path,
    record: ResearchMemoRecord,
    request_text: str,
) -> bool:
    if not record.symbol or not _looks_like_fund_metadata_request(request_text.casefold()):
        return False
    if record.symbol.strip().upper() != "QQQ":
        return False
    return any(
        row.symbol == "QQQ"
        and row.tracking_index
        and row.expense_ratio
        and row.holdings_summary
        and row.source_url
        for row in read_fund_metadata_cache(output_dir)
    )


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
    pull_financials: PullFinancials = pull_sec_financials,
    pull_volatility: PullVolatility = pull_volatility_metrics,
    pull_breadth: PullBreadth = pull_market_breadth_metrics,
    pull_fund_metadata: PullFundMetadata = pull_fund_metadata,
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
            pull_financials=pull_financials,
            pull_volatility=pull_volatility,
            pull_breadth=pull_breadth,
            pull_fund_metadata=pull_fund_metadata,
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
                "financials",
                "volatility",
                "breadth",
                "news_official",
                "fund_metadata",
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


def acknowledge_manual_research_data_request(
    output_dir: Path,
    *,
    action_type: str,
    symbol: str,
    form: str = "",
) -> ResearchDataRequestFulfillment | None:
    """Close one uniquely matched manual handoff after evidence is written locally."""
    if action_type not in {"manual_source", "manual_filing"}:
        raise ValueError("只能确认人工新闻来源或人工文件证据请求。")
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("确认人工证据请求需要证券代码。")
    normalized_form = form.strip().upper()
    matches: list[ResearchDataRequest] = []
    for request in list_research_data_requests(output_dir, symbol=normalized_symbol):
        matching_actions = [
            action
            for action in request.suggested_actions
            if action.action_type == action_type
        ]
        if not matching_actions:
            continue
        if action_type == "manual_filing" and normalized_form:
            if not any(
                _command_option_value(action.command, "--form").upper()
                == normalized_form
                for action in matching_actions
            ):
                continue
        matches.append(request)
    if len(matches) != 1:
        return None
    return fulfill_research_data_request(
        output_dir,
        request_id=matches[0].request_id,
        force=False,
    )


def diagnose_research_data_request(
    output_dir: Path,
    *,
    request_index: int = 1,
    symbol: str | None = None,
    name: str | None = None,
    limit: int = 20,
) -> ResearchDataRequestDiagnostic:
    """Explain the latest failed request using only local research records."""
    requests = list_research_data_requests(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    request = _select_request(requests, request_index=request_index, request_id=None)
    records = list_research_data_request_fulfillments(
        output_dir,
        request_id=request.request_id,
        limit=1,
    )
    if not records or records[0].status != "failed":
        raise ValueError("这条数据请求没有可诊断的失败记录。")
    failure = records[0]
    summary, recovery_steps = _failure_summary_and_recovery_steps(failure)
    return ResearchDataRequestDiagnostic(
        request=request,
        attempted_at=failure.created_at,
        summary=summary,
        recovery_steps=recovery_steps,
        retry_command=(
            "lychee research run-data-request "
            f"--request {request_index} {_research_request_selector(request)}"
        ),
        failure_path=Path(failure.fulfillment_path),
        failed_actions=_failed_actions(failure),
    )


def describe_research_data_request_failure(
    record: ResearchDataRequestFulfillmentRecord,
) -> str:
    """Return a safe, user-facing failure summary for queues and diagnostics."""
    return _failure_summary_and_recovery_steps(record)[0]


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


def _failure_summary_and_recovery_steps(
    record: ResearchDataRequestFulfillmentRecord,
) -> tuple[str, list[str]]:
    details = _failure_details(record)
    text = " ".join(details).casefold()
    if "operation not permitted" in text or "errno 1" in text:
        return (
            "网络连接或系统权限阻止了数据源请求。",
            [
                "这不是 API Key 配置问题；先确认当前终端允许访问网络。",
                "检查代理、防火墙、DNS 或系统网络权限后，再重试。",
            ],
        )
    if "timed out" in text or "timeout" in text:
        return (
            "数据源请求超时。",
            [
                "确认 provider 服务状态、网络代理和 DNS 正常。",
                "稍后重试；不要连续强制刷新同一请求。",
            ],
        )
    if "http error 401" in text or "unauthorized" in text:
        return (
            "数据源认证失败，请检查 API key 或额度。",
            [
                "运行 `lychee setup` 检查并更新对应 provider 的凭据。",
                "确认账户额度和 API key 状态后，再重试。",
            ],
        )
    if "http error 403" in text or "forbidden" in text:
        return (
            "数据源拒绝访问，请检查权限、频率限制、地区限制或请求标识。",
            [
                "检查 provider 控制台中的权限、套餐额度和访问限制。",
                "确认请求频率恢复后，再重试。",
            ],
        )
    if "尚未配置" in text or "lychee setup" in text:
        return (
            "相关数据源尚未完成配置。",
            [
                "运行 `lychee setup` 完成对应 provider 的配置。",
                "保存配置后，再重试这条数据请求。",
            ],
        )
    if "urlopen error" in text or "failed to establish" in text:
        return (
            "数据源连接失败，请检查网络、代理、防火墙、DNS 或 provider 状态。",
            [
                "确认本机网络和代理配置能访问 provider 域名。",
                "确认 provider 服务恢复后，再重试。",
            ],
        )
    return (
        "上次 provider 执行失败，请查看来源 artifact 中的原始错误。",
        [
            "查看失败 artifact 中各动作的原始错误和 warning。",
            "修复对应数据源后，再重试。",
        ],
    )


def _failed_actions(
    record: ResearchDataRequestFulfillmentRecord,
) -> list[ResearchDataRequestFailedAction]:
    actions: list[ResearchDataRequestFailedAction] = []
    executions = record.payload.get("executions")
    if not isinstance(executions, list):
        return actions
    for execution in executions:
        if not isinstance(execution, dict):
            continue
        status = execution.get("status")
        if status not in {None, "", "failed"}:
            continue
        message = execution.get("message")
        warnings = execution.get("warnings")
        actions.append(
            ResearchDataRequestFailedAction(
                action_type=str(execution.get("action_type") or "provider"),
                message=message if isinstance(message, str) else "未记录原始错误。",
                warnings=[warning for warning in warnings if isinstance(warning, str)]
                if isinstance(warnings, list)
                else [],
            )
        )
    return actions


def _failure_details(record: ResearchDataRequestFulfillmentRecord) -> list[str]:
    details: list[str] = []
    for action in _failed_actions(record):
        if action.message:
            details.append(action.message)
        details.extend(action.warnings)
    return details


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
    backlog_keys: set[tuple[str, str, str]] = set()
    research_metrics = read_research_metric_cache(output_dir)
    requests = list_research_data_requests(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    request_keys = {
        _provider_backlog_request_key(request)
        for request in requests
    }
    for request in _verification_backlog_requests(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    ):
        if _provider_backlog_request_key(request) in request_keys:
            continue
        request_keys.add(_provider_backlog_request_key(request))
        requests.append(request)
    for request in requests:
        if _is_process_research_request(request):
            continue
        if _is_manual_evidence_request(request):
            continue
        gap = _classify_provider_gap(request.request_text)
        backlog_key = (
            (request.symbol or request.display_name).strip().upper(),
            gap.plugin_type,
            request.request_text.strip().casefold(),
        )
        if backlog_key in backlog_keys:
            continue
        if not research_data_request_needs_manual_source(request) and not gap.always_backlog:
            continue
        if _provider_gap_is_covered(gap, request, research_metrics):
            continue
        backlog_keys.add(backlog_key)
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
                suggested_commands=_provider_gap_commands(
                    request,
                    gap,
                    research_metrics=research_metrics,
                ),
                next_step=gap.next_step,
                memo_path=request.memo_path,
                verification_path=request.verification_path,
            )
        )
    return backlog


def _verification_backlog_requests(
    output_dir: Path,
    *,
    symbol: str | None,
    name: str | None,
    limit: int,
) -> list[ResearchDataRequest]:
    """Keep residual provider gaps visible after a proxy request is fulfilled."""
    return _verification_hypothesis_data_requests(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
        latest_per_task=True,
        seen_tasks=set(),
        handled_request_ids=set(),
    )


def _provider_backlog_request_key(request: ResearchDataRequest) -> tuple[str, str]:
    return (
        (request.symbol or request.display_name).strip().upper(),
        request.request_text.strip().casefold(),
    )


def _is_manual_evidence_request(request: ResearchDataRequest) -> bool:
    return any(
        action.action_type == "manual_filing"
        for action in request.suggested_actions
    )


def _is_process_research_request(request: ResearchDataRequest) -> bool:
    text = request.request_text.casefold()
    return _has_any(
        text,
        (
            "对照支持链和反证链",
            "复核最强反证来源",
            "一致性复核",
            "回答同一个核心问题",
        ),
    )


def _provider_gap_is_covered(
    gap: "_ProviderGap",
    request: ResearchDataRequest,
    research_metrics: list[ResearchMetric],
) -> bool:
    if gap.plugin_type == "market_breadth" and request.symbol:
        if _looks_like_advancer_count_request(request.request_text.casefold()):
            return False
        return _nasdaq_proxy_is_complete(request.symbol, research_metrics)
    if gap.plugin_type != "volatility_metrics" or not request.symbol:
        return False
    expected_names = {
        "cboe vxn 收盘",
        "cboe vxn 20 交易日变化",
        "cboe vxn 近一年历史分位",
    }
    matched_names = {
        metric.name.strip().casefold()
        for metric in research_metrics
        if metric.symbol.upper() == request.symbol.upper()
        and metric.domain == "volatility_metrics"
        and metric.provider == "cboe"
    }
    return expected_names.issubset(matched_names)


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
        news_refresh_exhausted = _verification_topic_news_exhausted(payload) or (
            _verification_follows_completed_news_refresh(
                output_dir,
                record,
                path,
            )
        )
        for index, request_text in enumerate(
            _verification_hypothesis_request_texts(payload),
            start=1,
        ):
            request_id = f"{path.stem}:hypothesis-data-request:{index}"
            if request_id in handled_request_ids:
                continue
            if news_refresh_exhausted and _looks_like_news_request(request_text.casefold()):
                request_text = _manual_topic_news_request_text()
                suggested_actions = _manual_topic_news_actions(record)
            else:
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
        payload={"candidate": candidate},
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


def _verification_topic_news_exhausted(payload: dict[str, object]) -> bool:
    candidate = _dict_value(payload.get("candidate"))
    return candidate.get("topic_news_exhausted") is True


def _verification_follows_completed_news_refresh(
    output_dir: Path,
    record: ResearchMemoRecord,
    verification_path: Path,
) -> bool:
    for fulfillment in list_research_data_request_fulfillments(output_dir, limit=500):
        if not _fulfillment_matches_record(fulfillment, record):
            continue
        executions = _dict_list(fulfillment.payload.get("executions"))
        has_news = any(
            _string_value(execution.get("action_type")) == "news"
            and _string_value(execution.get("status")) in {"completed", "cached"}
            and _int_value(execution.get("count")) > 0
            for execution in executions
        )
        if not has_news:
            continue
        return any(
            _string_value(execution.get("action_type")) == "verify"
            and _string_value(execution.get("status")) == "completed"
            and _string_value(execution.get("output_path")) == str(verification_path)
            for execution in executions
        )
    return False


def _fulfillment_matches_record(
    fulfillment: ResearchDataRequestFulfillmentRecord,
    record: ResearchMemoRecord,
) -> bool:
    if fulfillment.market.strip().upper() != record.market.strip().upper():
        return False
    if fulfillment.symbol and record.symbol:
        return fulfillment.symbol.strip().upper() == record.symbol.strip().upper()
    return fulfillment.display_name.strip().casefold() == record.display_name.strip().casefold()


def _manual_topic_news_request_text() -> str:
    return (
        "主题新闻已刷新，但没有形成可审计的主题证据。不要重复刷新同一查询；"
        "请先复核现有新闻与研究问题的匹配度，必要时补充一手或可授权的来源。"
    )


def _manual_topic_news_actions(record: ResearchMemoRecord) -> list[ResearchDataRequestAction]:
    symbol = record.symbol.strip().upper() if record.symbol else "<证券代码>"
    actions: list[ResearchDataRequestAction] = []
    if symbol == "0700.HK":
        actions.append(
            ResearchDataRequestAction(
                "news_official",
                "lychee data pull news --symbols 0700.HK "
                "--provider tencent_official --force",
            )
        )
    actions.extend(
        [
        ResearchDataRequestAction(
            "manual_source",
            (
                f"lychee data set news --symbol {symbol} --headline \"已核验标题\" "
                "--summary \"与研究问题有关的关键事实\" "
                "--source-url \"https://...\""
            ),
            auto_executable=False,
        ),
        ResearchDataRequestAction(
            "verify",
            f"lychee research verify {_research_selector(record)}",
            auto_executable=False,
        ),
        ]
    )
    return actions


def _manual_filing_actions(
    record: ResearchMemoRecord,
    request_text: str,
) -> list[ResearchDataRequestAction]:
    lowered = request_text.casefold()
    if _has_any(lowered, ("form 4", "4 表", "4表", "内部人交易")):
        form = "4"
    elif "8-k" in lowered:
        form = "8-K"
    elif "10-q" in lowered:
        form = "10-Q"
    elif "10-k" in lowered:
        form = "10-K"
    else:
        form = "<表单类型>"
    return [
        ResearchDataRequestAction(
            "manual_filing",
            (
                "lychee data set filing "
                f"--symbol {record.symbol or '<证券代码>'} "
                f"--company {_quote_cli_value(record.display_name)} --form \"{form}\" "
                "--date YYYY-MM-DD --summary \"已核验的关键事实\" "
                '--source-url "https://..."'
            ),
            auto_executable=False,
        ),
        ResearchDataRequestAction(
            "verify",
            f"lychee research verify {_research_selector(record)}",
            auto_executable=False,
        ),
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


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


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
    is_volatility_request = _looks_like_volatility_request(lowered)
    is_breadth_request = _looks_like_breadth_request(lowered)
    if _looks_like_manual_filing_content_request(lowered):
        return _manual_filing_actions(record, request_text)
    if _looks_like_fund_metadata_request(lowered) and record.symbol:
        if record.symbol.upper() == "QQQ":
            actions.append(
                ResearchDataRequestAction(
                    "fund_metadata",
                    "lychee data pull fund-metadata --symbols QQQ --force",
                )
            )
        else:
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
    if is_breadth_request and record.symbol:
        actions.append(
            ResearchDataRequestAction(
                "breadth",
                f"lychee data pull breadth --symbols {record.symbol} --force",
            )
        )
    elif is_volatility_request and record.symbol:
        actions.append(
            ResearchDataRequestAction(
                "volatility",
                f"lychee data pull volatility --symbols {record.symbol} --force",
            )
        )
    elif _looks_like_market_request(lowered) and record.symbol:
        market_symbols = _market_request_symbols(record.symbol, lowered)
        actions.append(
            ResearchDataRequestAction(
                "market",
                "lychee data pull market --symbols "
                f"{','.join(market_symbols)} --provider auto --force",
            )
        )
    if (
        _looks_like_news_request(lowered)
        and not is_volatility_request
        and not is_breadth_request
    ):
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
    if (
        _looks_like_filing_request(lowered)
        and record.symbol
        and record.market.upper() in {"US", "HK", "CN"}
    ):
        actions.append(
            ResearchDataRequestAction(
                "filings",
                f"lychee data pull filings --symbols {record.symbol}",
            )
        )
    if (
        _looks_like_financial_snapshot_request(lowered)
        and record.symbol
        and record.market.upper() == "US"
        and _supports_financial_snapshot(record)
    ):
        actions.append(
            ResearchDataRequestAction(
                "financials",
                f"lychee data pull financials --symbols {record.symbol} --force",
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
    pull_financials: PullFinancials,
    pull_volatility: PullVolatility,
    pull_breadth: PullBreadth,
    pull_fund_metadata: PullFundMetadata,
    write_fund_guide: WriteFundGuide,
) -> ResearchDataRequestExecution:
    try:
        if action.action_type == "manual_source":
            return ResearchDataRequestExecution(
                action_type=action.action_type,
                status="manual_required",
                command=action.command,
                count=0,
                output_path=None,
                message="请先补充可审计的主题来源，再重新核验。",
            )
        if action.action_type == "manual_filing":
            return ResearchDataRequestExecution(
                action_type=action.action_type,
                status="manual_required",
                command=action.command,
                count=0,
                output_path=None,
                message="请先补充已核验的公告或表单摘要，再重新核验。",
            )
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
        if action.action_type == "fund_metadata":
            if not request.symbol:
                raise ValueError("基金资料刷新需要证券代码。")
            result = pull_fund_metadata(
                symbols=[request.symbol],
                output_dir=output_dir,
                force=force,
            )
            return _pull_execution(action, result, "官方基金资料已刷新。")
        if action.action_type == "market":
            if not request.symbol:
                raise ValueError("行情刷新需要证券代码。")
            market_symbols = _market_request_symbols(
                request.symbol,
                request.request_text.casefold(),
            )
            result = pull_market(
                symbols=market_symbols,
                output_dir=output_dir,
                provider_id="auto",
                force=force,
            )
            return _pull_execution(action, result, "行情已刷新。")
        if action.action_type in {"news", "news_official"}:
            result = pull_news(
                symbols=[request.symbol] if request.symbol else [],
                query=request.display_name,
                output_dir=output_dir,
                provider_id=(
                    "tencent_official"
                    if action.action_type == "news_official"
                    else "auto"
                ),
                force=force,
            )
            return _pull_execution(action, result, "新闻已刷新。")
        if action.action_type == "filings":
            if not request.symbol:
                raise ValueError("公告刷新需要证券代码。")
            result = pull_filings(symbols=[request.symbol], output_dir=output_dir)
            return _pull_execution(action, result, "公司公告已刷新。")
        if action.action_type == "financials":
            if not request.symbol:
                raise ValueError("财务快照刷新需要证券代码。")
            result = pull_financials(
                symbols=[request.symbol],
                output_dir=output_dir,
                force=force,
            )
            return _pull_execution(action, result, "SEC 财务快照已刷新。")
        if action.action_type == "volatility":
            if not request.symbol:
                raise ValueError("波动率指标刷新需要证券代码。")
            result = pull_volatility(
                symbols=[request.symbol],
                output_dir=output_dir,
                force=force,
            )
            return _pull_execution(action, result, "Cboe 波动率指标已刷新。")
        if action.action_type == "breadth":
            if not request.symbol:
                raise ValueError("市场扩散指标刷新需要证券代码。")
            result = pull_breadth(
                symbols=[request.symbol],
                output_dir=output_dir,
                force=force,
            )
            return _pull_execution(action, result, "Nasdaq 市场扩散代理已刷新。")
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


def _looks_like_manual_filing_content_request(text: str) -> bool:
    return _has_any(
        text,
        (
            "公告正文",
            "表单正文",
            "form 4",
            "4 表",
            "4表",
            "内部人交易",
            "8-k 正文",
            "8-k body",
            "8-k content",
            "10-q 正文",
            "10-q body",
            "10-k 正文",
            "10-k body",
        ),
    )


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


def _market_request_symbols(symbol: str, request_text: str) -> list[str]:
    normalized = symbol.strip().upper()
    benchmark_terms = (
        "更宽市场基准",
        "宽基",
        "benchmark",
        "s&p 500",
        "spy",
    )
    comparison_terms = ("对比", "比较", "相对强弱", "vs", "versus")
    if (
        normalized == "QQQ"
        and _has_any(request_text, benchmark_terms)
        and _has_any(request_text, comparison_terms)
    ):
        return ["QQQ", "SPY"]
    return [normalized]


def _looks_like_volatility_request(text: str) -> bool:
    return _has_any(
        text,
        (
            "波动率",
            "隐含波动",
            "vix",
            "vxn",
            "volatility",
            "option implied",
        ),
    )


def _looks_like_breadth_request(text: str) -> bool:
    return _has_any(
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
    )


def _looks_like_advancer_count_request(text: str) -> bool:
    return _has_any(text, ("上涨家数", "下跌家数", "advancer", "decliner"))


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


def _looks_like_financial_snapshot_request(text: str) -> bool:
    keywords = (
        "财务快照",
        "营收",
        "收入",
        "净利润",
        "经营现金流",
        "现金流",
        "revenue",
        "net income",
        "operating cash flow",
        "companyfacts",
        "xbrl",
    )
    return any(keyword in text for keyword in keywords)


def _supports_financial_snapshot(record: ResearchMemoRecord) -> bool:
    candidate = _dict_value(record.payload.get("candidate"))
    asset_type = _string_value(candidate.get("asset_type")).casefold()
    return not asset_type or asset_type == "stock"


def _research_selector(record: ResearchMemoRecord) -> str:
    if record.symbol:
        return f"--symbol {record.symbol}"
    return f"--name {_quote_cli_value(record.display_name)}"


def _research_request_selector(request: ResearchDataRequest) -> str:
    if request.symbol:
        return f"--symbol {request.symbol}"
    return f"--name {_quote_cli_value(request.display_name)}"


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


def _command_option_value(command: str, option: str) -> str:
    try:
        parts = shlex.split(command)
        return parts[parts.index(option) + 1].strip()
    except (IndexError, ValueError):
        return ""


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
            "主题新闻已刷新",
            "可审计主题新闻",
            "不要重复刷新同一查询",
        ),
    ):
        return _ProviderGap(
            data_domain="可审计主题新闻",
            plugin_type="entity_news",
            coverage_gap=(
                "当前新闻 provider 已返回行，但过滤后没有能回答研究问题的主题证据；"
                "不能重复使用同一查询推进研究。"
            ),
            suggested_provider_examples=[
                "发行人 IR 或公司新闻稿来源",
                "交易所公告或监管披露来源",
                "可授权、可保留原始链接的公司级财经新闻 API",
            ],
            next_step=(
                "接入可审计的公司/主题新闻 provider，或录入已核验的一手来源后，"
                "再重新下钻核验。"
            ),
            always_backlog=True,
        )
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
                "当前自动 provider 可补 Nasdaq-100 等权/市值加权扩散代理，"
                "但不能把它解释成真实上涨家数/下跌家数；成分级广度仍需专门来源。"
            ),
            suggested_provider_examples=[
                "Nasdaq NDX/NDXE 公开历史（等权扩散代理）",
                "Nasdaq Data Link / GIDS 成分与行情（需授权）",
                "等权指数或市场广度数据源",
                "行业/子行业表现数据源",
            ],
            next_step=(
                "已接入 Nasdaq NDX/NDXE 扩散代理；若需要真实上涨/下跌家数，"
                "仍需专门成分级 provider 或人工核验来源。"
            ),
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
    *,
    research_metrics: list[ResearchMetric] | None = None,
) -> list[str]:
    symbol = request.symbol or "<SYMBOL>"
    if gap.plugin_type == "entity_news":
        return [
            (
                f"lychee data set news --symbol {symbol} --headline \"已核验标题\" "
                "--summary \"与研究问题有关的关键事实\" "
                "--source-url \"https://...\""
            )
        ]
    if gap.plugin_type == "market_breadth" and request.symbol:
        manual_metric_command = (
            f"lychee data set metric --symbol {symbol} "
            f"--domain {gap.plugin_type} "
            '--name "<填入上涨/下跌家数或成分级广度指标>" '
            '--value "<填入核验后的读数>" '
            '--as-of YYYY-MM-DD --source-url "<资料来源URL>"'
        )
        proxy_is_complete = research_metrics is not None and _nasdaq_proxy_is_complete(
            request.symbol,
            research_metrics,
        )
        if proxy_is_complete:
            return [manual_metric_command]
        return [
            f"lychee data pull breadth --symbols {request.symbol} --force",
            manual_metric_command,
        ]
    return [
        (
            f"lychee data set metric --symbol {symbol} "
            f"--domain {gap.plugin_type} "
            '--name "<填入指标名称>" --value "<填入核验后的读数>" '
            '--as-of YYYY-MM-DD --source-url "<资料来源URL>"'
        )
    ]


def _nasdaq_proxy_is_complete(
    symbol: str,
    research_metrics: list[ResearchMetric],
) -> bool:
    expected_names = {
        "nasdaq-100 市值加权 20 交易日变化",
        "nasdaq-100 等权 20 交易日变化",
        "nasdaq-100 等权相对市值加权差异",
    }
    matched_names = {
        metric.name.strip().casefold()
        for metric in research_metrics
        if metric.symbol.upper() == symbol.upper()
        and metric.domain == "market_breadth"
        and metric.provider == "nasdaq_public"
    }
    return expected_names.issubset(matched_names)
