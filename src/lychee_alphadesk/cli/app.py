import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lychee_alphadesk.core import setup as setup_helpers
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
)
from lychee_alphadesk.core.llm import LLMProviderError
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR, DEMO_ROOT
from lychee_alphadesk.core.policy import load_policy, validate_policy
from lychee_alphadesk.core.reports import generate_demo_report
from lychee_alphadesk.core.research_db import (
    list_research_queue,
    write_discovery_research_run,
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
    console.print("正在调用 LLM 分析美股、港股和 A 股市场，请稍候...", soft_wrap=True)
    try:
        report = build_today_discovery_report(selected_markets, output_dir=output_dir)
    except (DiscoveryLLMRequiredError, LLMProviderError) as error:
        console.print(str(error), soft_wrap=True)
        raise typer.Exit(code=1) from error
    output_path = write_discovery_report(report, output_dir)
    db_path = write_discovery_research_run(report, output_dir, output_path)
    console.print(f"今日市场发现已写入: {output_path}", soft_wrap=True)
    console.print(f"研究库已更新: {db_path}", soft_wrap=True)
    console.print(discovery_report_summary(report))


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
        typer.Option("--symbols", help="用英文逗号分隔证券代码，例如 AAPL,TSLA。"),
    ],
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
) -> None:
    """拉取市场新闻到本地缓存。"""
    try:
        result = pull_news_events(
            symbols=parse_symbols(symbols),
            output_dir=output_dir,
            provider_id=provider,
            start_date=start_date,
            end_date=end_date,
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
    }.get(state, state)


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
app.add_typer(data_app, name="data")
setup_app.add_typer(llm_setup_app, name="llm")
app.add_typer(setup_app, name="setup")


def main() -> None:
    app()
