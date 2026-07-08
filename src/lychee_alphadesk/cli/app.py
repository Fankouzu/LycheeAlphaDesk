import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lychee_alphadesk.core import setup as setup_helpers
from lychee_alphadesk.core.action_queue import ActionQueueItem, build_action_queue
from lychee_alphadesk.core.audit import init_audit_db, list_audit_records
from lychee_alphadesk.core.cache_freshness import (
    cache_entry_status,
    list_cache_entries,
)
from lychee_alphadesk.core.config import (
    AlphaDeskConfig,
    ProviderSetupInfo,
    ensure_config_file,
    load_config,
    set_openai_compatible_llm,
    set_provider_value,
)
from lychee_alphadesk.core.data_engine import build_demo_data_snapshot, write_snapshot_json
from lychee_alphadesk.core.demo import REQUIRED_DEMO_FILES, check_demo_workspace
from lychee_alphadesk.core.discovery import (
    DiscoveryDataRequiredError,
    DiscoveryLLMRequiredError,
    build_today_discovery_report,
    discovery_report_summary,
    parse_markets,
    write_discovery_report,
)
from lychee_alphadesk.core.live_data import (
    PullResult,
    build_cached_data_snapshot,
    parse_symbols,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    run_cached_data_health,
    write_fund_metadata_cache,
    write_fund_metadata_cache_from_file,
    write_fund_metadata_guide,
    write_research_metric_cache,
)
from lychee_alphadesk.core.llm import LLMProviderError
from lychee_alphadesk.core.opportunity_radar import (
    OpportunityRadarReport,
    build_opportunity_radar,
    write_opportunity_radar_report,
)
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR, DEMO_ROOT
from lychee_alphadesk.core.policy import load_policy, validate_policy
from lychee_alphadesk.core.reports import generate_demo_report
from lychee_alphadesk.core.research import deepen_research_queue, fill_research_data_gaps
from lychee_alphadesk.core.research_db import (
    list_research_evidence_reviews,
    list_research_memos,
    list_research_queue,
    list_research_reviews,
    write_discovery_research_run,
)
from lychee_alphadesk.core.research_memo import (
    ResearchMemoResult,
    generate_research_memo,
)
from lychee_alphadesk.core.research_requests import (
    ProviderBacklogItem,
    ResearchDataRequest,
    ResearchDataRequestFulfillment,
    fulfill_research_data_request,
    list_provider_backlog_items,
    list_research_data_requests,
    research_data_request_needs_manual_source,
)
from lychee_alphadesk.core.workbench import (
    RESEARCH_EVIDENCE_REVIEW_VERDICTS,
    RESEARCH_REVIEW_VERDICTS,
    PendingEvidenceReviewItem,
    ResearchEvidenceReviewResult,
    ResearchReviewResult,
    ResearchRunResult,
    ResearchVerificationResult,
    WorkbenchCheckResult,
    beginner_research_brief,
    list_pending_evidence_reviews,
    record_research_evidence_review,
    record_research_review,
    render_research_task_detail,
    research_evidence_change_detail_groups,
    run_research_task,
    run_workbench_check,
    select_research_candidate_index,
    suggest_pending_evidence_review,
    verify_research_task,
)
from lychee_alphadesk.tui.app import run_tui
from lychee_alphadesk.tui.setup import run_setup_tui

console = Console()
app = typer.Typer(
    help="Lychee AlphaDesk 终端原生投资研究工作台。",
    invoke_without_command=True,
)
policy_app = typer.Typer(help="投资政策命令。")
audit_app = typer.Typer(help="审计记录命令。")
data_app = typer.Typer(help="行情、新闻、公告和预测数据命令。")
data_pull_app = typer.Typer(help="拉取实时数据源数据到本地缓存。")
data_set_app = typer.Typer(help="写入人工核验过的数据到本地缓存。")
data_guide_app = typer.Typer(help="生成数据补齐向导和本地模板。")
discover_app = typer.Typer(help="发现优先的市场研究命令。")
research_app = typer.Typer(help="本地研究库和研究队列命令。")
setup_app = typer.Typer(
    help="打开配置中心，或写入单项配置值。",
    invoke_without_command=True,
)
llm_setup_app = typer.Typer(
    help="写入单项 LLM 服务配置值。",
    invoke_without_command=True,
)


@app.callback()
def root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        run_tui()


@app.command()
def demo() -> None:
    """检查内置演示文件是否可用。"""
    missing = check_demo_workspace(DEMO_ROOT)
    if missing:
        for path in missing:
            console.print(f"缺少演示文件: {path}")
        raise typer.Exit(code=1)

    init_audit_db(DEFAULT_OUTPUT_DIR)
    console.print("演示工作区已就绪")
    for name in REQUIRED_DEMO_FILES:
        console.print(f"- examples/demo/{name}")
    console.print(f"输出目录: {DEFAULT_OUTPUT_DIR}")


@app.command()
def report(
    demo: Annotated[
        bool,
        typer.Option("--demo", help="使用内置演示数据生成报告。"),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="报告输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """生成 Markdown 日报。"""
    if not demo:
        console.print("v0.1 仅支持使用 --demo 生成报告。")
        raise typer.Exit(code=1)

    result = generate_demo_report(output_dir=output_dir)
    console.print(f"报告已写入: {result.report_path}")
    console.print(f"审计记录: {result.audit_record.report_id}")


@policy_app.command("check")
def policy_check(path: Path) -> None:
    """校验投资政策 YAML 文件。"""
    policy = load_policy(path)
    result = validate_policy(policy)

    if result.ok:
        console.print("投资政策检查通过")
    else:
        console.print("投资政策检查失败")

    for item in result.passes:
        console.print(f"通过: {item}")
    for item in result.warnings:
        console.print(f"警告: {item}")
    for item in result.errors:
        console.print(f"错误: {item}")

    if not result.ok:
        raise typer.Exit(code=1)


@audit_app.command("list")
def audit_list(
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="审计输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """列出已生成的审计记录。"""
    records = list_audit_records(output_dir)
    if not records:
        console.print("未找到审计记录")
        return

    for record in records:
        console.print(
            f"记录: {record.report_id} {record.mode} {Path(record.report_path).name} "
            f"{record.report_path}"
        )

    table = Table(title="Lychee AlphaDesk 审计记录")
    table.add_column("报告 ID")
    table.add_column("创建时间")
    table.add_column("模式")
    table.add_column("报告文件")
    table.add_column("报告路径")
    for record in records:
        table.add_row(
            record.report_id,
            record.created_at,
            record.mode,
            Path(record.report_path).name,
            record.report_path,
        )
    console.print(table)


@data_app.command("snapshot")
def data_snapshot(
    demo: Annotated[
        bool,
        typer.Option("--demo", help="使用内置演示数据源生成数据快照。"),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="快照输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """写入统一 JSON 数据快照。"""
    snapshot = (
        build_demo_data_snapshot(DEMO_ROOT)
        if demo
        else build_cached_data_snapshot(output_dir)
    )
    output_path = write_snapshot_json(snapshot, output_dir)
    console.print(f"数据快照已写入: {output_path}")
    console.print(f"模式: {_display_mode(snapshot.mode)}")
    console.print(f"数据源: {', '.join(snapshot.provider_names)}")
    console.print(f"行情: {snapshot.counts['prices']}")
    console.print(f"新闻事件: {snapshot.counts['news_events']}")
    console.print(f"公告: {snapshot.counts['filings']}")
    console.print(f"预测: {snapshot.counts['forecasts']}")


@data_app.command("health")
def data_health(
    demo: Annotated[
        bool,
        typer.Option("--demo", help="检查内置演示数据源健康状态。"),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="实时缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """显示数据源质量检查。"""
    if demo:
        snapshot = build_demo_data_snapshot(DEMO_ROOT)
        checks = snapshot.quality_checks
        console.print(f"数据源: {', '.join(snapshot.provider_names)}")
    else:
        checks = run_cached_data_health(output_dir)
        console.print(f"缓存目录: {output_dir / 'data'}", soft_wrap=True)
    table = Table(title="Lychee AlphaDesk 数据健康")
    table.add_column("检查项")
    table.add_column("状态")
    table.add_column("数据源")
    table.add_column("说明")
    for check in checks:
        table.add_row(
            check.name,
            _display_status(check.status),
            check.provider,
            check.message,
        )
    console.print(table)


@data_app.command("freshness")
def data_freshness(
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="实时缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
    layer: Annotated[
        str | None,
        typer.Option("--layer", help="只查看某一层缓存，例如 market。"),
    ] = None,
) -> None:
    """查看本地缓存新鲜度。"""
    entries = list_cache_entries(output_dir, layer=layer)
    if not entries:
        console.print("暂无缓存新鲜度记录。请先运行 `lychee data pull ...`。")
        return

    table = Table(title="Lychee AlphaDesk 数据新鲜度")
    table.add_column("层级")
    table.add_column("状态")
    table.add_column("数据源")
    table.add_column("缓存 Key", overflow="fold")
    table.add_column("市场")
    table.add_column("交易状态")
    table.add_column("过期时间")
    table.add_column("行数")
    for entry in entries:
        table.add_row(
            entry.layer,
            _display_cache_status(cache_entry_status(entry)),
            entry.provider,
            entry.cache_key,
            entry.market,
            _display_session_state(entry.session_state),
            entry.expires_at.isoformat(timespec="seconds"),
            str(entry.row_count),
        )
    console.print(table)
    console.print("缓存明细")
    for entry in entries:
        console.print(
            f"- {entry.cache_key} 状态: {_display_cache_status(cache_entry_status(entry))} "
            f"数据源: {entry.provider} 过期: {entry.expires_at.isoformat(timespec='seconds')}"
        )


@discover_app.command("today")
def discover_today(
    markets: Annotated[
        str,
        typer.Option("--markets", help="用英文逗号分隔市场: us,hk,cn。"),
    ] = "us,hk,cn",
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="发现缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """在输入证券代码前生成适合新手的市场发现报告。"""
    try:
        selected_markets = parse_markets(markets)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(code=1) from error
    console.print(
        "正在准备市场级新闻，并调用 LLM 分析美股、港股和 A 股市场，请稍候...",
        soft_wrap=True,
    )
    try:
        report = build_today_discovery_report(selected_markets, output_dir=output_dir)
    except (
        DiscoveryDataRequiredError,
        DiscoveryLLMRequiredError,
        LLMProviderError,
    ) as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    output_path = write_discovery_report(report, output_dir)
    db_path = write_discovery_research_run(report, output_dir, output_path)
    console.print(f"今日市场发现已写入: {output_path}", soft_wrap=True)
    console.print(f"研究库已更新: {db_path}", soft_wrap=True)
    console.print(discovery_report_summary(report, output_dir=output_dir))


@discover_app.command("radar")
def discover_radar(
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多显示多少条机会雷达线索。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="发现缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """基于本地行情和新闻缓存扫描异常研究线索。"""
    report = build_opportunity_radar(output_dir=output_dir, limit=limit)
    output_path = write_opportunity_radar_report(report, output_dir)
    console.print(f"机会雷达已写入: {output_path}", soft_wrap=True)
    _print_opportunity_radar(report)


@research_app.command("queue")
def research_queue(
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态过滤，例如 new。"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多显示多少个候选。"),
    ] = 20,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """查看本地研究队列。"""
    queue = list_research_queue(output_dir, status=status, limit=limit)
    if not queue:
        console.print("研究队列为空。请先运行 `lychee discover today`。")
        return

    table = Table(title="Lychee AlphaDesk 研究队列")
    table.add_column("状态")
    table.add_column("市场")
    table.add_column("名称", overflow="fold")
    table.add_column("代码")
    table.add_column("主题", overflow="fold")
    table.add_column("置信度")
    table.add_column("证据")
    table.add_column("下一步")
    for item in queue:
        table.add_row(
            item.status,
            item.market,
            item.display_name,
            item.symbol or "-",
            item.related_theme,
            item.confidence,
            str(len(item.evidence)),
            str(len(item.next_actions)),
        )
    console.print(table)
    console.print("研究队列明细")
    for item in queue:
        symbol = item.symbol or "-"
        console.print(
            f"- {item.display_name} ({symbol}) [{item.market}] "
            f"主题: {item.related_theme} 状态: {item.status}"
        )


@research_app.command("deepen")
def research_deepen(
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多生成多少个研究深挖包。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """从研究队列生成可审计的二阶段研究深挖包。"""
    result = deepen_research_queue(output_dir=output_dir, status=status, limit=limit)
    if not result.packets:
        console.print("研究队列为空。请先运行 `lychee discover today`。")
        return

    if result.artifact_path is not None:
        console.print(f"研究深挖包已写入: {result.artifact_path}", soft_wrap=True)
    console.print(f"研究库已更新: {result.db_path}", soft_wrap=True)
    table = Table(title="Lychee AlphaDesk 研究深挖包")
    table.add_column("市场")
    table.add_column("名称", overflow="fold")
    table.add_column("代码")
    table.add_column("代理", overflow="fold")
    table.add_column("证据")
    table.add_column("缺口")
    table.add_column("下一步")
    for packet in result.packets:
        payload = packet.packet
        evidence_ids = payload.get("evidence_ids")
        data_gaps = payload.get("data_gaps")
        next_actions = payload.get("next_actions")
        proxy_symbols = _packet_proxy_symbols(payload)
        table.add_row(
            packet.market,
            packet.display_name,
            packet.symbol or "-",
            _display_values(proxy_symbols),
            _display_count(evidence_ids),
            _display_count(data_gaps),
            _display_count(next_actions),
        )
    console.print(table)
    console.print("研究深挖明细")
    for packet in result.packets:
        payload = packet.packet
        evidence_ids = payload.get("evidence_ids")
        data_gaps = payload.get("data_gaps")
        proxy_symbols = _packet_proxy_symbols(payload)
        console.print(
            f"- {packet.display_name} ({packet.symbol or '-'}) [{packet.market}] "
            f"证据: {_display_values(evidence_ids)} "
            f"代理: {_display_values(proxy_symbols)} "
            f"缺口: {_display_values(data_gaps)}",
            soft_wrap=True,
        )
    console.print(beginner_research_brief(result.packets), soft_wrap=True)


@research_app.command("fill-gaps")
def research_fill_gaps(
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多处理多少个研究候选。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
    force: Annotated[
        bool,
        typer.Option("--force", help="忽略已有缓存，强制重新拉取可自动补齐的数据。"),
    ] = False,
) -> None:
    """根据研究深挖包缺口自动补齐可拉取的数据。"""
    fill_result = fill_research_data_gaps(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=force,
    )
    if fill_result.candidates_checked == 0:
        console.print("研究队列为空。请先运行 `lychee discover today`。")
        return

    console.print(f"自动补数据完成，已检查候选: {fill_result.candidates_checked}")
    table = Table(title="Lychee AlphaDesk 自动补缺口")
    table.add_column("动作")
    table.add_column("状态")
    table.add_column("代码", overflow="fold")
    table.add_column("行数")
    table.add_column("说明", overflow="fold")
    for action in fill_result.actions:
        table.add_row(
            _display_gap_action_type(action.action_type),
            _display_gap_action_status(action.status),
            _display_values(action.symbols),
            str(action.count),
            action.message,
        )
    console.print(table)
    for warning in fill_result.warnings:
        console.print(f"警告: {warning}", soft_wrap=True)

    deepen_result = deepen_research_queue(output_dir=output_dir, status=status, limit=limit)
    if deepen_result.artifact_path is not None:
        console.print(
            f"补齐后研究深挖包已写入: {deepen_result.artifact_path}",
            soft_wrap=True,
        )


@research_app.command("check")
def research_check(
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多检查多少个研究候选。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
    force: Annotated[
        bool,
        typer.Option("--force", help="忽略已有缓存，强制重新拉取可自动补齐的数据。"),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="未达标时以非零退出码结束，供 agent/CI 使用。"),
    ] = False,
) -> None:
    """自动补缺、重新深挖并检查工作台是否达到研究入口要求。"""
    result = run_workbench_check(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=force,
    )
    _print_workbench_check(result)
    if strict and not result.is_ready:
        raise typer.Exit(code=1)


@research_app.command("detail")
def research_detail(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码选择研究任务，例如 STX、QQQ、0700.HK。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称选择研究任务，例如 Seagate。"),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多检查多少个研究候选。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
    force: Annotated[
        bool,
        typer.Option("--force", help="忽略已有缓存，强制重新拉取可自动补齐的数据。"),
    ] = False,
) -> None:
    """输出单条研究任务的工作台详情。"""
    result = run_workbench_check(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=force,
    )
    if not result.candidates:
        console.print("研究队列为空。请先运行 `lychee discover today`。")
        return
    selected_index = select_research_candidate_index(result, symbol=symbol, name=name)
    if selected_index is None:
        console.print("没有找到匹配的研究任务。")
        console.print("可选任务:")
        for candidate in result.candidates:
            console.print(
                f"- {candidate.display_name} | 入口: {candidate.observation_entry}",
                soft_wrap=True,
            )
        raise typer.Exit(code=1)
    candidate = result.candidates[selected_index]
    packet = (
        result.deepen_result.packets[selected_index]
        if selected_index < len(result.deepen_result.packets)
        else None
    )
    console.print(render_research_task_detail(candidate, packet), soft_wrap=True)


@research_app.command("run")
def research_run(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码选择研究任务，例如 STX、QQQ、0700.HK。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称选择研究任务，例如 Seagate。"),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多检查多少个研究候选。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
    force: Annotated[
        bool,
        typer.Option("--force", help="忽略已有缓存，强制刷新本任务相关数据。"),
    ] = False,
) -> None:
    """执行单条研究任务的数据刷新链，并输出更新后的研究任务面板。"""
    try:
        result = run_research_task(
            output_dir=output_dir,
            symbol=symbol,
            name=name,
            status=status,
            limit=limit,
            force=force,
        )
    except ValueError as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    _print_research_run(result)


@research_app.command("verify")
def research_verify(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码选择研究任务，例如 STX、QQQ、0700.HK。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称选择研究任务，例如 Seagate。"),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多检查多少个研究候选。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """对单条研究任务生成下钻核验清单。"""
    try:
        result = verify_research_task(
            output_dir=output_dir,
            symbol=symbol,
            name=name,
            status=status,
            limit=limit,
        )
    except ValueError as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    _print_research_verification(result)


@research_app.command("review")
def research_review(
    verdict: Annotated[
        str,
        typer.Option(
            "--verdict",
            help="研究流程判断: continue_research, needs_more_evidence, pause_watch, blocked。",
        ),
    ],
    note: Annotated[
        str,
        typer.Option("--note", help="人工或 agent 复核备注。"),
    ] = "",
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码选择研究任务，例如 STX、QQQ、0700.HK。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称选择研究任务，例如 Seagate。"),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多检查多少个研究候选。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """记录一次研究复核判断，不产生买卖建议。"""
    try:
        result = record_research_review(
            output_dir=output_dir,
            symbol=symbol,
            name=name,
            status=status,
            limit=limit,
            verdict=verdict,
            note=note,
        )
    except ValueError as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    _print_research_review(result)


@research_app.command("evidence-review")
def research_evidence_review(
    evidence_text: Annotated[
        str,
        typer.Option("--text", help="要复核的新闻标题或证据文本片段。"),
    ],
    verdict: Annotated[
        str,
        typer.Option("--verdict", help="证据方向: support, reverse, irrelevant。"),
    ],
    note: Annotated[
        str,
        typer.Option("--note", help="人工或 agent 对该证据方向的备注。"),
    ] = "",
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码选择研究任务，例如 STX、QQQ、0700.HK。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称选择研究任务，例如 Seagate。"),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多检查多少个研究候选。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """记录单条证据的方向复核，不产生买卖建议。"""
    try:
        result = record_research_evidence_review(
            output_dir=output_dir,
            symbol=symbol,
            name=name,
            status=status,
            limit=limit,
            evidence_text=evidence_text,
            verdict=verdict,
            note=note,
        )
    except ValueError as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    _print_research_evidence_review(result)


@research_app.command("reviews")
def research_reviews(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码过滤复核历史，例如 STX、QQQ。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称过滤复核历史。"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多显示多少条复核记录。"),
    ] = 20,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """查看研究复核历史。"""
    records = list_research_reviews(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    if not records:
        console.print("暂无研究复核历史。请先运行 `lychee research review`。")
        return
    table = Table(title="Lychee AlphaDesk 研究复核历史")
    table.add_column("时间")
    table.add_column("名称", overflow="fold")
    table.add_column("代码")
    table.add_column("市场")
    table.add_column("复核判断")
    table.add_column("证据")
    table.add_column("备注", overflow="fold")
    for record in records:
        table.add_row(
            record.created_at,
            record.display_name,
            record.symbol or "-",
            record.market,
            record.verdict_label,
            (
                f"支持 {record.support_count} | "
                f"风险 {record.risk_count} | "
                f"待补 {record.missing_count}"
            ),
            record.note,
        )
    console.print(table)
    console.print("复核记录明细")
    for record in records:
        console.print(
            f"- {record.display_name} ({record.symbol or '-'}) [{record.market}] "
            f"{record.verdict_label}: {record.note}",
            soft_wrap=True,
        )
        console.print(
            f"  证据: 支持 {record.support_count} | 风险 {record.risk_count} | "
            f"待补 {record.missing_count}",
            soft_wrap=True,
        )
        console.print(f"  记录: {record.review_path}", soft_wrap=True)
        console.print(f"  下钻核验: {record.verification_path}", soft_wrap=True)
    console.print("边界: 研究复核历史不是买卖建议。", soft_wrap=True)


@research_app.command("evidence-reviews")
def research_evidence_reviews(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码过滤证据复核历史，例如 STX、QQQ。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称过滤证据复核历史。"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多显示多少条证据复核记录。"),
    ] = 20,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """查看单条证据方向复核历史。"""
    records = list_research_evidence_reviews(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    if not records:
        console.print("暂无证据复核历史。请先运行 `lychee research evidence-review`。")
        return
    table = Table(title="Lychee AlphaDesk 证据复核历史")
    table.add_column("时间")
    table.add_column("名称", overflow="fold")
    table.add_column("代码")
    table.add_column("市场")
    table.add_column("复核方向")
    table.add_column("证据文本", overflow="fold")
    table.add_column("备注", overflow="fold")
    for record in records:
        table.add_row(
            record.created_at,
            record.display_name,
            record.symbol or "-",
            record.market,
            record.verdict_label,
            record.evidence_text,
            record.note,
        )
    console.print(table)
    console.print("证据复核明细")
    for record in records:
        console.print(
            f"- {record.display_name} ({record.symbol or '-'}) [{record.market}] "
            f"{record.verdict_label}",
            soft_wrap=True,
        )
        console.print(f"  证据文本: {record.evidence_text}", soft_wrap=True)
        console.print(f"  备注: {record.note}", soft_wrap=True)
        console.print(f"  记录: {record.review_path}", soft_wrap=True)
    console.print("边界: 单条证据复核历史不是买卖建议。", soft_wrap=True)


@research_app.command("pending-evidence")
def research_pending_evidence(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="只查看某个证券代码的待判定证据。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="只查看某个研究任务名称的待判定证据。"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多显示多少条待判定证据。"),
    ] = 50,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """查看仍需人工判定方向的新闻证据队列。"""
    items = list_pending_evidence_reviews(output_dir=output_dir, limit=limit)
    items = _filter_pending_evidence_items(items, symbol=symbol, name=name)
    if not items:
        console.print(
            "暂无待判定证据。若刚运行过 `lychee research verify`，"
            "说明当前没有需要人工分类的新闻；可查看证据板的“离题/已过滤”，"
            "或重新下钻核验。",
            soft_wrap=True,
        )
        return
    table = Table(title="Lychee AlphaDesk 待判定证据队列")
    table.add_column("时间")
    table.add_column("名称", overflow="fold")
    table.add_column("代码")
    table.add_column("市场")
    table.add_column("系统建议")
    table.add_column("证据文本", overflow="fold")
    for item in items:
        table.add_row(
            item.created_at,
            item.display_name,
            item.symbol or "-",
            item.market,
            item.suggested_verdict_label,
            item.evidence_text,
        )
    console.print(table)
    console.print("待处理明细")
    for item in items:
        console.print(
            f"- {item.display_name} ({item.symbol or '-'}) [{item.market}]",
            soft_wrap=True,
        )
        console.print(f"  要回答的问题: {item.primary_question}", soft_wrap=True)
        console.print(f"  待判定证据: {item.evidence_text}", soft_wrap=True)
        console.print(
            f"  系统建议: {item.suggested_verdict_label} | {item.suggested_reason}",
            soft_wrap=True,
        )
        console.print(f"  下钻核验: {item.artifact_path}", soft_wrap=True)
        console.print(f"  复核命令: {item.review_command}", soft_wrap=True)
    console.print("边界: 待判定证据队列不是买卖建议。", soft_wrap=True)


@research_app.command("memo")
def research_memo(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码选择研究任务，例如 STX、QQQ、0700.HK。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称选择研究任务，例如 Seagate。"),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="按研究状态选择候选；默认处理 new。"),
    ] = "new",
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多检查多少个研究候选。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """调用 LLM 为单条研究任务生成证据备忘录和反方审查。"""
    console.print("正在调用 LLM 生成研究备忘录，请稍候...", soft_wrap=True)
    try:
        result = generate_research_memo(
            output_dir=output_dir,
            symbol=symbol,
            name=name,
            status=status,
            limit=limit,
        )
    except (LLMProviderError, ValueError) as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    _print_research_memo(result)


@research_app.command("memos")
def research_memos(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码过滤备忘录历史，例如 STX、QQQ。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称过滤备忘录历史。"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多显示多少条备忘录。"),
    ] = 20,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """查看 LLM 研究备忘录历史。"""
    records = list_research_memos(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    if not records:
        console.print("暂无研究备忘录历史。请先运行 `lychee research memo`。")
        return
    table = Table(title="Lychee AlphaDesk 研究备忘录历史")
    table.add_column("时间")
    table.add_column("名称", overflow="fold")
    table.add_column("代码")
    table.add_column("市场")
    table.add_column("置信度")
    table.add_column("证据")
    table.add_column("摘要", overflow="fold")
    for record in records:
        table.add_row(
            record.created_at,
            record.display_name,
            record.symbol or "-",
            record.market,
            record.confidence,
            (
                f"支持 {record.support_count} | "
                f"反方 {record.skeptic_count} | "
                f"待补 {record.missing_count} | "
                f"下一步 {record.next_step_count}"
            ),
            record.summary,
        )
    console.print(table)
    console.print("备忘录明细")
    for record in records:
        console.print(
            f"- {record.display_name} ({record.symbol or '-'}) [{record.market}] "
            f"置信度: {record.confidence}",
            soft_wrap=True,
        )
        console.print(f"  摘要: {record.summary}", soft_wrap=True)
        console.print(
            f"  证据: 支持 {record.support_count} | 反方 {record.skeptic_count} | "
            f"待补 {record.missing_count} | 下一步 {record.next_step_count}",
            soft_wrap=True,
        )
        console.print(f"  记录: {record.memo_path}", soft_wrap=True)
        console.print(f"  下钻核验: {record.verification_path}", soft_wrap=True)
    console.print("边界: 研究备忘录历史不是买卖建议。", soft_wrap=True)


@research_app.command("data-requests")
def research_data_requests(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码过滤数据请求，例如 QQQ、STX。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称过滤数据请求。"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多扫描多少条最新研究备忘录。"),
    ] = 20,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """查看 LLM 研究备忘录提出的下一批数据请求。"""
    requests = list_research_data_requests(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    _print_research_data_requests(requests)


@research_app.command("next")
def research_next(
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多展示多少条下一步行动。"),
    ] = 10,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """查看统一的下一步研究行动队列。"""
    queue = build_action_queue(output_dir, limit=limit)
    _print_action_queue(queue)


@research_app.command("provider-backlog")
def research_provider_backlog(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码过滤数据源缺口，例如 QQQ、STX。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称过滤数据源缺口。"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多扫描多少条最新研究备忘录。"),
    ] = 20,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """查看当前 provider 还不能自动满足的数据能力缺口。"""
    backlog = list_provider_backlog_items(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    _print_provider_backlog(backlog)


@research_app.command("run-data-request")
def research_run_data_request(
    request_index: Annotated[
        int,
        typer.Option("--request", help="要执行的数据请求序号，从 1 开始。"),
    ] = 1,
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="按证券代码过滤数据请求，例如 QQQ、STX。"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="按任务名称过滤数据请求。"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="最多扫描多少条最新研究备忘录。"),
    ] = 20,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="研究库所在输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
    force: Annotated[
        bool,
        typer.Option("--force/--no-force", help="执行可自动拉取的数据时是否强制刷新。"),
    ] = True,
) -> None:
    """执行一条研究数据请求中可自动完成的补数据动作。"""
    try:
        result = fulfill_research_data_request(
            output_dir,
            request_index=request_index,
            symbol=symbol,
            name=name,
            limit=limit,
            force=force,
        )
    except ValueError as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    _print_research_data_request_fulfillment(result)


@data_pull_app.command("market")
def data_pull_market(
    symbols: Annotated[
        str,
        typer.Option("--symbols", help="用英文逗号分隔证券代码，例如 AAPL,TSLA,0700.HK。"),
    ],
    provider: Annotated[
        str,
        typer.Option("--provider", help="行情数据源。"),
    ] = "alpha_vantage",
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="实时缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
    force: Annotated[
        bool,
        typer.Option("--force", help="忽略保质期和交易时段缓存策略，强制刷新行情。"),
    ] = False,
) -> None:
    """拉取最新日线行情到本地缓存。"""
    try:
        result = pull_market_prices(
            symbols=parse_symbols(symbols),
            output_dir=output_dir,
            provider_id=provider,
            force=force,
        )
    except (RuntimeError, ValueError) as error:
        console.print(str(error))
        raise typer.Exit(code=1) from error
    _print_pull_result(result_label="行情", count=result.count, result=result)


@data_pull_app.command("news")
def data_pull_news(
    symbols: Annotated[
        str,
        typer.Option("--symbols", help="用英文逗号分隔证券代码；留空则拉取市场级新闻。"),
    ] = "",
    query: Annotated[
        str,
        typer.Option("--query", help="主题关键词查询；用于按研究主题补强新闻证据。"),
    ] = "",
    provider: Annotated[
        str,
        typer.Option("--provider", help="新闻数据源: auto, marketaux, finnhub, newsapi。"),
    ] = "auto",
    start_date: Annotated[
        str | None,
        typer.Option("--from", help="开始日期 YYYY-MM-DD。"),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--to", help="结束日期 YYYY-MM-DD。"),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="实时缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
    force: Annotated[
        bool,
        typer.Option("--force", help="忽略保质期，强制刷新新闻。"),
    ] = False,
) -> None:
    """拉取市场新闻到本地缓存。"""
    try:
        result = pull_news_events(
            symbols=parse_symbols(symbols),
            query=query or None,
            output_dir=output_dir,
            provider_id=provider,
            start_date=start_date,
            end_date=end_date,
            force=force,
        )
    except (RuntimeError, ValueError) as error:
        console.print(str(error))
        raise typer.Exit(code=1) from error
    _print_pull_result(result_label="新闻事件", count=result.count, result=result)


@data_pull_app.command("filings")
def data_pull_filings(
    symbols: Annotated[
        str,
        typer.Option("--symbols", help="用英文逗号分隔美股代码，例如 AAPL,TSLA。"),
    ],
    limit: Annotated[
        int,
        typer.Option("--limit", help="每个代码最多拉取的近期 SEC 公告数量。"),
    ] = 5,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="实时缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """拉取近期 SEC EDGAR 公告到本地缓存。"""
    try:
        result = pull_sec_filings(
            symbols=parse_symbols(symbols),
            output_dir=output_dir,
            limit_per_symbol=limit,
        )
    except (RuntimeError, ValueError) as error:
        console.print(str(error))
        raise typer.Exit(code=1) from error
    _print_pull_result(result_label="公告", count=result.count, result=result)


@data_set_app.command("fund")
def data_set_fund(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="基金或 ETF 代码，例如 2800.HK、510300.SH。"),
    ] = None,
    display_name: Annotated[
        str | None,
        typer.Option("--name", help="基金或 ETF 显示名称。"),
    ] = None,
    source_url: Annotated[
        str | None,
        typer.Option("--source-url", help="资料来源 URL。"),
    ] = None,
    from_file: Annotated[
        Path | None,
        typer.Option("--from-file", help="从 data guide fund 生成并填写后的 JSON 模板导入。"),
    ] = None,
    market: Annotated[
        str,
        typer.Option("--market", help="市场，例如 US、HK、CN。"),
    ] = "",
    tracking_index: Annotated[
        str,
        typer.Option("--tracking-index", help="跟踪指数或基准。"),
    ] = "",
    expense_ratio: Annotated[
        str,
        typer.Option("--expense-ratio", help="费用率或管理费说明。"),
    ] = "",
    holdings_summary: Annotated[
        str,
        typer.Option("--holdings-summary", help="成分或持仓摘要。"),
    ] = "",
    as_of: Annotated[
        str,
        typer.Option("--as-of", help="资料日期 YYYY-MM-DD。"),
    ] = "",
    provider: Annotated[
        str,
        typer.Option("--provider", help="资料来源类型，默认 manual。"),
    ] = "manual",
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="实时缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """写入基金/ETF 元资料缓存，用于代理标的核验。"""
    try:
        if from_file is not None:
            result = write_fund_metadata_cache_from_file(
                output_dir=output_dir,
                guide_path=from_file,
            )
            symbol_text = _fund_symbol_from_result_path(from_file)
        else:
            if symbol is None or display_name is None or source_url is None:
                raise ValueError(
                    "请提供 --symbol、--name、--source-url，或使用 --from-file 导入已填写模板。"
                )
            result = write_fund_metadata_cache(
                output_dir=output_dir,
                symbol=symbol,
                display_name=display_name,
                market=market,
                tracking_index=tracking_index,
                expense_ratio=expense_ratio,
                holdings_summary=holdings_summary,
                source_url=source_url,
                as_of=as_of,
                provider=provider,
            )
            symbol_text = symbol.strip().upper()
    except ValueError as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    console.print(f"基金资料已写入: {symbol_text}")
    _print_pull_result(result_label="基金资料", count=result.count, result=result)


@data_set_app.command("metric")
def data_set_metric(
    symbol: Annotated[
        str,
        typer.Option("--symbol", help="指标对应的证券代码，例如 QQQ、STX、0700.HK。"),
    ],
    domain: Annotated[
        str,
        typer.Option(
            "--domain",
            help="指标领域，例如 market_breadth、volatility_metrics、fund_flows。",
        ),
    ],
    name: Annotated[
        str,
        typer.Option("--name", help="指标名称，例如 纳斯达克100上涨家数。"),
    ],
    value: Annotated[
        str,
        typer.Option("--value", help="核验后的指标读数，例如 63/100。"),
    ],
    source_url: Annotated[
        str,
        typer.Option("--source-url", help="资料来源 URL。"),
    ],
    as_of: Annotated[
        str,
        typer.Option("--as-of", help="指标日期 YYYY-MM-DD。"),
    ] = "",
    note: Annotated[
        str,
        typer.Option("--note", help="人工备注，说明指标如何使用。"),
    ] = "",
    provider: Annotated[
        str,
        typer.Option("--provider", help="资料来源类型，默认 manual。"),
    ] = "manual",
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="实时缓存输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """写入人工核验过的研究指标缓存，用于补充工作台证据。"""
    try:
        result = write_research_metric_cache(
            output_dir=output_dir,
            symbol=symbol,
            domain=domain,
            name=name,
            value=value,
            as_of=as_of,
            source_url=source_url,
            note=note,
            provider=provider,
        )
    except ValueError as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    console.print(f"研究指标已写入: {symbol.strip().upper()} {domain.strip().lower()}")
    _print_pull_result(result_label="研究指标", count=result.count, result=result)


def _fund_symbol_from_result_path(path: Path) -> str:
    return (
        path.stem.removeprefix("fund-metadata-guide-").strip().upper()
        or path.stem.upper()
    )


@data_guide_app.command("fund")
def data_guide_fund(
    symbol: Annotated[
        str,
        typer.Option("--symbol", help="基金或 ETF 代码，例如 2800.HK、510300.SH。"),
    ],
    display_name: Annotated[
        str,
        typer.Option("--name", help="基金或 ETF 显示名称。"),
    ],
    market: Annotated[
        str,
        typer.Option("--market", help="市场，例如 US、HK、CN。"),
    ] = "",
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="向导输出目录。"),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """生成基金/ETF 资料补齐向导，不写入未经核验的数据。"""
    try:
        guide = write_fund_metadata_guide(
            output_dir=output_dir,
            symbol=symbol,
            display_name=display_name,
            market=market,
        )
    except ValueError as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    console.print("基金资料补齐向导")
    console.print(f"标的: {guide.display_name} ({guide.symbol}) [{guide.market}]")
    console.print(f"模板已写入: {guide.output_path}", soft_wrap=True)
    console.print("先查这些资料")
    console.print("- 跟踪指数或基准")
    console.print("- 费用率或管理费说明")
    console.print("- 成分或持仓摘要")
    console.print("- 资料来源 URL")
    console.print("建议来源")
    for source in guide.suggested_sources:
        console.print(f"- {source}")
    console.print("填完模板后导入")
    console.print(guide.apply_command, soft_wrap=True)
    console.print("查完后写入")
    console.print(guide.write_command, soft_wrap=True)
    console.print("边界: 向导只生成模板，不会猜测基金资料，也不是投资建议。")


def _print_pull_result(*, result_label: str, count: int, result: PullResult) -> None:
    provider = result.provider
    output_path = result.output_path
    warnings = result.warnings
    action = "已拉取" if result.refreshed else "已使用缓存"
    console.print(f"{action}{result_label}: {count}")
    console.print(f"数据源: {provider}")
    console.print(f"缓存: {output_path}", soft_wrap=True)
    for warning in warnings:
        console.print(f"警告: {warning}", soft_wrap=True)


def _display_mode(mode: str) -> str:
    return {"demo": "演示", "live": "实时"}.get(mode, mode)


def _display_status(status: str) -> str:
    return {"pass": "通过", "warning": "警告", "error": "错误"}.get(status, status)


def _display_cache_status(status: str) -> str:
    return {
        "fresh": "有效",
        "expired": "过期",
        "final_for_session": "收盘确认",
        "failed": "失败",
    }.get(status, status)


def _display_session_state(state: str) -> str:
    return {
        "pre_open": "开盘前",
        "open": "交易中",
        "lunch_break": "午休",
        "post_close_refresh": "收盘确认窗口",
        "closed": "收盘",
        "weekend": "周末",
        "ttl": "保质期",
    }.get(state, state)


def _display_count(value: object) -> str:
    return str(len(value)) if isinstance(value, list) else "0"


def _display_values(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "-"
    separator = "；" if any("。" in str(item) or "缺少" in str(item) for item in value) else ", "
    if separator == "；":
        return separator.join(str(item).rstrip("。") for item in value) + "。"
    return separator.join(str(item) for item in value)


def _packet_proxy_symbols(payload: dict[str, object]) -> list[str]:
    local_data = payload.get("local_data")
    if not isinstance(local_data, dict):
        return []
    symbol_mapping = local_data.get("symbol_mapping")
    if not isinstance(symbol_mapping, list):
        return []
    symbols: list[str] = []
    for row in symbol_mapping:
        if isinstance(row, dict) and isinstance(row.get("symbol"), str):
            symbols.append(row["symbol"])
    return symbols


def _print_workbench_check(result: WorkbenchCheckResult) -> None:
    if result.artifact_path is not None:
        console.print(f"工作台自检报告已写入: {result.artifact_path}", soft_wrap=True)
    console.print(f"状态: {_display_workbench_status(result.status)}")
    table = Table(title="Lychee AlphaDesk 工作台自检")
    table.add_column("检查项")
    table.add_column("状态")
    table.add_column("说明", overflow="fold")
    for gate in result.gates:
        table.add_row(gate.name, _display_workbench_gate_status(gate.status), gate.detail)
    console.print(table)
    console.print(result.beginner_brief, soft_wrap=True)


def _print_research_run(result: ResearchRunResult) -> None:
    console.print(f"研究执行记录已写入: {result.artifact_path}", soft_wrap=True)
    console.print(f"状态: {_display_research_run_status(result.status)}")
    table = Table(title="Lychee AlphaDesk 研究执行链")
    table.add_column("动作")
    table.add_column("状态")
    table.add_column("代码", overflow="fold")
    table.add_column("行数")
    table.add_column("说明", overflow="fold")
    for action in result.actions:
        table.add_row(
            _display_research_action_type(action.action_type),
            _display_research_action_status(action.status),
            _display_values(action.symbols),
            str(action.count),
            action.message,
        )
    console.print(table)
    for action in result.actions:
        for warning in action.warnings:
            console.print(f"警告: {warning}", soft_wrap=True)
    console.print(result.detail, soft_wrap=True)


def _print_research_verification(result: ResearchVerificationResult) -> None:
    console.print(f"下钻核验记录已写入: {result.artifact_path}", soft_wrap=True)
    console.print(f"下钻核验: {result.candidate.display_name} [{result.candidate.market}]")
    console.print(f"一致性结论: {result.status_label}")
    table = Table(title="Lychee AlphaDesk 下钻核验")
    table.add_column("核验项")
    table.add_column("状态")
    table.add_column("说明", overflow="fold")
    for check in result.checks:
        table.add_row(
            check.name,
            _display_verification_check_status(check.status),
            check.detail,
        )
    console.print(table)
    console.print("证据板")
    _print_evidence_board_column("支持证据", result.evidence_board["support"])
    _print_evidence_board_column("风险/反向待查", result.evidence_board["risk"])
    _print_evidence_board_column(
        "离题/已过滤",
        result.evidence_board.get("off_topic", []),
    )
    _print_evidence_board_column("待补证据", result.evidence_board["missing"])
    _print_research_evidence_change(result)
    _print_research_decision_board(result)
    _print_pending_evidence_review_commands(result)
    console.print(result.conclusion, soft_wrap=True)
    console.print("下一步")
    for action in result.next_actions:
        console.print(f"- {action}", soft_wrap=True)
    console.print("边界: 下钻核验不是买卖建议。", soft_wrap=True)


def _print_pending_evidence_review_commands(
    result: ResearchVerificationResult,
    *,
    limit: int = 5,
) -> None:
    pending_items = _pending_evidence_review_texts(result)[:limit]
    if not pending_items:
        return
    selector = _research_selector(result.candidate.symbol, result.candidate.display_name)
    console.print("待判定证据处理")
    console.print(
        f"- 查看队列: lychee research pending-evidence {selector}",
        soft_wrap=True,
    )
    for evidence_text in pending_items:
        verdict, reason = suggest_pending_evidence_review(
            evidence_text,
            primary_question=result.decision_board.primary_question,
        )
        verdict_label = RESEARCH_EVIDENCE_REVIEW_VERDICTS[verdict]
        console.print(
            f"- 系统建议: {verdict_label} | {reason}",
            soft_wrap=True,
        )
        console.print(
            "- 复核命令: "
            f"lychee research evidence-review {selector} "
            f"--text {_quote_cli_value(evidence_text)} "
            f"--verdict {verdict} --note {_quote_cli_value(reason)}",
            soft_wrap=True,
        )
    console.print(
        f"- 分类后重新运行: lychee research verify {selector}",
        soft_wrap=True,
    )
    console.print("边界: 待判定证据处理不是买卖建议。", soft_wrap=True)


def _filter_pending_evidence_items(
    items: list[PendingEvidenceReviewItem],
    *,
    symbol: str | None,
    name: str | None,
) -> list[PendingEvidenceReviewItem]:
    if symbol:
        target = symbol.strip().upper()
        items = [
            item
            for item in items
            if item.symbol is not None and item.symbol.upper() == target
        ]
    if name:
        target_name = name.strip().lower()
        items = [
            item
            for item in items
            if item.display_name.lower() == target_name
            or target_name in item.display_name.lower()
        ]
    return items


def _pending_evidence_review_texts(
    result: ResearchVerificationResult,
) -> list[str]:
    items: list[str] = []
    prefix = "新闻待判定: "
    suffix = " 命中主题但方向未明。"
    for row in result.evidence_board["risk"]:
        if not row.startswith(prefix):
            continue
        evidence_text = row.removeprefix(prefix)
        if evidence_text.endswith(suffix):
            evidence_text = evidence_text.removesuffix(suffix)
        evidence_text = evidence_text.strip()
        if evidence_text and evidence_text not in items:
            items.append(evidence_text)
    return items


def _research_selector(symbol: str | None, display_name: str) -> str:
    if symbol:
        return f"--symbol {symbol}"
    return f"--name {_quote_cli_value(display_name)}"


def _quote_cli_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _print_research_decision_board(result: ResearchVerificationResult) -> None:
    board = result.decision_board
    console.print("研究决策板")
    console.print(f"状态: {board.workflow_label}", soft_wrap=True)
    console.print(f"要回答的问题: {board.primary_question}", soft_wrap=True)
    console.print(f"判断规则: {board.decision_rule}", soft_wrap=True)
    console.print(
        f"建议记录: {board.suggested_verdict}（{board.suggested_verdict_label}）",
        soft_wrap=True,
    )
    console.print("工作台下一步")
    for step in board.next_steps:
        console.print(f"- {step}", soft_wrap=True)
    for command in board.next_commands:
        console.print(f"- 执行命令: {command}", soft_wrap=True)


def _print_research_evidence_change(result: ResearchVerificationResult) -> None:
    change = result.evidence_change
    console.print("证据变化")
    console.print(f"状态: {change.status_label}", soft_wrap=True)
    console.print(f"摘要: {change.summary}", soft_wrap=True)
    if change.previous_artifact_path:
        console.print(f"上一份核验: {change.previous_artifact_path}", soft_wrap=True)
    detail_groups = [
        (title, rows)
        for title, rows in research_evidence_change_detail_groups(change)
        if rows
    ]
    if detail_groups:
        console.print("证据变化明细")
        for title, rows in detail_groups:
            _print_evidence_board_column(title, rows[:5])


def _print_research_review(result: ResearchReviewResult) -> None:
    console.print(f"研究复核记录已写入: {result.artifact_path}", soft_wrap=True)
    console.print(f"研究库已更新: {result.db_path}", soft_wrap=True)
    console.print(
        f"复核任务: {result.verification.candidate.display_name} "
        f"[{result.verification.candidate.market}]",
        soft_wrap=True,
    )
    console.print(f"复核判断: {result.verdict_label}", soft_wrap=True)
    console.print(f"备注: {result.note}", soft_wrap=True)
    console.print(
        "证据数量: "
        f"支持 {result.evidence_counts['support']} | "
        f"风险/反向待查 {result.evidence_counts['risk']} | "
        f"离题/已过滤 {result.evidence_counts.get('off_topic', 0)} | "
        f"待补 {result.evidence_counts['missing']}",
        soft_wrap=True,
    )
    console.print("证据板")
    _print_evidence_board_column(
        "支持证据",
        result.verification.evidence_board["support"],
    )
    _print_evidence_board_column(
        "风险/反向待查",
        result.verification.evidence_board["risk"],
    )
    _print_evidence_board_column(
        "离题/已过滤",
        result.verification.evidence_board.get("off_topic", []),
    )
    _print_evidence_board_column(
        "待补证据",
        result.verification.evidence_board["missing"],
    )
    _print_research_review_next_steps(result)
    console.print("边界: 研究复核不是买卖建议。", soft_wrap=True)


def _print_research_review_next_steps(result: ResearchReviewResult) -> None:
    candidate = result.verification.candidate
    selector = _research_selector(candidate.symbol, candidate.display_name)
    console.print("工作台下一步")
    if result.verdict == "continue_research":
        console.print(
            f"- 生成研究备忘录: lychee research memo {selector}",
            soft_wrap=True,
        )
        console.print(
            f"- 重新下钻核验: lychee research verify {selector}",
            soft_wrap=True,
        )
    elif result.verdict == "needs_more_evidence":
        console.print(
            f"- 刷新并补强证据: lychee research run {selector} --force",
            soft_wrap=True,
        )
        console.print(
            f"- 重新下钻核验: lychee research verify {selector}",
            soft_wrap=True,
        )
    elif result.verdict == "pause_watch":
        console.print(
            f"- 保持观察并重新核验: lychee research verify {selector}",
            soft_wrap=True,
        )
    else:
        console.print(
            f"- 查看阻塞任务详情: lychee research detail {selector}",
            soft_wrap=True,
        )
    console.print(
        f"- 查看研究复核历史: lychee research reviews {selector}",
        soft_wrap=True,
    )


def _print_research_evidence_review(result: ResearchEvidenceReviewResult) -> None:
    console.print(f"证据复核记录已写入: {result.artifact_path}", soft_wrap=True)
    console.print(f"研究库已更新: {result.db_path}", soft_wrap=True)
    console.print(
        f"复核任务: {result.candidate.display_name} "
        f"[{result.candidate.market}]",
        soft_wrap=True,
    )
    console.print(f"证据文本: {result.evidence_text}", soft_wrap=True)
    console.print(f"复核方向: {result.verdict_label}", soft_wrap=True)
    console.print(f"备注: {result.note}", soft_wrap=True)
    _print_research_evidence_review_next_steps(result)
    console.print("边界: 单条证据复核不是买卖建议。", soft_wrap=True)


def _print_research_evidence_review_next_steps(
    result: ResearchEvidenceReviewResult,
) -> None:
    selector = _research_selector(result.candidate.symbol, result.candidate.display_name)
    console.print("工作台下一步")
    console.print(
        f"- 重新下钻核验: lychee research verify {selector}",
        soft_wrap=True,
    )
    console.print(
        f"- 继续处理待判定证据: lychee research pending-evidence {selector}",
        soft_wrap=True,
    )
    console.print(
        f"- 查看证据复核历史: lychee research evidence-reviews {selector}",
        soft_wrap=True,
    )


def _print_research_memo(result: ResearchMemoResult) -> None:
    memo = result.memo
    console.print(f"研究备忘录已写入: {result.artifact_path}", soft_wrap=True)
    console.print(f"研究库已更新: {result.db_path}", soft_wrap=True)
    console.print(f"研究任务: {result.candidate.display_name} [{result.candidate.market}]")
    console.print(f"置信度: {memo.confidence}")
    console.print("摘要")
    console.print(memo.summary, soft_wrap=True)
    console.print("工作假设")
    console.print(memo.working_hypothesis, soft_wrap=True)
    console.print("证据读数")
    console.print(memo.evidence_reading, soft_wrap=True)
    console.print("支持证据")
    for item in memo.support_points:
        console.print(f"- {item}", soft_wrap=True)
    console.print("反方审查")
    for item in memo.skeptic_review:
        console.print(f"- {item}", soft_wrap=True)
    console.print("反证检查")
    for item in memo.falsification_checks:
        console.print(f"- {item}", soft_wrap=True)
    console.print("待补证据")
    for item in memo.missing_evidence:
        console.print(f"- {item}", soft_wrap=True)
    console.print("下一批数据请求")
    for item in memo.next_data_requests:
        console.print(f"- {item}", soft_wrap=True)
    console.print("下一步研究动作")
    for item in memo.next_research_steps:
        console.print(f"- {item}", soft_wrap=True)
    _print_research_memo_next_steps(result)
    console.print("边界: 研究备忘录不是买卖建议。", soft_wrap=True)


def _print_research_memo_next_steps(result: ResearchMemoResult) -> None:
    selector = _research_selector(result.candidate.symbol, result.candidate.display_name)
    suggested_verdict = result.verification.decision_board.suggested_verdict
    verdict = (
        suggested_verdict
        if suggested_verdict in RESEARCH_REVIEW_VERDICTS
        else "needs_more_evidence"
    )
    verdict_label = RESEARCH_REVIEW_VERDICTS[verdict]
    note = _quote_cli_value(_research_memo_review_note(verdict))
    console.print("工作台下一步")
    console.print(
        f"- 查看数据请求队列: lychee research data-requests {selector}",
        soft_wrap=True,
    )
    console.print(f"- 工作台建议: {verdict_label}", soft_wrap=True)
    if verdict == "needs_more_evidence":
        console.print(
            f"- 刷新并补强证据: lychee research run {selector} --force",
            soft_wrap=True,
        )
    elif verdict == "blocked":
        console.print(
            f"- 查看阻塞任务详情: lychee research detail {selector}",
            soft_wrap=True,
        )
    console.print(
        "- 记录研究复核: "
        f"lychee research review {selector} "
        f"--verdict {verdict} --note {note}",
        soft_wrap=True,
    )
    console.print(
        f"- 重新下钻核验: lychee research verify {selector}",
        soft_wrap=True,
    )
    console.print(
        f"- 查看研究备忘录历史: lychee research memos {selector}",
        soft_wrap=True,
    )


def _research_memo_review_note(verdict: str) -> str:
    if verdict == "continue_research":
        return "备忘录已生成，继续人工一致性复核。"
    if verdict == "needs_more_evidence":
        return "备忘录显示证据仍需补强，继续刷新和复核。"
    if verdict == "blocked":
        return "备忘录生成后仍存在阻塞，先记录阻塞并补齐数据。"
    return "备忘录已生成，暂停观察并等待新证据。"


def _print_research_data_requests(requests: list[ResearchDataRequest]) -> None:
    if not requests:
        console.print("暂无研究数据请求。请先运行 `lychee research memo`。")
        return
    table = Table(title="Lychee AlphaDesk 研究数据请求")
    table.add_column("时间")
    table.add_column("名称", overflow="fold")
    table.add_column("代码")
    table.add_column("市场")
    table.add_column("置信度")
    table.add_column("请求", overflow="fold")
    table.add_column("命令数")
    for item in requests:
        table.add_row(
            item.created_at,
            item.display_name,
            item.symbol or "-",
            item.market,
            item.confidence,
            item.request_text,
            str(len(item.suggested_commands)),
        )
    console.print(table)
    console.print("数据请求明细")
    for index, item in enumerate(requests, start=1):
        console.print(
            f"{index}. {item.display_name} ({item.symbol or '-'}) [{item.market}]",
            soft_wrap=True,
        )
        console.print(f"   请求: {item.request_text}", soft_wrap=True)
        if research_data_request_needs_manual_source(item):
            console.print(
                "   说明: 这类数据当前没有自动补数据命令，需要人工补来源或等待插件接入。",
                soft_wrap=True,
            )
        console.print("   建议命令:")
        for command in item.suggested_commands:
            console.print(f"   - {command}", soft_wrap=True)
        console.print(
            f"   执行支持的动作: lychee research run-data-request --request {index} "
            f"{_research_selector(item.symbol, item.display_name)}",
            soft_wrap=True,
        )
        console.print(f"   来源备忘录: {item.memo_path}", soft_wrap=True)
        console.print(f"   下钻核验: {item.verification_path}", soft_wrap=True)
    console.print("边界: 数据请求队列只用于补证据，不是买卖建议。", soft_wrap=True)


def _print_provider_backlog(items: list[ProviderBacklogItem]) -> None:
    if not items:
        console.print("暂无数据源缺口。当前研究数据请求已有自动动作或暂无请求。")
        return
    table = Table(title="Lychee AlphaDesk 数据源缺口队列")
    table.add_column("时间")
    table.add_column("名称", overflow="fold")
    table.add_column("代码")
    table.add_column("市场")
    table.add_column("领域")
    table.add_column("插件类型")
    table.add_column("缺口", overflow="fold")
    for item in items:
        table.add_row(
            item.created_at,
            item.display_name,
            item.symbol or "-",
            item.market,
            item.data_domain,
            item.plugin_type,
            item.coverage_gap,
        )
    console.print(table)
    console.print("数据源缺口明细")
    for index, item in enumerate(items, start=1):
        console.print(
            f"{index}. {item.display_name} ({item.symbol or '-'}) [{item.market}]",
            soft_wrap=True,
        )
        console.print(f"   研究请求: {item.request_text}", soft_wrap=True)
        console.print(f"   数据领域: {item.data_domain}", soft_wrap=True)
        console.print(f"   插件类型: {item.plugin_type}", soft_wrap=True)
        console.print(f"   当前缺口: {item.coverage_gap}", soft_wrap=True)
        console.print("   候选来源形态:")
        for source in item.suggested_provider_examples:
            console.print(f"   - {source}", soft_wrap=True)
        console.print("   建议命令:")
        for command in item.suggested_commands:
            console.print(f"   - {command}", soft_wrap=True)
        console.print(f"   下一步: {item.next_step}", soft_wrap=True)
        console.print(f"   来源备忘录: {item.memo_path}", soft_wrap=True)
        console.print(f"   下钻核验: {item.verification_path}", soft_wrap=True)
    console.print("边界: 数据源缺口队列只用于规划补数据能力，不是买卖建议。", soft_wrap=True)


def _print_action_queue(items: list[ActionQueueItem]) -> None:
    if not items:
        console.print(
            "暂无下一步行动。请先运行 `lychee discover today` 或 `lychee research check`。"
        )
        return
    table = Table(title="Lychee AlphaDesk 下一步行动队列")
    table.add_column("优先级")
    table.add_column("区域")
    table.add_column("行动", overflow="fold")
    table.add_column("命令", overflow="fold")
    for index, item in enumerate(items, start=1):
        table.add_row(str(index), item.area, item.title, item.command)
    console.print(table)
    console.print("行动明细")
    for index, item in enumerate(items, start=1):
        console.print(f"{index}. [{item.area}] {item.title}", soft_wrap=True)
        console.print(f"   为什么: {item.detail}", soft_wrap=True)
        console.print(f"   执行: {item.command}", soft_wrap=True)
        console.print(f"   来源: {item.source}", soft_wrap=True)
    console.print("边界: 行动队列只推进研究流程，不是买卖建议。", soft_wrap=True)


def _print_opportunity_radar(report: OpportunityRadarReport) -> None:
    console.print("Lychee AlphaDesk 机会雷达")
    console.print(f"状态: {report.status}")
    console.print(report.disclaimer, soft_wrap=True)
    for warning in report.warnings:
        console.print(f"警告: {warning}", soft_wrap=True)
    if not report.signals:
        return
    table = Table(title="机会雷达线索")
    table.add_column("优先级")
    table.add_column("市场")
    table.add_column("代码")
    table.add_column("主题", overflow="fold")
    table.add_column("分数")
    table.add_column("信号", overflow="fold")
    for index, signal in enumerate(report.signals, start=1):
        table.add_row(
            str(index),
            signal.market,
            signal.symbol,
            signal.theme,
            str(signal.score),
            (
                f"新闻 {signal.news_count} | 主题命中 {signal.theme_hits} | "
                f"成交量排名 {signal.volume_rank}"
            ),
        )
    console.print(table)
    console.print("线索明细")
    for index, signal in enumerate(report.signals, start=1):
        console.print(
            f"{index}. {signal.symbol} [{signal.market}] {signal.theme}",
            soft_wrap=True,
        )
        console.print(f"   行情快照: {signal.price_snapshot}", soft_wrap=True)
        console.print(f"   为什么值得研究: {signal.why_it_matters}", soft_wrap=True)
        console.print("   证据标题:", soft_wrap=True)
        for headline in signal.evidence:
            console.print(f"   - {headline}", soft_wrap=True)
        console.print("   下一步验证:", soft_wrap=True)
        for step in signal.next_steps:
            console.print(f"   - {step}", soft_wrap=True)


def _print_research_data_request_fulfillment(
    result: ResearchDataRequestFulfillment,
) -> None:
    request = result.request
    console.print("研究数据请求执行结果")
    console.print(
        f"请求: {request.display_name} ({request.symbol or '-'}) [{request.market}]",
        soft_wrap=True,
    )
    console.print(f"内容: {request.request_text}", soft_wrap=True)
    table = Table(title="执行明细")
    table.add_column("动作")
    table.add_column("状态")
    table.add_column("行数")
    table.add_column("说明", overflow="fold")
    table.add_column("输出", overflow="fold")
    for execution in result.executions:
        table.add_row(
            _display_data_request_action(execution.action_type),
            _display_data_request_execution_status(execution.status),
            str(execution.count),
            execution.message,
            str(execution.output_path or "-"),
        )
    console.print(table)
    for execution in result.executions:
        for warning in execution.warnings:
            console.print(f"警告: {warning}", soft_wrap=True)
    console.print("边界: 数据请求执行只补证据，不是买卖建议。", soft_wrap=True)


def _print_evidence_board_column(title: str, rows: list[str]) -> None:
    console.print(title)
    if not rows:
        console.print("- 无")
        return
    for row in rows:
        console.print(f"- {row}", soft_wrap=True)


def _display_verification_check_status(status: str) -> str:
    return {
        "pass": "通过",
        "warn": "待核验",
        "fail": "阻塞",
        "na": "不适用",
    }.get(status, status)


def _display_research_run_status(status: str) -> str:
    return {
        "completed": "已完成",
        "partial": "部分完成",
    }.get(status, status)


def _display_research_action_type(action_type: str) -> str:
    return {
        "refresh_market": "刷新行情",
        "refresh_news": "刷新新闻",
        "refresh_topic_news": "刷新主题新闻",
        "refresh_filings": "刷新美股公告/财报",
        "none": "无自动动作",
    }.get(action_type, action_type)


def _display_research_action_status(status: str) -> str:
    return {
        "pulled": "已刷新",
        "cached": "使用缓存",
        "failed": "失败",
        "skipped": "跳过",
    }.get(status, status)


def _display_data_request_action(action_type: str) -> str:
    return {
        "fund_metadata_guide": "基金资料模板",
        "fund_metadata_import": "基金资料导入",
        "market": "行情",
        "news": "新闻",
        "filings": "SEC 公告",
        "verify": "下钻核验",
    }.get(action_type, action_type)


def _display_data_request_execution_status(status: str) -> str:
    return {
        "completed": "已完成",
        "failed": "失败",
        "skipped": "跳过",
        "manual_required": "需人工",
    }.get(status, status)


def _display_workbench_status(status: str) -> str:
    return {
        "ready": "可继续研究",
        "blocked": "未达标",
    }.get(status, status)


def _display_workbench_gate_status(status: str) -> str:
    return {
        "pass": "通过",
        "fail": "失败",
        "warn": "提醒",
    }.get(status, status)


def _display_gap_action_type(action_type: str) -> str:
    return {
        "market_prices": "行情",
        "sec_filings": "SEC 公告",
        "symbol_mapping": "代码映射",
    }.get(action_type, action_type)


def _display_gap_action_status(status: str) -> str:
    return {
        "pulled": "已拉取",
        "cached": "已使用缓存",
        "partial": "部分完成",
        "skipped": "跳过",
        "failed": "失败",
        "mapped": "已生成映射",
        "needs_input": "需人工处理",
    }.get(status, status)


@setup_app.callback()
def setup(ctx: typer.Context) -> None:
    """打开交互式配置中心。"""
    if ctx.invoked_subcommand is not None:
        return
    path = ensure_config_file()
    _run_configuration_center(path)


@setup_app.command("set")
def setup_set(provider_id: str, value: str) -> None:
    """保存某个数据源的 API Key 或 Token。"""
    try:
        path = set_provider_value(provider_id, value)
    except KeyError as error:
        console.print(f"未知数据源: {provider_id}")
        console.print(str(error))
        raise typer.Exit(code=1) from error
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(code=1) from error
    provider = load_config(path).providers[provider_id]
    console.print(f"已保存 {provider.name}: {path}", soft_wrap=True)


@llm_setup_app.callback()
def setup_llm(ctx: typer.Context) -> None:
    """要求使用非交互式 LLM 配置命令。"""
    if ctx.invoked_subcommand is not None:
        return
    console.print("请使用 `lychee setup llm set <base_url> <api_key> MODEL_NAME`。")
    raise typer.Exit(code=2)


@llm_setup_app.command("set")
def setup_llm_set(
    base_url: str,
    api_key: str,
    model: Annotated[
        str | None,
        typer.Argument(help="可选模型名称，例如 gpt-4.1-mini。"),
    ] = None,
) -> None:
    """保存 OpenAI 兼容 LLM 端点。"""
    try:
        path = set_openai_compatible_llm(base_url, api_key, model)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(code=1) from error
    console.print(f"已保存 OpenAI 兼容 LLM: {path}", soft_wrap=True)


def _run_configuration_center(path: Path) -> None:
    if not _keyboard_navigation_available():
        console.print("Lychee AlphaDesk 配置中心")
        console.print(f"配置文件: {path}", soft_wrap=True)
        console.print(
            "交互式配置需要可用的终端键盘导航。",
            soft_wrap=True,
        )
        console.print("自动化配置数据源: `lychee setup set <provider_id> <value>`。")
        console.print(
            "自动化配置 LLM: `lychee setup llm set <base_url> <api_key> MODEL_NAME`。",
            soft_wrap=True,
        )
        raise typer.Exit(code=2)

    run_setup_tui(path)


def _print_provider_detail(provider: ProviderSetupInfo) -> None:
    console.clear()
    console.print(setup_helpers.provider_detail_text(provider), soft_wrap=True)


def _providers_requiring_values(config: AlphaDeskConfig) -> list[ProviderSetupInfo]:
    return setup_helpers.providers_requiring_values(config)


def _provider_config_status(provider: ProviderSetupInfo) -> str:
    return setup_helpers.provider_config_status(provider)


def _provider_menu_label(provider: ProviderSetupInfo) -> str:
    return setup_helpers.provider_menu_label(provider)


def _print_value_capture_result(*, received: bool) -> None:
    if received:
        console.print("[green]✅ 已收到输入[/green]")
        return
    console.print("[red]❌ 未输入配置值[/red]")


def _keyboard_navigation_available() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


app.add_typer(policy_app, name="policy")
app.add_typer(audit_app, name="audit")
app.add_typer(discover_app, name="discover")
app.add_typer(research_app, name="research")
data_app.add_typer(data_pull_app, name="pull")
data_app.add_typer(data_set_app, name="set")
data_app.add_typer(data_guide_app, name="guide")
app.add_typer(data_app, name="data")
setup_app.add_typer(llm_setup_app, name="llm")
app.add_typer(setup_app, name="setup")


def main() -> None:
    app()
