import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import (
    evaluate_financials_cache,
    evaluate_market_cache,
    evaluate_news_cache,
    evaluate_research_metrics_cache,
    record_financials_cache,
    record_market_cache,
    record_news_cache,
    record_research_metrics_cache,
)
from lychee_alphadesk.core.config import AlphaDeskConfig, load_config
from lychee_alphadesk.core.data_engine import DataQualityCheck, DataSnapshot
from lychee_alphadesk.providers.demo import (
    FilingSummary,
    NewsEvent,
    PriceRow,
)
from lychee_alphadesk.providers.news_plugins import (
    NewsProviderPlugin,
    NewsProviderRegistry,
    NewsProviderRequest,
    discover_news_provider_plugins,
    missing_required_settings,
    pull_plugin_news,
)

JsonFetcher = Callable[[str, dict[str, str] | None], object]
JsonPoster = Callable[[str, dict[str, str] | None, dict[str, object]], object]
FormJsonPoster = Callable[[str, dict[str, str] | None, dict[str, str]], object]
TextFetcher = Callable[[str, dict[str, str] | None], str]

SEC_USER_AGENT = (
    "LycheeAlphaDesk/0.1 support@lychee.ai"
)
MARKET_NEWS_SYMBOL = "MARKET"
MARKET_NEWS_QUERY = (
    "stock market OR financial markets OR earnings OR central bank "
    "OR US stocks OR Hong Kong stocks OR China stocks"
)
GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
HKEX_ACTIVE_STOCKS_ENDPOINT = (
    "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_e.json"
)
HKEX_TITLE_SEARCH_ENDPOINT = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
HKEX_NEWS_BASE_URL = "https://www1.hkexnews.hk"
CNINFO_STOCK_LIST_ENDPOINT = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_ANNOUNCEMENT_ENDPOINT = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE_URL = "https://static.cninfo.com.cn/"
CNINFO_TIMEZONE = timezone(timedelta(hours=8))
CBOE_VXN_HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv"
CBOE_VOLATILITY_SYMBOLS: dict[str, tuple[str, str]] = {
    "QQQ": ("VXN", CBOE_VXN_HISTORY_URL),
}
NEWS_ENTITY_QUERIES: dict[str, str] = {
    "0700.HK": "Tencent OR 腾讯",
    "2800.HK": "Hang Seng Index OR 恒生指数 OR 盈富基金",
    "3033.HK": "Hang Seng TECH Index OR 恒生科技",
    "3067.HK": "Hang Seng TECH Index OR 恒生科技",
    "3456.HK": "HKEX Tech 100 OR 港交所科技100",
    "510300.SH": "CSI 300 OR 沪深300",
    "512480.SH": "China semiconductor OR 中国半导体",
    "515050.SH": "China 5G OR 中国5G",
    "159819.SZ": "China artificial intelligence OR 中国人工智能",
    "NVDA": "NVIDIA",
    "TSLA": "Tesla",
    "BABA": "Alibaba OR 阿里巴巴",
    "AAPL": "Apple Inc",
    "MSFT": "Microsoft",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet OR Google",
    "META": "Meta Platforms",
    "STX": "Seagate Technology",
}
COMMON_SEC_COMPANIES: dict[str, tuple[int, str]] = {
    "AAPL": (320193, "Apple Inc."),
    "MSFT": (789019, "Microsoft Corp."),
    "NVDA": (1045810, "NVIDIA Corp."),
    "TSLA": (1318605, "Tesla, Inc."),
    "AMZN": (1018724, "Amazon.com, Inc."),
    "BABA": (1577552, "Alibaba Group Holding Limited"),
    "GOOGL": (1652044, "Alphabet Inc."),
    "GOOG": (1652044, "Alphabet Inc."),
    "META": (1326801, "Meta Platforms, Inc."),
    "BRK.B": (1067983, "Berkshire Hathaway Inc."),
    "STX": (1137789, "Seagate Technology Holdings plc"),
}


@dataclass(frozen=True)
class PullResult:
    domain: str
    provider: str
    count: int
    output_path: Path
    warnings: list[str]
    refreshed: bool = True


@dataclass(frozen=True)
class FundMetadata:
    symbol: str
    display_name: str
    market: str
    tracking_index: str
    expense_ratio: str
    holdings_summary: str
    source_url: str
    as_of: str
    provider: str


@dataclass(frozen=True)
class ResearchMetric:
    symbol: str
    domain: str
    name: str
    value: str
    as_of: str
    source_url: str
    note: str
    provider: str


@dataclass(frozen=True)
class FinancialSnapshot:
    symbol: str
    company: str
    cik: int
    form: str
    fiscal_year: int | None
    fiscal_period: str
    period_end: str
    filing_date: str
    currency: str
    revenue: int | float | None
    revenue_period_start: str
    revenue_period_end: str
    revenue_prior: int | float | None
    revenue_prior_period_start: str
    revenue_prior_period_end: str
    net_income: int | float | None
    net_income_period_start: str
    net_income_period_end: str
    net_income_prior: int | float | None
    net_income_prior_period_start: str
    net_income_prior_period_end: str
    operating_cash_flow: int | float | None
    operating_cash_flow_period_start: str
    operating_cash_flow_period_end: str
    operating_cash_flow_prior: int | float | None
    operating_cash_flow_prior_period_start: str
    operating_cash_flow_prior_period_end: str
    source_url: str


@dataclass(frozen=True)
class FundMetadataGuide:
    symbol: str
    display_name: str
    market: str
    required_fields: list[str]
    suggested_sources: list[str]
    write_command: str
    apply_command: str
    output_path: Path


def write_fund_metadata_guide(
    *,
    output_dir: Path,
    symbol: str,
    display_name: str,
    market: str = "",
) -> FundMetadataGuide:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("请提供基金或 ETF 代码。")
    normalized_market = market.strip().upper() or _infer_symbol_market(normalized_symbol)
    clean_name = display_name.strip() or normalized_symbol
    required_fields = [
        "tracking_index",
        "expense_ratio",
        "holdings_summary",
        "source_url",
    ]
    suggested_sources = _fund_metadata_suggested_sources(normalized_market)
    write_command = _fund_metadata_write_command(
        symbol=normalized_symbol,
        display_name=clean_name,
        market=normalized_market,
    )
    output_path = _fund_metadata_guide_path(output_dir, normalized_symbol)
    guide = FundMetadataGuide(
        symbol=normalized_symbol,
        display_name=clean_name,
        market=normalized_market,
        required_fields=required_fields,
        suggested_sources=suggested_sources,
        write_command=write_command,
        apply_command=f"lychee data set fund --from-file {_quote_cli_value(str(output_path))}",
        output_path=output_path,
    )
    guide.output_path.parent.mkdir(parents=True, exist_ok=True)
    guide.output_path.write_text(
        json.dumps(
            {
                "symbol": guide.symbol,
                "display_name": guide.display_name,
                "market": guide.market,
                "required_fields": guide.required_fields,
                "suggested_sources": guide.suggested_sources,
                "write_command": guide.write_command,
                "apply_command": guide.apply_command,
                "template": {
                    "tracking_index": "",
                    "expense_ratio": "",
                    "holdings_summary": "",
                    "source_url": "",
                    "as_of": "",
                },
                "notes": [
                    "只填写已经从基金公司、交易所、券商或可信数据商核对过的资料。",
                    "不要让 LLM 猜测费用率、跟踪指数或持仓成分。",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return guide


def write_fund_metadata_cache_from_file(
    *,
    output_dir: Path,
    guide_path: Path,
) -> PullResult:
    try:
        payload = json.loads(guide_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"基金资料模板不存在: {guide_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"基金资料模板不是有效 JSON: {guide_path}") from error
    if not isinstance(payload, dict):
        raise ValueError("基金资料模板必须是 JSON 对象。")
    template = payload.get("template")
    if not isinstance(template, dict):
        raise ValueError("基金资料模板缺少 template 对象。")
    return write_fund_metadata_cache(
        output_dir=output_dir,
        symbol=_json_text(payload, "symbol"),
        display_name=_json_text(payload, "display_name"),
        market=_json_text(payload, "market"),
        tracking_index=_json_text(template, "tracking_index"),
        expense_ratio=_json_text(template, "expense_ratio"),
        holdings_summary=_json_text(template, "holdings_summary"),
        source_url=_json_text(template, "source_url"),
        as_of=_json_text(template, "as_of"),
        provider="manual",
    )


def write_fund_metadata_cache(
    *,
    output_dir: Path,
    symbol: str,
    display_name: str,
    market: str,
    tracking_index: str = "",
    expense_ratio: str = "",
    holdings_summary: str = "",
    source_url: str = "",
    as_of: str = "",
    provider: str = "manual",
) -> PullResult:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("请提供基金或 ETF 代码。")
    if not source_url.strip():
        raise ValueError("请提供资料来源 URL，避免写入不可审计的基金资料。")

    row = FundMetadata(
        symbol=normalized_symbol,
        display_name=display_name.strip() or normalized_symbol,
        market=market.strip().upper() or _infer_symbol_market(normalized_symbol),
        tracking_index=tracking_index.strip(),
        expense_ratio=expense_ratio.strip(),
        holdings_summary=holdings_summary.strip(),
        source_url=source_url.strip(),
        as_of=as_of.strip(),
        provider=provider.strip() or "manual",
    )
    rows = _merge_symbol_cache_rows(
        output_dir=output_dir,
        filename="fund-metadata.json",
        new_rows=[asdict(row)],
    )
    output_path = _write_cache(
        output_dir=output_dir,
        filename="fund-metadata.json",
        provider=row.provider,
        rows=rows,
        warnings=[],
    )
    return PullResult("fund_metadata", row.provider, 1, output_path, [])


def write_research_metric_cache(
    *,
    output_dir: Path,
    symbol: str,
    domain: str,
    name: str,
    value: str,
    as_of: str,
    source_url: str,
    note: str = "",
    provider: str = "manual",
) -> PullResult:
    normalized_symbol = symbol.strip().upper()
    normalized_domain = domain.strip().lower()
    clean_name = name.strip()
    clean_value = value.strip()
    if not normalized_symbol:
        raise ValueError("请提供研究指标对应的证券代码。")
    if not normalized_domain:
        raise ValueError("请提供研究指标领域，例如 market_breadth 或 volatility_metrics。")
    if not clean_name:
        raise ValueError("请提供研究指标名称。")
    if not clean_value:
        raise ValueError("请提供研究指标数值或读数。")
    if not source_url.strip():
        raise ValueError("请提供资料来源 URL，避免写入不可审计的研究指标。")

    row = ResearchMetric(
        symbol=normalized_symbol,
        domain=normalized_domain,
        name=clean_name,
        value=clean_value,
        as_of=as_of.strip(),
        source_url=source_url.strip(),
        note=note.strip(),
        provider=provider.strip() or "manual",
    )
    rows = _merge_research_metric_cache_rows(output_dir, [asdict(row)])
    output_path = _write_cache(
        output_dir=output_dir,
        filename="research-metrics.json",
        provider=row.provider,
        rows=rows,
        warnings=[],
    )
    return PullResult("research_metric", row.provider, 1, output_path, [])


def pull_volatility_metrics(
    *,
    symbols: list[str],
    output_dir: Path,
    fetch_text: TextFetcher | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> PullResult:
    normalized_symbols = _normalized_symbols(symbols)
    supported_symbols = [
        symbol for symbol in normalized_symbols if symbol in CBOE_VOLATILITY_SYMBOLS
    ]
    if not supported_symbols:
        supported = ", ".join(sorted(CBOE_VOLATILITY_SYMBOLS))
        raise ValueError(f"当前 Cboe 波动率数据仅支持: {supported}。")

    freshness = evaluate_research_metrics_cache(
        output_dir=output_dir,
        provider="cboe",
        symbols=supported_symbols,
        now=now,
        force=force,
    )
    if not freshness.should_refresh and freshness.entry is not None:
        cached_rows = [
            row
            for row in _read_cache(output_dir, "research-metrics.json").rows
            if _cache_row_symbol(row) in set(supported_symbols)
            and str(row.get("domain") or "").strip() == "volatility_metrics"
        ]
        return PullResult(
            "research_metric",
            freshness.entry.provider,
            len(cached_rows),
            freshness.entry.artifact_path,
            [freshness.reason],
            refreshed=False,
        )

    fetcher = fetch_text or _fetch_text
    rows: list[ResearchMetric] = []
    warnings: list[str] = []
    for symbol in supported_symbols:
        index_name, source_url = CBOE_VOLATILITY_SYMBOLS[symbol]
        history = _parse_cboe_volatility_history(
            _fetch_provider_text(fetcher, source_url, None)
        )
        if not history:
            warnings.append(f"Cboe {index_name} 没有返回可解析的历史收盘数据。")
            continue
        rows.extend(
            _build_cboe_volatility_metrics(
                symbol=symbol,
                index_name=index_name,
                source_url=source_url,
                history=history,
            )
        )

    cache_rows = _merge_research_metric_cache_rows(
        output_dir,
        [asdict(row) for row in rows],
    )
    output_path = _write_cache(
        output_dir=output_dir,
        filename="research-metrics.json",
        provider="cboe",
        rows=cache_rows,
        warnings=warnings,
        now=now,
    )
    record_research_metrics_cache(
        output_dir=output_dir,
        provider="cboe",
        symbols=supported_symbols,
        artifact_path=output_path,
        row_count=len(rows),
        now=now,
        forced=force,
    )
    return PullResult("research_metric", "cboe", len(rows), output_path, warnings)


def write_manual_news_event(
    *,
    output_dir: Path,
    symbol: str,
    headline: str,
    summary: str,
    source_url: str,
    published_at: str = "",
    now: datetime | None = None,
) -> PullResult:
    normalized_symbol = symbol.strip().upper()
    clean_headline = headline.strip()
    clean_summary = summary.strip()
    clean_source_url = source_url.strip()
    if not normalized_symbol:
        raise ValueError("请提供新闻对应的证券代码。")
    if not clean_headline:
        raise ValueError("请提供新闻标题。")
    if not clean_summary:
        raise ValueError("请提供新闻摘要或关键事实。")
    source_parts = urllib.parse.urlparse(clean_source_url)
    if source_parts.scheme not in {"http", "https"} or not source_parts.netloc:
        raise ValueError("请提供可审计的 http(s) 来源 URL。")

    row = NewsEvent(
        timestamp=published_at.strip()
        or (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        headline=clean_headline,
        summary=clean_summary,
        symbols=[normalized_symbol],
        source_url=clean_source_url,
        is_symbol_scoped=True,
    )
    rows = _merge_news_cache_rows(output_dir, [asdict(row)])
    output_path = _write_cache(
        output_dir=output_dir,
        filename="news-events.json",
        provider="manual",
        rows=rows,
        warnings=[],
        now=now,
    )
    return PullResult("news", "manual", 1, output_path, [])


def write_manual_filing_summary(
    *,
    output_dir: Path,
    company: str,
    form: str,
    date: str,
    summary: str,
    source_url: str,
    symbol: str = "",
) -> PullResult:
    normalized_symbol = symbol.strip().upper()
    clean_company = company.strip()
    clean_form = form.strip().upper()
    clean_date = date.strip()
    clean_summary = summary.strip()
    clean_source_url = source_url.strip()
    if not clean_company:
        raise ValueError("请提供公告所属公司名称。")
    if not clean_form:
        raise ValueError("请提供公告或表单类型，例如 4、8-K、10-Q。")
    try:
        datetime.fromisoformat(f"{clean_date}T00:00:00")
    except ValueError as error:
        raise ValueError("请提供公告日期，格式为 YYYY-MM-DD。") from error
    if not clean_summary:
        raise ValueError("请提供已核验的公告关键事实。")
    source_parts = urllib.parse.urlparse(clean_source_url)
    if source_parts.scheme not in {"http", "https"} or not source_parts.netloc:
        raise ValueError("请提供可审计的 http(s) 来源 URL。")

    row = FilingSummary(
        date=clean_date,
        company=clean_company,
        form=clean_form,
        summary=clean_summary,
        source_url=clean_source_url,
        symbol=normalized_symbol,
    )
    rows = _merge_filing_cache_rows(output_dir, [asdict(row)])
    output_path = _write_cache(
        output_dir=output_dir,
        filename="filings.json",
        provider="manual",
        rows=rows,
        warnings=[],
    )
    return PullResult("filings", "manual", 1, output_path, [])


def read_research_metric_cache(output_dir: Path) -> list[ResearchMetric]:
    cache = _read_cache(output_dir, "research-metrics.json")
    return [_research_metric_from_dict(row) for row in cache.rows]


def _fund_metadata_guide_path(output_dir: Path, symbol: str) -> Path:
    safe_symbol = re.sub(r"[^A-Z0-9._-]+", "-", symbol.upper()).strip("-")
    return output_dir / "data" / f"fund-metadata-guide-{safe_symbol}.json"


def _json_text(payload: dict[object, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _fund_metadata_suggested_sources(market: str) -> list[str]:
    if market == "HK":
        return ["基金公司产品页", "香港交易所 ETF 页面", "券商/数据商基金资料页"]
    if market == "CN":
        return ["基金公司产品页", "交易所或基金公告页面", "券商/数据商基金资料页"]
    if market == "US":
        return ["基金公司产品页", "交易所 ETF 页面", "券商/数据商基金资料页"]
    return ["基金公司产品页", "交易所基金资料页面", "券商/数据商基金资料页"]


def _fund_metadata_write_command(
    *,
    symbol: str,
    display_name: str,
    market: str,
) -> str:
    return (
        f"lychee data set fund --symbol {symbol} "
        f"--name {_quote_cli_value(display_name)} "
        f"--market {market or '<MARKET>'} "
        '--tracking-index "<填入核验后的跟踪指数>" '
        '--expense-ratio "<填入核验后的费用率>" '
        '--holdings-summary "<填入核验后的成分摘要>" '
        '--source-url "<填入资料来源URL>"'
    )


def _quote_cli_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_fund_metadata_cache(output_dir: Path) -> list[FundMetadata]:
    cache = _read_cache(output_dir, "fund-metadata.json")
    return [_fund_metadata_from_dict(row) for row in cache.rows]


def pull_market_prices(
    *,
    symbols: list[str],
    config_path: Path | None = None,
    output_dir: Path,
    provider_id: str = "alpha_vantage",
    fetch_json: JsonFetcher | None = None,
    post_json: JsonPoster | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> PullResult:
    if provider_id not in {"alpha_vantage", "auto", "eastmoney", "tushare"}:
        raise ValueError(
            "当前版本仅支持通过 alpha_vantage、eastmoney、tushare 或 auto 拉取行情"
        )

    if not symbols:
        raise ValueError("请至少输入一个证券代码。")

    freshness = evaluate_market_cache(
        output_dir=output_dir,
        provider=provider_id,
        symbols=symbols,
        now=now,
        force=force,
    )
    if not freshness.should_refresh and freshness.entry is not None:
        cache = _read_cache(output_dir, "market-prices.json")
        if freshness.entry.status == "no_data":
            return PullResult(
                "market",
                freshness.entry.provider,
                0,
                freshness.entry.artifact_path,
                [freshness.reason, *cache.warnings],
                refreshed=False,
            )
        if _market_cache_covers_symbols(cache.rows, symbols):
            return PullResult(
                "market",
                freshness.entry.provider,
                len(cache.rows),
                freshness.entry.artifact_path,
                [freshness.reason],
                refreshed=False,
            )
        cache_gap_warning = "行情缓存未覆盖本次请求的全部代码，需要重新刷新。"
    else:
        cache_gap_warning = ""

    rows: list[PriceRow] = []
    warnings: list[str] = [cache_gap_warning] if cache_gap_warning else []
    fetcher = fetch_json or _fetch_json
    poster = post_json or _post_json
    config = load_config(config_path)
    api_key: str | None = None
    tushare_token: str | None = None

    for symbol in symbols:
        selected_provider = _market_provider_for_symbol(provider_id, symbol, config)
        if selected_provider == "alpha_vantage":
            if api_key is None:
                api_key = _configured_value(
                    config.providers["alpha_vantage"].value,
                    "Alpha Vantage",
                )
            try:
                row = _pull_alpha_vantage_daily(symbol, api_key, fetcher)
            except RuntimeError as error:
                row = None
                primary_warning = f"{symbol} Alpha Vantage 行情拉取失败: {error}"
            else:
                primary_warning = ""
        elif selected_provider == "tushare":
            if tushare_token is None:
                tushare_token = _configured_value(
                    config.providers["tushare"].value,
                    "Tushare Pro",
                )
            try:
                row = _pull_tushare_daily(symbol, tushare_token, poster)
            except RuntimeError as error:
                row = None
                primary_warning = f"{symbol} Tushare 行情拉取失败: {error}"
            else:
                primary_warning = ""
            if row is None and provider_id == "auto":
                try:
                    row = _pull_eastmoney_daily(symbol, fetcher)
                except RuntimeError as error:
                    eastmoney_warning = f"{symbol} Eastmoney 行情拉取失败: {error}"
                    primary_warning = (
                        f"{primary_warning}；{eastmoney_warning}"
                        if primary_warning
                        else eastmoney_warning
                    )
                else:
                    if row is not None and primary_warning:
                        warnings.append(f"{primary_warning}；已改用 Eastmoney")
        else:
            try:
                row = _pull_eastmoney_daily(symbol, fetcher)
            except RuntimeError as error:
                row = None
                primary_warning = f"{symbol} Eastmoney 行情拉取失败: {error}"
            else:
                primary_warning = ""
        if row is None:
            try:
                row = _pull_yahoo_chart(symbol, fetcher)
            except RuntimeError as error:
                if primary_warning:
                    warnings.append(f"{primary_warning}；Yahoo fallback 失败: {error}")
                else:
                    warnings.append(
                        f"{_provider_display_name(selected_provider)} 没有返回 {symbol} 的行情；"
                        f"Yahoo fallback 失败: {error}"
                    )
                continue
        if row is None:
            warnings.append(f"{_provider_display_name(selected_provider)} 没有返回 {symbol} 的行情")
        else:
            rows.append(row)

    new_rows = [asdict(row) for row in rows]
    cache_rows = _merge_market_cache_rows(output_dir, new_rows)
    output_path = _write_cache(
        output_dir=output_dir,
        filename="market-prices.json",
        provider=provider_id,
        rows=cache_rows,
        warnings=warnings,
        now=now,
    )
    record_market_cache(
        output_dir=output_dir,
        provider=provider_id,
        symbols=symbols,
        artifact_path=output_path,
        row_count=len(rows),
        now=now,
        forced=force,
    )
    return PullResult("market", provider_id, len(rows), output_path, warnings)


def pull_news_events(
    *,
    symbols: list[str],
    query: str | None = None,
    config: AlphaDeskConfig | None = None,
    config_path: Path | None = None,
    output_dir: Path,
    provider_id: str = "auto",
    start_date: str | None = None,
    end_date: str | None = None,
    fetch_json: JsonFetcher | None = None,
    news_provider_registry: NewsProviderRegistry | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> PullResult:
    active_config = config or load_config(config_path)
    start, end = _date_window(start_date, end_date)
    freshness = evaluate_news_cache(
        output_dir=output_dir,
        provider=provider_id,
        symbols=symbols,
        start_date=start,
        end_date=end,
        query=query,
        now=now,
        force=force,
    )
    if not freshness.should_refresh and freshness.entry is not None:
        cache = _read_cache(output_dir, "news-events.json")
        matching_rows = _matching_news_cache_rows(cache.rows, symbols)
        if freshness.entry.row_count == 0:
            return PullResult(
                "news",
                freshness.entry.provider,
                0,
                freshness.entry.artifact_path,
                [
                    "新闻缓存记录为空且仍在保质期内，跳过重复刷新；"
                    "需要重新尝试请使用 --force。"
                ],
                refreshed=False,
            )
        if _news_cache_covers_symbols(cache.rows, symbols):
            return PullResult(
                "news",
                freshness.entry.provider,
                len(matching_rows) if symbols else len(cache.rows),
                freshness.entry.artifact_path,
                [freshness.reason],
                refreshed=False,
            )
        cache_gap_warning = (
            "新闻缓存未覆盖本次请求的代码，需要重新刷新。"
            if symbols
            else "新闻缓存记录为空，需要重新刷新。"
        )
    else:
        cache_gap_warning = ""

    fetcher = fetch_json or _fetch_json
    warnings: list[str] = [cache_gap_warning] if cache_gap_warning else []
    registry = news_provider_registry or discover_news_provider_plugins()
    warnings.extend(_sanitize_error_message(item) for item in registry.diagnostics)
    rows: list[NewsEvent] = []
    selected_provider = ""
    last_empty_provider = ""
    last_error: RuntimeError | None = None

    for candidate in _news_provider_candidates(
        active_config,
        provider_id,
        symbols,
        query,
        registry,
    ):
        if query and candidate == "finnhub":
            warnings.append("Finnhub 不支持主题关键词新闻查询，正在尝试下一个新闻数据源")
            continue
        try:
            rows = _pull_news_for_provider(
                provider_id=candidate,
                symbols=symbols,
                query=query,
                start=start,
                end=end,
                config=active_config,
                fetch_json=fetcher,
                news_provider_registry=registry,
            )
        except RuntimeError as error:
            last_error = RuntimeError(_sanitize_error_message(str(error)))
            if provider_id == "auto":
                warnings.append(
                    _news_provider_retry_warning(
                        candidate,
                        last_error,
                        provider_name=_news_provider_display_name(candidate, registry),
                    )
                )
                continue
            raise last_error from error
        if not rows and provider_id == "auto":
            last_empty_provider = candidate
            warnings.append(
                f"{_news_provider_display_name(candidate, registry)} 没有返回匹配新闻，"
                "正在尝试下一个可用新闻数据源"
            )
            continue
        selected_provider = candidate
        break

    if not selected_provider:
        if last_empty_provider:
            selected_provider = last_empty_provider
            rows = []
        elif last_error:
            raise last_error
        else:
            raise ValueError(
                "尚未配置可用新闻数据源。请配置内置新闻来源，或安装并配置新闻插件。"
            )

    new_rows = [asdict(row) for row in rows]
    cache_rows = _merge_news_cache_rows(output_dir, new_rows)
    output_path = _write_cache(
        output_dir=output_dir,
        filename="news-events.json",
        provider=selected_provider,
        rows=cache_rows,
        warnings=warnings,
        now=now,
    )
    record_news_cache(
        output_dir=output_dir,
        provider=selected_provider,
        cache_provider=provider_id,
        symbols=symbols,
        start_date=start,
        end_date=end,
        query=query,
        artifact_path=output_path,
        row_count=len(rows),
        now=now,
        forced=force,
    )
    return PullResult("news", selected_provider, len(rows), output_path, warnings)


def pull_sec_filings(
    *,
    symbols: list[str],
    output_dir: Path,
    fetch_json: JsonFetcher | None = None,
    fetch_text: TextFetcher | None = None,
    post_form_json: FormJsonPoster | None = None,
    limit_per_symbol: int = 5,
) -> PullResult:
    headers = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}
    fetcher = fetch_json or _fetch_json
    text_fetcher = fetch_text or _fetch_text
    form_poster = post_form_json or _post_form_json
    rows: list[FilingSummary] = []
    warnings: list[str] = []
    normalized_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    hk_symbols = [symbol for symbol in normalized_symbols if symbol.endswith(".HK")]
    cn_symbols = [
        symbol
        for symbol in normalized_symbols
        if symbol.endswith((".SH", ".SZ"))
    ]
    sec_symbols = [
        symbol
        for symbol in normalized_symbols
        if symbol not in {*hk_symbols, *cn_symbols}
    ]
    providers: list[str] = []

    if sec_symbols:
        providers.append("sec_edgar")
        try:
            tickers_payload = _fetch_provider_json(
                fetcher,
                "https://www.sec.gov/files/company_tickers.json",
                headers,
            )
            cik_by_symbol, company_by_symbol = _parse_sec_company_tickers(tickers_payload)
        except RuntimeError:
            cik_by_symbol, company_by_symbol = _common_sec_company_maps()
            warnings.append("SEC 代码映射拉取失败，已使用内置 CIK 备用映射")

        _merge_common_sec_companies(cik_by_symbol, company_by_symbol)
        for symbol in sec_symbols:
            cik = cik_by_symbol.get(symbol)
            company = company_by_symbol.get(symbol, symbol)
            if cik is None:
                warnings.append(f"未找到 {symbol} 的 SEC CIK")
                continue
            try:
                payload = _fetch_provider_json(
                    fetcher,
                    f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                    headers,
                )
            except RuntimeError as error:
                warnings.append(f"{symbol} SEC 提交记录拉取失败: {error}")
                continue
            rows.extend(
                _parse_sec_recent_filings(
                    cik=cik,
                    symbol=symbol,
                    company=company,
                    payload=payload,
                    limit=limit_per_symbol,
                )
            )

    if hk_symbols:
        providers.append("hkexnews")
        try:
            stock_payload = _fetch_provider_json(
                fetcher,
                HKEX_ACTIVE_STOCKS_ENDPOINT,
                headers,
            )
        except RuntimeError as error:
            warnings.append(f"HKEX 上市证券清单拉取失败: {error}")
        else:
            stock_ids = _parse_hkex_active_stock_ids(stock_payload)
            for symbol in hk_symbols:
                stock_id = stock_ids.get(_hkex_stock_code(symbol))
                if stock_id is None:
                    warnings.append(f"未在 HKEX 上市证券清单中找到 {symbol}")
                    continue
                query = urllib.parse.urlencode(
                    {
                        "category": "0",
                        "lang": "EN",
                        "market": "SEHK",
                        "stockId": stock_id,
                    }
                )
                try:
                    html = _fetch_provider_text(
                        text_fetcher,
                        f"{HKEX_TITLE_SEARCH_ENDPOINT}?{query}",
                        {"User-Agent": SEC_USER_AGENT, "Accept": "text/html"},
                    )
                except RuntimeError as error:
                    warnings.append(f"{symbol} HKEX 公告拉取失败: {error}")
                    continue
                rows.extend(
                    _parse_hkex_recent_filings(
                        symbol=symbol,
                        html=html,
                        limit=limit_per_symbol,
                    )
                )

    if cn_symbols:
        providers.append("cninfo")
        cninfo_headers = {
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/search",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            stock_payload = _fetch_provider_json(
                fetcher,
                CNINFO_STOCK_LIST_ENDPOINT,
                headers,
            )
        except RuntimeError as error:
            warnings.append(f"巨潮证券清单拉取失败: {error}")
        else:
            stocks = _parse_cninfo_stocks(stock_payload)
            for symbol in cn_symbols:
                stock = stocks.get(_cninfo_stock_code(symbol))
                if stock is None:
                    warnings.append(f"未在巨潮证券清单中找到 {symbol}")
                    continue
                try:
                    payload = _post_provider_form_json(
                        form_poster,
                        CNINFO_ANNOUNCEMENT_ENDPOINT,
                        cninfo_headers,
                        _cninfo_announcement_payload(stock),
                    )
                except RuntimeError as error:
                    warnings.append(f"{symbol} 巨潮公告拉取失败: {error}")
                    continue
                rows.extend(
                    _parse_cninfo_recent_filings(
                        symbol=symbol,
                        company=stock.company,
                        payload=payload,
                        limit=limit_per_symbol,
                    )
                )

    cache_rows = _merge_filing_cache_rows(output_dir, [asdict(row) for row in rows])
    provider = "+".join(providers) or "sec_edgar"
    output_path = _write_cache(
        output_dir=output_dir,
        filename="filings.json",
        provider=provider,
        rows=cache_rows,
        warnings=warnings,
    )
    return PullResult("filings", provider, len(rows), output_path, warnings)


def pull_sec_financials(
    *,
    symbols: list[str],
    output_dir: Path,
    fetch_json: JsonFetcher | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> PullResult:
    if not symbols:
        raise ValueError("请至少输入一个美股证券代码。")

    normalized_symbols = [symbol.upper() for symbol in symbols]
    freshness = evaluate_financials_cache(
        output_dir=output_dir,
        symbols=normalized_symbols,
        now=now,
        force=force,
    )
    if not freshness.should_refresh and freshness.entry is not None:
        cache = _read_cache(output_dir, "financials.json")
        if _financial_cache_covers_symbols(cache.rows, normalized_symbols):
            return PullResult(
                "financials",
                freshness.entry.provider,
                len(cache.rows),
                freshness.entry.artifact_path,
                [freshness.reason],
                refreshed=False,
            )
        cache_gap_warning = "财务快照缓存未覆盖本次请求的全部代码，需要重新刷新。"
    else:
        cache_gap_warning = ""

    headers = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}
    fetcher = fetch_json or _fetch_json
    warnings: list[str] = [cache_gap_warning] if cache_gap_warning else []
    rows: list[FinancialSnapshot] = []
    try:
        tickers_payload = _fetch_provider_json(
            fetcher,
            "https://www.sec.gov/files/company_tickers.json",
            headers,
        )
        cik_by_symbol, company_by_symbol = _parse_sec_company_tickers(tickers_payload)
    except RuntimeError:
        cik_by_symbol, company_by_symbol = _common_sec_company_maps()
        warnings.append("SEC 代码映射拉取失败，已使用内置 CIK 备用映射")
    _merge_common_sec_companies(cik_by_symbol, company_by_symbol)

    for symbol in normalized_symbols:
        if _infer_symbol_market(symbol) != "US":
            warnings.append(f"{symbol} 当前不适用 SEC XBRL 财务快照；仅支持美股发行人。")
            continue
        cik = cik_by_symbol.get(symbol)
        if cik is None:
            warnings.append(f"未找到 {symbol} 的 SEC CIK，无法拉取财务快照。")
            continue
        company = company_by_symbol.get(symbol, symbol)
        source_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        try:
            payload = _fetch_provider_json(fetcher, source_url, headers)
        except RuntimeError as error:
            warnings.append(f"{symbol} SEC XBRL 财务快照拉取失败: {error}")
            continue
        snapshot = _parse_sec_financial_snapshot(
            symbol=symbol,
            company=company,
            cik=cik,
            source_url=source_url,
            payload=payload,
        )
        if snapshot is None:
            warnings.append(f"{symbol} SEC XBRL 未找到可用的营收、利润或经营现金流数据。")
            continue
        rows.append(snapshot)

    cache_rows = _merge_symbol_cache_rows(
        output_dir=output_dir,
        filename="financials.json",
        new_rows=[asdict(row) for row in rows],
    )
    output_path = _write_cache(
        output_dir=output_dir,
        filename="financials.json",
        provider="sec_edgar",
        rows=cache_rows,
        warnings=warnings,
        now=now,
    )
    record_financials_cache(
        output_dir=output_dir,
        symbols=normalized_symbols,
        artifact_path=output_path,
        row_count=len(rows),
        now=now,
        forced=force,
    )
    return PullResult("financials", "sec_edgar", len(rows), output_path, warnings)


def build_cached_data_snapshot(output_dir: Path) -> DataSnapshot:
    market_cache = _read_cache(output_dir, "market-prices.json")
    news_cache = _read_cache(output_dir, "news-events.json")
    filing_cache = _read_cache(output_dir, "filings.json")
    financials_cache = _read_cache(output_dir, "financials.json")

    prices = [_price_row_from_dict(row) for row in market_cache.rows]
    news_events = [_news_event_from_dict(row) for row in news_cache.rows]
    filings = [_filing_summary_from_dict(row) for row in filing_cache.rows]
    financials = [_financial_snapshot_from_dict(row) for row in financials_cache.rows]
    provider_names = [
        provider
        for provider in [
            market_cache.provider,
            news_cache.provider,
            filing_cache.provider,
            financials_cache.provider,
        ]
        if provider
    ]

    return DataSnapshot(
        mode="live",
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        provider_names=provider_names,
        prices=prices,
        news_events=news_events,
        filings=filings,
        financials=[asdict(row) for row in financials],
        forecasts={},
        quality_checks=run_cached_data_health(output_dir),
    )


def run_cached_data_health(output_dir: Path) -> list[DataQualityCheck]:
    market_cache = _read_cache(output_dir, "market-prices.json")
    checks = [
        _cache_health_check(
            output_dir=output_dir,
            filename="market-prices.json",
            name="market-cache-present",
            noun="行情",
        ),
        _cache_health_check(
            output_dir=output_dir,
            filename="financials.json",
            name="financials-cache-present",
            noun="财务快照",
        ),
        _cache_health_check(
            output_dir=output_dir,
            filename="news-events.json",
            name="news-cache-present",
            noun="新闻事件",
        ),
        _cache_health_check(
            output_dir=output_dir,
            filename="filings.json",
            name="filings-cache-present",
            noun="公告",
        ),
    ]
    return [*checks, *_market_coverage_checks(market_cache)]


def parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",")]
    return [symbol for symbol in symbols if symbol]


@dataclass(frozen=True)
class _CachePayload:
    provider: str
    rows: list[dict[str, object]]
    warnings: tuple[str, ...] = ()


def _fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法从 {_sanitize_url(url)} 获取 JSON: {error}") from error


def _fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            if not isinstance(body, bytes):
                raise RuntimeError("响应正文不是文本字节流")
            return body.decode("utf-8")
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError(f"无法从 {_sanitize_url(url)} 获取文本: {error}") from error


def _post_json(
    url: str,
    headers: dict[str, str] | None,
    payload: dict[str, object],
) -> object:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers or {},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法从 {_sanitize_url(url)} 获取 JSON: {error}") from error


def _post_form_json(
    url: str,
    headers: dict[str, str] | None,
    payload: dict[str, str],
) -> object:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers=headers or {},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法从 {_sanitize_url(url)} 获取 JSON: {error}") from error


def _fetch_provider_json(
    fetch_json: JsonFetcher,
    url: str,
    headers: dict[str, str] | None,
) -> object:
    try:
        return fetch_json(url, headers)
    except RuntimeError as error:
        raise RuntimeError(_sanitize_error_message(str(error))) from error


def _fetch_provider_text(
    fetch_text: TextFetcher,
    url: str,
    headers: dict[str, str] | None,
) -> str:
    try:
        return fetch_text(url, headers)
    except RuntimeError as error:
        raise RuntimeError(_sanitize_error_message(str(error))) from error


def _post_provider_json(
    post_json: JsonPoster,
    url: str,
    headers: dict[str, str] | None,
    payload: dict[str, object],
) -> object:
    try:
        return post_json(url, headers, payload)
    except RuntimeError as error:
        raise RuntimeError(_sanitize_error_message(str(error))) from error


def _post_provider_form_json(
    post_form_json: FormJsonPoster,
    url: str,
    headers: dict[str, str] | None,
    payload: dict[str, str],
) -> object:
    try:
        return post_form_json(url, headers, payload)
    except RuntimeError as error:
        raise RuntimeError(_sanitize_error_message(str(error))) from error


def _sanitize_error_message(message: str) -> str:
    sanitized = message
    for key in ("apikey", "apiKey", "api_key", "token", "api_token"):
        sanitized = re.sub(
            rf"({re.escape(key)}=)[^&\s]+",
            r"\1***",
            sanitized,
            flags=re.IGNORECASE,
        )
    return sanitized


def _sanitize_url(url: str) -> str:
    return _sanitize_error_message(url)


def _news_provider_retry_warning(
    provider_id: str,
    error: RuntimeError,
    *,
    provider_name: str | None = None,
) -> str:
    display_name = provider_name or {
        "gdelt": "GDELT",
        "marketaux": "Marketaux",
        "finnhub": "Finnhub",
        "newsapi": "NewsAPI",
    }.get(provider_id, provider_id)
    reason = _news_provider_error_summary(str(error))
    return f"{display_name} {reason}，正在尝试下一个已配置新闻数据源"


def _news_provider_error_summary(message: str) -> str:
    normalized = message.casefold()
    if "http error 401" in normalized or "http 401" in normalized:
        return "认证失败（HTTP 401）；请检查 API Key"
    if "http error 403" in normalized or "http 403" in normalized:
        return "被拒绝访问（HTTP 403）；请检查 API Key、套餐权限或地区限制"
    if "http error 429" in normalized or "http 429" in normalized:
        return "请求过于频繁（HTTP 429）；请等待额度恢复后重试"
    if "timed out" in normalized or "timeout" in normalized:
        return "请求超时"
    return "请求失败"


def _configured_value(value: str | None, provider_name: str) -> str:
    if value and value.strip():
        return value.strip()
    raise ValueError(f"{provider_name} 尚未配置。请先运行 `lychee setup`。")


def _market_provider_for_symbol(
    provider_id: str,
    symbol: str,
    config: AlphaDeskConfig,
) -> str:
    if provider_id != "auto":
        return provider_id
    tushare_token = config.providers["tushare"].value
    if _is_tushare_symbol(symbol) and tushare_token and tushare_token.strip():
        return "tushare"
    return "eastmoney" if _is_eastmoney_symbol(symbol) else "alpha_vantage"


def _is_eastmoney_symbol(symbol: str) -> bool:
    normalized = symbol.upper()
    return normalized.endswith((".HK", ".SH", ".SS", ".SZ"))


def _is_tushare_symbol(symbol: str) -> bool:
    return _is_eastmoney_symbol(symbol)


def _provider_display_name(provider_id: str) -> str:
    return {
        "alpha_vantage": "Alpha Vantage",
        "eastmoney": "Eastmoney",
        "gdelt": "GDELT",
        "marketaux": "Marketaux",
        "finnhub": "Finnhub",
        "newsapi": "NewsAPI",
        "tushare": "Tushare Pro",
        "auto": "Auto",
    }.get(provider_id, provider_id)


def _pull_alpha_vantage_daily(
    symbol: str,
    api_key: str,
    fetcher: JsonFetcher,
) -> PriceRow | None:
    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode(
        {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "apikey": api_key,
            "outputsize": "compact",
        }
    )
    payload = _fetch_provider_json(fetcher, url, None)
    return _parse_alpha_vantage_daily(symbol, payload)


def _pull_eastmoney_daily(
    symbol: str,
    fetcher: JsonFetcher,
) -> PriceRow | None:
    secid = _eastmoney_secid(symbol)
    if secid is None:
        return None
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "end": "20500101",
            "lmt": "5",
        }
    )
    try:
        payload = _fetch_provider_json(
            fetcher,
            url,
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/138 Safari/537.36"
                ),
                "Referer": "https://quote.eastmoney.com/",
            },
        )
    except RuntimeError:
        payload = _fetch_provider_json(fetcher, url, None)
    return _parse_eastmoney_daily(symbol, payload)


def _pull_tushare_daily(
    symbol: str,
    token: str,
    post_json: JsonPoster,
) -> PriceRow | None:
    api_name = _tushare_market_api_name(symbol)
    ts_code = _tushare_symbol(symbol)
    if not api_name or not ts_code:
        return None
    payload = _post_provider_json(
        post_json,
        "https://api.tushare.pro",
        {"Content-Type": "application/json"},
        {
            "api_name": api_name,
            "token": token,
            "params": {"ts_code": ts_code},
            "fields": "ts_code,trade_date,open,high,low,close,vol",
        },
    )
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if code not in {0, "0", None}:
        message = str(payload.get("msg") or "未知错误")
        if str(code) == "40203":
            raise RuntimeError(
                f"Tushare {api_name} 接口权限不足（40203）: {message}"
            )
        raise RuntimeError(f"Tushare {api_name} 接口返回 {code}: {message}")
    return _parse_tushare_daily(symbol, api_name, payload)


def _pull_yahoo_chart(symbol: str, fetcher: JsonFetcher) -> PriceRow | None:
    yahoo_symbol = _yahoo_chart_symbol(symbol)
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(yahoo_symbol, safe="")
        + "?"
        + urllib.parse.urlencode({"range": "5d", "interval": "1d"})
    )
    payload = _fetch_provider_json(
        fetcher,
        url,
        {"User-Agent": "Mozilla/5.0 LycheeAlphaDesk/0.1"},
    )
    return _parse_yahoo_chart(symbol, payload)


def _parse_alpha_vantage_daily(symbol: str, payload: object) -> PriceRow | None:
    if not isinstance(payload, dict):
        return None
    raw_series = payload.get("Time Series (Daily)")
    if not isinstance(raw_series, dict) or not raw_series:
        return None
    latest_date = sorted(raw_series, reverse=True)[0]
    latest = raw_series.get(latest_date)
    if not isinstance(latest, dict):
        return None
    close = latest.get("4. close")
    volume = latest.get("5. volume")
    if not isinstance(close, str) or not isinstance(volume, str):
        return None
    return PriceRow(
        symbol=symbol.upper(),
        date=latest_date,
        close=float(close),
        volume=int(float(volume)),
        currency=_infer_symbol_currency(symbol),
    )


def _parse_eastmoney_daily(
    symbol: str,
    payload: object,
) -> PriceRow | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    klines = data.get("klines")
    if not isinstance(klines, list) or not klines:
        return None
    latest = klines[-1]
    if not isinstance(latest, str):
        return None
    parts = latest.split(",")
    if len(parts) < 6:
        return None
    date = parts[0] or datetime.now(UTC).date().isoformat()
    return PriceRow(
        symbol=symbol.upper(),
        date=date or datetime.now(UTC).date().isoformat(),
        close=float(parts[2]),
        volume=int(float(parts[5])),
        currency=_infer_symbol_currency(symbol),
    )


def _parse_tushare_daily(
    symbol: str,
    api_name: str,
    payload: dict[str, object],
) -> PriceRow | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not isinstance(items, list) or not items:
        return None
    latest = items[0]
    if not isinstance(latest, list) or len(latest) != len(fields):
        return None
    row = {
        str(field): value
        for field, value in zip(fields, latest, strict=True)
        if isinstance(field, str)
    }
    trade_date = str(row.get("trade_date") or "").strip()
    close = _number_value(row.get("close"))
    volume = _number_value(row.get("vol"))
    if not trade_date or close is None or volume is None:
        return None
    return PriceRow(
        symbol=symbol.upper(),
        date=_tushare_trade_date(trade_date),
        close=close,
        volume=int(volume * _tushare_volume_multiplier(api_name)),
        currency=_infer_symbol_currency(symbol),
    )


def _tushare_market_api_name(symbol: str) -> str | None:
    normalized = symbol.upper()
    if normalized.endswith(".HK"):
        return "hk_daily"
    if normalized.endswith((".SH", ".SS", ".SZ")):
        return "fund_daily" if _is_tushare_fund_symbol(normalized) else "daily"
    return None


def _tushare_symbol(symbol: str) -> str | None:
    normalized = symbol.upper()
    if normalized.endswith(".HK"):
        return f"{normalized.removesuffix('.HK').zfill(5)}.HK"
    if normalized.endswith(".SS"):
        return f"{normalized.removesuffix('.SS')}.SH"
    if normalized.endswith((".SH", ".SZ")):
        return normalized
    return None


def _is_tushare_fund_symbol(symbol: str) -> bool:
    code = symbol.rsplit(".", 1)[0]
    if symbol.endswith(".SH"):
        return code.startswith(("51", "52", "56", "58", "59"))
    if symbol.endswith(".SZ"):
        return code.startswith(("15", "16", "18"))
    return False


def _tushare_volume_multiplier(api_name: str) -> int:
    return 1 if api_name == "hk_daily" else 100


def _tushare_trade_date(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _number_value(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse_yahoo_chart(symbol: str, payload: object) -> PriceRow | None:
    if not isinstance(payload, dict):
        return None
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return None
    result = results[0]
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        return None
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        return None
    quote = quotes[0]
    closes = quote.get("close")
    volumes = quote.get("volume")
    if not isinstance(closes, list):
        return None
    latest_index = _latest_numeric_index(closes)
    if latest_index is None:
        return None
    close = closes[latest_index]
    timestamp = timestamps[latest_index] if latest_index < len(timestamps) else None
    volume = (
        volumes[latest_index]
        if isinstance(volumes, list) and latest_index < len(volumes)
        else 0
    )
    meta = result.get("meta")
    currency = _infer_symbol_currency(symbol)
    if isinstance(meta, dict):
        meta_currency = meta.get("currency")
        if isinstance(meta_currency, str):
            currency = meta_currency
    return PriceRow(
        symbol=symbol.upper(),
        date=_date_from_epoch(timestamp),
        close=float(close),
        volume=int(float(volume or 0)),
        currency=currency,
    )


def _latest_numeric_index(values: list[object]) -> int | None:
    for index in range(len(values) - 1, -1, -1):
        if isinstance(values[index], int | float):
            return index
    return None


def _date_from_epoch(value: object) -> str:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC).date().isoformat()
    return datetime.now(UTC).date().isoformat()


def _eastmoney_secid(symbol: str) -> str | None:
    normalized = symbol.upper()
    if normalized.endswith(".HK"):
        code = normalized.removesuffix(".HK").zfill(5)
        return f"116.{code}"
    if normalized.endswith(".SH") or normalized.endswith(".SS"):
        return f"1.{normalized.rsplit('.', 1)[0]}"
    if normalized.endswith(".SZ"):
        return f"0.{normalized.removesuffix('.SZ')}"
    return None


def _yahoo_chart_symbol(symbol: str) -> str:
    normalized = symbol.upper()
    if normalized.endswith(".SH"):
        return normalized.removesuffix(".SH") + ".SS"
    return normalized


def _infer_symbol_currency(symbol: str) -> str:
    normalized = symbol.upper()
    if normalized.endswith(".HK"):
        return "HKD"
    if normalized.endswith((".SH", ".SZ", ".SS")):
        return "CNY"
    return "USD"


def _infer_symbol_market(symbol: str) -> str:
    normalized = symbol.upper()
    if normalized.endswith(".HK"):
        return "HK"
    if normalized.endswith((".SH", ".SZ", ".SS")):
        return "CN"
    return "US"


def _normalized_symbols(symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        cleaned = symbol.strip().upper()
        if cleaned and cleaned not in seen:
            normalized.append(cleaned)
            seen.add(cleaned)
    return normalized


def _parse_cboe_volatility_history(text: str) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    reader = csv.DictReader(StringIO(text))
    for row in reader:
        try:
            observed_at = datetime.strptime(str(row.get("DATE") or ""), "%m/%d/%Y").date()
            close = float(str(row.get("CLOSE") or ""))
        except ValueError:
            continue
        rows.append((observed_at, close))
    return sorted(rows, key=lambda item: item[0])


def _build_cboe_volatility_metrics(
    *,
    symbol: str,
    index_name: str,
    source_url: str,
    history: list[tuple[date, float]],
) -> list[ResearchMetric]:
    observed_at, latest_close = history[-1]
    as_of = observed_at.isoformat()
    note = (
        f"{index_name} 是 Cboe 发布的纳斯达克 100 30 天隐含波动率指标；"
        "用于研究风险背景，不代表交易指令。"
    )
    rows = [
        ResearchMetric(
            symbol=symbol,
            domain="volatility_metrics",
            name=f"Cboe {index_name} 收盘",
            value=f"{latest_close:.2f}",
            as_of=as_of,
            source_url=source_url,
            note=note,
            provider="cboe",
        )
    ]
    if len(history) >= 21:
        prior_close = history[-21][1]
        change = latest_close / prior_close - 1 if prior_close else 0.0
        rows.append(
            ResearchMetric(
                symbol=symbol,
                domain="volatility_metrics",
                name=f"Cboe {index_name} 20 交易日变化",
                value=f"{change:+.2%}",
                as_of=as_of,
                source_url=source_url,
                note="与 20 个可用交易日之前的收盘读数比较。",
                provider="cboe",
            )
        )
    if len(history) >= 252:
        trailing_closes = [close for _, close in history[-252:]]
        percentile = 100 * sum(close <= latest_close for close in trailing_closes) / len(
            trailing_closes
        )
        rows.append(
            ResearchMetric(
                symbol=symbol,
                domain="volatility_metrics",
                name=f"Cboe {index_name} 近一年历史分位",
                value=f"{percentile:.1f}%",
                as_of=as_of,
                source_url=source_url,
                note="按最近 252 个可用交易日收盘读数计算。",
                provider="cboe",
            )
        )
    return rows


def _news_provider_candidates(
    config: AlphaDeskConfig,
    provider_id: str,
    symbols: list[str],
    query: str | None,
    news_provider_registry: NewsProviderRegistry,
) -> list[str]:
    if provider_id != "auto":
        if provider_id in {"gdelt", "marketaux", "finnhub", "newsapi"}:
            if not symbols and provider_id == "finnhub":
                raise ValueError(
                    "Finnhub 当前仅支持个股新闻；市场级新闻请使用 Marketaux 或 NewsAPI。"
                )
            return [provider_id]
        plugin = news_provider_registry.providers.get(provider_id)
        if plugin is None:
            installed = ", ".join(sorted(news_provider_registry.providers)) or "无"
            raise ValueError(
                f"未安装新闻插件 '{provider_id}'。当前已安装插件: {installed}。"
            )
        _validate_news_plugin_request(
            plugin,
            config,
            symbols,
            query,
        )
        return [provider_id]

    candidates = [
        plugin.metadata.provider_id
        for plugin in sorted(
            news_provider_registry.providers.values(),
            key=lambda item: (item.metadata.priority, item.metadata.provider_id),
        )
        if _news_plugin_is_eligible(plugin, config, symbols, query)
    ]
    provider_order = (
        ("marketaux", "newsapi")
        if not symbols
        else ("marketaux", "finnhub", "newsapi")
    )
    for candidate in provider_order:
        provider = config.providers[candidate]
        if provider.value and provider.value.strip():
            candidates.append(candidate)
    candidates.append("gdelt")
    return candidates


def _news_plugin_is_eligible(
    plugin: NewsProviderPlugin,
    config: AlphaDeskConfig,
    symbols: list[str],
    query: str | None,
) -> bool:
    plugin_config = config.provider_plugins.get(plugin.metadata.provider_id)
    if plugin_config is None or not plugin_config.enabled:
        return False
    return not _missing_news_plugin_request_requirements(
        plugin,
        plugin_config.settings,
        symbols,
        query,
    )


def _validate_news_plugin_request(
    plugin: NewsProviderPlugin,
    config: AlphaDeskConfig,
    symbols: list[str],
    query: str | None,
) -> None:
    plugin_config = config.provider_plugins.get(plugin.metadata.provider_id)
    settings = plugin_config.settings if plugin_config and plugin_config.enabled else {}
    missing = _missing_news_plugin_request_requirements(plugin, settings, symbols, query)
    if not missing:
        return
    if missing[0] == "capability":
        required = _required_news_plugin_capability(symbols, query)
        raise ValueError(
            f"{plugin.metadata.display_name} 不支持当前请求；需要能力 '{required}'。"
        )
    missing_text = ", ".join(missing)
    raise ValueError(
        f"{plugin.metadata.display_name} 尚未完成配置，缺少: {missing_text}。"
        f"请运行 `lychee setup plugin set {plugin.metadata.provider_id} <设置项> <值>`。"
    )


def _missing_news_plugin_request_requirements(
    plugin: NewsProviderPlugin,
    settings: dict[str, str],
    symbols: list[str],
    query: str | None,
) -> tuple[str, ...]:
    required_capability = _required_news_plugin_capability(symbols, query)
    if required_capability not in plugin.metadata.capabilities:
        return ("capability",)
    return missing_required_settings(plugin, settings)


def _required_news_plugin_capability(
    symbols: list[str],
    query: str | None,
) -> str:
    if symbols:
        return "entity_news"
    if query and query.strip():
        return "topic_news"
    return "market_news"


def _news_provider_display_name(
    provider_id: str,
    news_provider_registry: NewsProviderRegistry,
) -> str:
    plugin = news_provider_registry.providers.get(provider_id)
    if plugin is not None:
        return plugin.metadata.display_name
    return _provider_display_name(provider_id)


def _pull_news_for_provider(
    *,
    provider_id: str,
    symbols: list[str],
    query: str | None,
    start: str,
    end: str,
    config: AlphaDeskConfig,
    fetch_json: JsonFetcher,
    news_provider_registry: NewsProviderRegistry,
) -> list[NewsEvent]:
    plugin = news_provider_registry.providers.get(provider_id)
    if plugin is not None:
        plugin_config = config.provider_plugins.get(provider_id)
        settings = plugin_config.settings if plugin_config else {}
        try:
            return pull_plugin_news(
                plugin,
                NewsProviderRequest(
                    symbols=tuple(symbols),
                    query=query,
                    start_date=start,
                    end_date=end,
                    settings=settings,
                ),
            )
        except Exception as error:  # Third-party plugin boundary; do not leak settings.
            message = _sanitize_news_plugin_error(str(error), settings)
            raise RuntimeError(f"{plugin.metadata.display_name} 插件拉取失败: {message}") from error

    if provider_id == "gdelt":
        return _pull_gdelt_news(symbols, query, fetch_json)
    if provider_id == "finnhub":
        if query and query.strip():
            raise ValueError("Finnhub 当前不支持主题关键词新闻查询。")
        api_key = _configured_value(config.providers["finnhub"].value, "Finnhub")
        return _pull_finnhub_news(symbols, start, end, api_key, fetch_json)
    if provider_id == "marketaux":
        api_key = _configured_value(config.providers["marketaux"].value, "Marketaux")
        return _pull_marketaux_news(symbols, query, api_key, fetch_json)
    if provider_id == "newsapi":
        api_key = _configured_value(config.providers["newsapi"].value, "NewsAPI")
        return _pull_newsapi_events(symbols, query, start, end, api_key, fetch_json)
    raise ValueError(f"不支持的新闻数据源: {provider_id}")


def _sanitize_news_plugin_error(message: str, settings: dict[str, str]) -> str:
    sanitized = _sanitize_error_message(message)
    for value in settings.values():
        secret = value.strip()
        if secret:
            sanitized = sanitized.replace(secret, "***")
    return sanitized


def _date_window(start_date: str | None, end_date: str | None) -> tuple[str, str]:
    if start_date and end_date:
        return start_date, end_date
    end = datetime.now(UTC).date()
    start = end - timedelta(days=7)
    return start.isoformat(), end.isoformat()


def _pull_finnhub_news(
    symbols: list[str],
    start: str,
    end: str,
    api_key: str,
    fetch_json: JsonFetcher,
) -> list[NewsEvent]:
    rows: list[NewsEvent] = []
    for symbol in symbols:
        url = "https://finnhub.io/api/v1/company-news?" + urllib.parse.urlencode(
            {"symbol": symbol, "from": start, "to": end, "token": api_key}
        )
        payload = _fetch_provider_json(fetch_json, url, None)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if isinstance(item, dict):
                rows.append(
                    NewsEvent(
                        timestamp=_timestamp_from_epoch(item.get("datetime")),
                        headline=str(item.get("headline") or ""),
                        summary=str(item.get("summary") or ""),
                        symbols=[symbol],
                        source_url=str(item.get("url") or ""),
                        is_symbol_scoped=True,
                    )
                )
    return rows


def _pull_gdelt_news(
    symbols: list[str],
    query: str | None,
    fetch_json: JsonFetcher,
) -> list[NewsEvent]:
    targets = _gdelt_targets(symbols, query)
    rows: list[NewsEvent] = []
    for target_symbols, target_query in targets:
        url = GDELT_DOC_ENDPOINT + "?" + urllib.parse.urlencode(
            {
                "query": target_query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": "10",
                "timespan": "1week",
            }
        )
        payload = _fetch_provider_json(fetch_json, url, None)
        if not isinstance(payload, dict):
            continue
        articles = payload.get("articles")
        if not isinstance(articles, list):
            continue
        for article in articles:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            source_url = str(article.get("url") or "").strip()
            if not title or not source_url:
                continue
            rows.append(
                NewsEvent(
                    timestamp=_gdelt_timestamp(article.get("seendate")),
                    headline=title,
                    summary=_gdelt_article_metadata(article),
                    symbols=target_symbols,
                    source_url=source_url,
                    is_symbol_scoped=_is_symbol_scoped_news_target(target_symbols),
                )
            )
    return rows


def _gdelt_targets(
    symbols: list[str],
    query: str | None,
) -> list[tuple[list[str], str]]:
    if not symbols:
        return [([MARKET_NEWS_SYMBOL], query.strip() if query else MARKET_NEWS_QUERY)]
    targets: list[tuple[list[str], str]] = []
    for symbol in symbols:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            continue
        mapped_entity = NEWS_ENTITY_QUERIES.get(normalized_symbol)
        entity_query = mapped_entity or normalized_symbol
        if query and query.strip():
            entity_query = (
                f"({entity_query}) ({query.strip()})"
                if mapped_entity
                else query.strip()
            )
        targets.append(([normalized_symbol], entity_query))
    return targets


def _gdelt_timestamp(value: object) -> str:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return text


def _gdelt_article_metadata(article: dict[str, object]) -> str:
    parts = [
        str(article.get("domain") or "").strip(),
        str(article.get("language") or "").strip(),
        str(article.get("sourcecountry") or "").strip(),
    ]
    metadata = " | ".join(part for part in parts if part)
    return f"GDELT 文章元数据: {metadata}" if metadata else "GDELT 文章元数据。"


def _pull_marketaux_news(
    symbols: list[str],
    query: str | None,
    api_key: str,
    fetch_json: JsonFetcher,
) -> list[NewsEvent]:
    params = {
        "api_token": api_key,
        "language": "en",
        "limit": "20",
    }
    if query and query.strip():
        params["search"] = query.strip()
    if symbols:
        params["symbols"] = ",".join(symbols)
    else:
        params["countries"] = "us,hk,cn"
    url = "https://api.marketaux.com/v1/news/all?" + urllib.parse.urlencode(params)
    payload = _fetch_provider_json(fetch_json, url, None)
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    rows: list[NewsEvent] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rows.append(
            NewsEvent(
                timestamp=str(item.get("published_at") or ""),
                headline=str(item.get("title") or ""),
                summary=str(item.get("description") or ""),
                symbols=_marketaux_symbols(item, symbols),
                source_url=str(item.get("url") or ""),
            )
        )
    return rows


def _pull_newsapi_events(
    symbols: list[str],
    query: str | None,
    start: str,
    end: str,
    api_key: str,
    fetch_json: JsonFetcher,
) -> list[NewsEvent]:
    rows: list[NewsEvent] = []
    for target_symbols, effective_query in _newsapi_targets(symbols, query):
        url = "https://newsapi.org/v2/everything?" + urllib.parse.urlencode(
            {
                "q": effective_query,
                "from": start,
                "to": end,
                "sortBy": "publishedAt",
                "pageSize": "20",
                "apiKey": api_key,
            }
        )
        payload = _fetch_provider_json(fetch_json, url, None)
        if not isinstance(payload, dict):
            continue
        articles = payload.get("articles")
        if not isinstance(articles, list):
            continue
        for item in articles:
            if not isinstance(item, dict):
                continue
            rows.append(
                NewsEvent(
                    timestamp=str(item.get("publishedAt") or ""),
                    headline=str(item.get("title") or ""),
                    summary=str(item.get("description") or ""),
                    symbols=target_symbols,
                    source_url=str(item.get("url") or ""),
                    is_symbol_scoped=_is_symbol_scoped_news_target(target_symbols),
                )
            )
    return rows


def _newsapi_targets(
    symbols: list[str],
    query: str | None,
) -> list[tuple[list[str], str]]:
    if not symbols:
        return [
            (
                [MARKET_NEWS_SYMBOL],
                query.strip() if query and query.strip() else MARKET_NEWS_QUERY,
            )
        ]
    targets: list[tuple[list[str], str]] = []
    for symbol in symbols:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            continue
        mapped_entity = NEWS_ENTITY_QUERIES.get(normalized_symbol)
        entity_query = mapped_entity or normalized_symbol
        if query and query.strip():
            effective_query = (
                f"({entity_query}) AND ({query.strip()})"
                if mapped_entity
                else query.strip()
            )
        else:
            effective_query = entity_query
        targets.append(([normalized_symbol], effective_query))
    return targets


def _is_symbol_scoped_news_target(symbols: list[str]) -> bool:
    return len(symbols) == 1 and symbols[0].upper() != MARKET_NEWS_SYMBOL


def _marketaux_symbols(item: dict[str, object], fallback: list[str]) -> list[str]:
    entities = item.get("entities")
    if not isinstance(entities, list):
        return fallback or [MARKET_NEWS_SYMBOL]
    symbols: list[str] = []
    for entity in entities:
        if isinstance(entity, dict) and isinstance(entity.get("symbol"), str):
            symbols.append(entity["symbol"])
    return symbols or fallback or [MARKET_NEWS_SYMBOL]


def _timestamp_from_epoch(value: object) -> str:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds")
    return ""


def _parse_sec_company_tickers(
    payload: object,
) -> tuple[dict[str, int], dict[str, str]]:
    cik_by_symbol: dict[str, int] = {}
    company_by_symbol: dict[str, str] = {}
    if not isinstance(payload, dict):
        return cik_by_symbol, company_by_symbol
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        ticker = item.get("ticker")
        cik = item.get("cik_str")
        title = item.get("title")
        if isinstance(ticker, str) and isinstance(cik, int):
            symbol = ticker.upper()
            cik_by_symbol[symbol] = cik
            if isinstance(title, str):
                company_by_symbol[symbol] = title
    return cik_by_symbol, company_by_symbol


def _common_sec_company_maps() -> tuple[dict[str, int], dict[str, str]]:
    cik_by_symbol: dict[str, int] = {}
    company_by_symbol: dict[str, str] = {}
    _merge_common_sec_companies(cik_by_symbol, company_by_symbol)
    return cik_by_symbol, company_by_symbol


def _merge_common_sec_companies(
    cik_by_symbol: dict[str, int],
    company_by_symbol: dict[str, str],
) -> None:
    for symbol, (cik, company) in COMMON_SEC_COMPANIES.items():
        cik_by_symbol.setdefault(symbol, cik)
        company_by_symbol.setdefault(symbol, company)


def _parse_sec_recent_filings(
    *,
    cik: int,
    symbol: str,
    company: str,
    payload: object,
    limit: int,
) -> list[FilingSummary]:
    if not isinstance(payload, dict):
        return []
    filings = payload.get("filings")
    if not isinstance(filings, dict):
        return []
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        return []

    forms = _string_list(recent.get("form"))
    dates = _string_list(recent.get("filingDate"))
    accessions = _string_list(recent.get("accessionNumber"))
    documents = _string_list(recent.get("primaryDocument"))

    rows: list[FilingSummary] = []
    for form, filing_date, accession, document in list(
        zip(forms, dates, accessions, documents, strict=False)
    )[:limit]:
        accession_path = accession.replace("-", "")
        rows.append(
            FilingSummary(
                date=filing_date,
                company=company,
                form=form,
                summary=f"{symbol} 在 {filing_date} 提交了 {form}。",
                source_url=(
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{cik}/{accession_path}/{document}"
                ),
                symbol=symbol,
            )
        )
    return rows


def _parse_hkex_active_stock_ids(payload: object) -> dict[str, str]:
    if not isinstance(payload, list):
        return {}
    stock_ids: dict[str, str] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        code = str(row.get("c") or "").strip().upper()
        stock_id = str(row.get("i") or "").strip()
        if code and stock_id:
            stock_ids[code] = stock_id
    return stock_ids


def _hkex_stock_code(symbol: str) -> str:
    return symbol.upper().split(".", maxsplit=1)[0].zfill(5)


def _parse_hkex_recent_filings(
    *,
    symbol: str,
    html: str,
    limit: int,
) -> list[FilingSummary]:
    parser = _HKEXTitleSearchParser()
    parser.feed(html)
    parser.close()
    rows: list[FilingSummary] = []
    for announcement in parser.rows[:limit]:
        filing_date = _hkex_release_date(announcement.release_time)
        if not filing_date:
            continue
        rows.append(
            FilingSummary(
                date=filing_date,
                company=announcement.company or symbol,
                form="HKEX 公告",
                summary=f"HKEXnews 公告: {announcement.headline}",
                source_url=urllib.parse.urljoin(
                    HKEX_NEWS_BASE_URL,
                    announcement.source_path,
                ),
                symbol=symbol,
            )
        )
    return rows


def _hkex_release_date(value: str) -> str:
    cleaned = value.replace("Release Time:", "").strip()
    try:
        return datetime.strptime(cleaned, "%d/%m/%Y %H:%M").date().isoformat()
    except ValueError:
        return ""


@dataclass(frozen=True)
class _HKEXTitleSearchAnnouncement:
    release_time: str
    company: str
    headline: str
    source_path: str


class _HKEXTitleSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_HKEXTitleSearchAnnouncement] = []
        self._row: dict[str, str] | None = None
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self._row = {
                "release_time": "",
                "company": "",
                "headline": "",
                "source_path": "",
            }
            self._capture = None
            return
        if self._row is None:
            return
        class_names = set((attributes.get("class") or "").split())
        if tag == "td":
            if "release-time" in class_names:
                self._capture = "release_time"
            elif "stock-short-name" in class_names:
                self._capture = "company"
            return
        if tag == "div" and "headline" in class_names:
            self._capture = "headline"
            return
        if tag == "a" and attributes.get("href"):
            self._row["source_path"] = attributes["href"] or ""
            return
        if tag == "br" and self._capture:
            self._row[self._capture] += "\n"

    def handle_endtag(self, tag: str) -> None:
        if self._row is None:
            return
        if tag in {"td", "div"}:
            self._capture = None
            return
        if tag != "tr":
            return
        release_time = _compact_hkex_text(self._row["release_time"])
        company = _hkex_company_name(self._row["company"])
        headline = _compact_hkex_text(self._row["headline"])
        source_path = self._row["source_path"].strip()
        if release_time and headline and source_path:
            self.rows.append(
                _HKEXTitleSearchAnnouncement(
                    release_time=release_time,
                    company=company,
                    headline=headline,
                    source_path=source_path,
                )
            )
        self._row = None
        self._capture = None

    def handle_data(self, data: str) -> None:
        if self._row is not None and self._capture:
            self._row[self._capture] += data


def _compact_hkex_text(value: str) -> str:
    return " ".join(value.split())


def _hkex_company_name(value: str) -> str:
    cleaned = value.replace("Stock Short Name:", "").strip()
    for line in cleaned.splitlines():
        compact = _compact_hkex_text(line)
        if compact:
            return compact
    return _compact_hkex_text(cleaned)


@dataclass(frozen=True)
class _CNINFOStock:
    code: str
    organization_id: str
    company: str


def _parse_cninfo_stocks(payload: object) -> dict[str, _CNINFOStock]:
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("stockList")
    if not isinstance(rows, list):
        return {}
    stocks: dict[str, _CNINFOStock] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip().upper()
        organization_id = str(row.get("orgId") or "").strip()
        company = _compact_cninfo_text(str(row.get("zwjc") or ""))
        if code and organization_id:
            stocks[code] = _CNINFOStock(code, organization_id, company or code)
    return stocks


def _cninfo_stock_code(symbol: str) -> str:
    return symbol.upper().split(".", maxsplit=1)[0]


def _cninfo_announcement_payload(stock: _CNINFOStock) -> dict[str, str]:
    return {
        "pageNum": "1",
        "pageSize": "30",
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{stock.code},{stock.organization_id}",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }


def _parse_cninfo_recent_filings(
    *,
    symbol: str,
    company: str,
    payload: object,
    limit: int,
) -> list[FilingSummary]:
    if not isinstance(payload, dict):
        return []
    announcements = payload.get("announcements")
    if not isinstance(announcements, list):
        return []
    rows: list[FilingSummary] = []
    for announcement in announcements[:limit]:
        if not isinstance(announcement, dict):
            continue
        filing_date = _cninfo_release_date(announcement.get("announcementTime"))
        title = _compact_cninfo_text(str(announcement.get("announcementTitle") or ""))
        source_path = str(announcement.get("adjunctUrl") or "").strip()
        if not filing_date or not title or not source_path:
            continue
        rows.append(
            FilingSummary(
                date=filing_date,
                company=_compact_cninfo_text(
                    str(announcement.get("secName") or company or symbol)
                ),
                form="巨潮公告",
                summary=f"巨潮资讯公告: {title}",
                source_url=urllib.parse.urljoin(CNINFO_STATIC_BASE_URL, source_path),
                symbol=symbol,
            )
        )
    return rows


def _cninfo_release_date(value: object) -> str:
    if isinstance(value, int | float):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, CNINFO_TIMEZONE).date().isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _compact_cninfo_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", unescape(value))
    return " ".join(without_tags.split())


def _parse_sec_financial_snapshot(
    *,
    symbol: str,
    company: str,
    cik: int,
    source_url: str,
    payload: object,
) -> FinancialSnapshot | None:
    revenue = _latest_sec_fact_value(
        payload,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ],
    )
    net_income = _latest_sec_fact_value(payload, ["NetIncomeLoss"])
    operating_cash_flow = _latest_sec_fact_value(
        payload,
        ["NetCashProvidedByUsedInOperatingActivities"],
    )
    revenue_prior = _comparable_sec_fact_value(payload, revenue)
    net_income_prior = _comparable_sec_fact_value(payload, net_income)
    operating_cash_flow_prior = _comparable_sec_fact_value(payload, operating_cash_flow)
    facts = [fact for fact in [revenue, net_income, operating_cash_flow] if fact]
    if not facts:
        return None

    latest = max(facts, key=_sec_fact_sort_key)
    return FinancialSnapshot(
        symbol=symbol,
        company=company,
        cik=cik,
        form=_sec_fact_text(latest, "form"),
        fiscal_year=_sec_fact_int(latest, "fy"),
        fiscal_period=_sec_fact_text(latest, "fp"),
        period_end=_sec_fact_text(latest, "end"),
        filing_date=_sec_fact_text(latest, "filed"),
        currency="USD",
        revenue=_sec_fact_number(revenue),
        revenue_period_start=_sec_fact_text(revenue or {}, "start"),
        revenue_period_end=_sec_fact_text(revenue or {}, "end"),
        revenue_prior=_sec_fact_number(revenue_prior),
        revenue_prior_period_start=_sec_fact_text(revenue_prior or {}, "start"),
        revenue_prior_period_end=_sec_fact_text(revenue_prior or {}, "end"),
        net_income=_sec_fact_number(net_income),
        net_income_period_start=_sec_fact_text(net_income or {}, "start"),
        net_income_period_end=_sec_fact_text(net_income or {}, "end"),
        net_income_prior=_sec_fact_number(net_income_prior),
        net_income_prior_period_start=_sec_fact_text(net_income_prior or {}, "start"),
        net_income_prior_period_end=_sec_fact_text(net_income_prior or {}, "end"),
        operating_cash_flow=_sec_fact_number(operating_cash_flow),
        operating_cash_flow_period_start=_sec_fact_text(operating_cash_flow or {}, "start"),
        operating_cash_flow_period_end=_sec_fact_text(operating_cash_flow or {}, "end"),
        operating_cash_flow_prior=_sec_fact_number(operating_cash_flow_prior),
        operating_cash_flow_prior_period_start=_sec_fact_text(
            operating_cash_flow_prior or {}, "start"
        ),
        operating_cash_flow_prior_period_end=_sec_fact_text(
            operating_cash_flow_prior or {}, "end"
        ),
        source_url=source_url,
    )


def _latest_sec_fact_value(
    payload: object,
    concepts: list[str],
) -> dict[str, object] | None:
    candidates = _sec_fact_candidates(payload, concepts)
    return max(candidates, key=_sec_fact_sort_key) if candidates else None


def _sec_fact_candidates(
    payload: object,
    concepts: list[str],
) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        return []
    us_gaap = facts.get("us-gaap")
    if not isinstance(us_gaap, dict):
        return []

    candidates: list[dict[str, object]] = []
    for concept in concepts:
        fact = us_gaap.get(concept)
        if not isinstance(fact, dict):
            continue
        units = fact.get("units")
        if not isinstance(units, dict):
            continue
        values = units.get("USD")
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            if _sec_fact_text(value, "form") not in {"10-Q", "10-K"}:
                continue
            if _sec_fact_number(value) is None:
                continue
            candidates.append({**value, "_concept": concept})
    return candidates


def _comparable_sec_fact_value(
    payload: object,
    current: dict[str, object] | None,
) -> dict[str, object] | None:
    if current is None:
        return None
    concept = _sec_fact_text(current, "_concept")
    current_end = _sec_fact_date(current, "end")
    current_start = _sec_fact_date(current, "start")
    if not concept or current_end is None or current_start is None:
        return None

    current_form = _sec_fact_text(current, "form")
    current_period = _sec_fact_text(current, "fp")
    candidates: list[dict[str, object]] = []
    for candidate in _sec_fact_candidates(payload, [concept]):
        candidate_end = _sec_fact_date(candidate, "end")
        candidate_start = _sec_fact_date(candidate, "start")
        if candidate_end is None or candidate_start is None:
            continue
        if _sec_fact_text(candidate, "form") != current_form:
            continue
        if current_period and _sec_fact_text(candidate, "fp") != current_period:
            continue
        year_gap = (current_end - candidate_end).days
        if not 330 <= year_gap <= 400:
            continue
        duration_delta = abs(
            (current_end - current_start).days - (candidate_end - candidate_start).days
        )
        if duration_delta > 14:
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: _sec_fact_comparison_key(
            current_end,
            current_start,
            candidate,
        ),
    )


def _sec_fact_comparison_key(
    current_end: date,
    current_start: date,
    candidate: dict[str, object],
) -> tuple[int, int, tuple[str, str, str]]:
    candidate_end = _sec_fact_date(candidate, "end")
    candidate_start = _sec_fact_date(candidate, "start")
    if candidate_end is None or candidate_start is None:
        return (9999, 9999, _sec_fact_sort_key(candidate))
    return (
        abs((current_end - candidate_end).days - 365),
        abs(
            (current_end - current_start).days
            - (candidate_end - candidate_start).days
        ),
        _sec_fact_sort_key(candidate),
    )


def _sec_fact_sort_key(fact: dict[str, object]) -> tuple[str, str, str]:
    return (
        _sec_fact_text(fact, "filed"),
        _sec_fact_text(fact, "end"),
        _sec_fact_text(fact, "start"),
    )


def _sec_fact_text(fact: dict[str, object], key: str) -> str:
    value = fact.get(key)
    return value.strip() if isinstance(value, str) else ""


def _sec_fact_int(fact: dict[str, object], key: str) -> int | None:
    value = fact.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _sec_fact_date(fact: dict[str, object], key: str) -> date | None:
    value = _sec_fact_text(fact, key)
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _sec_fact_number(fact: dict[str, object] | None) -> int | float | None:
    if fact is None:
        return None
    value = fact.get("val")
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _write_cache(
    *,
    output_dir: Path,
    filename: str,
    provider: str,
    rows: list[dict[str, object]],
    warnings: list[str],
    now: datetime | None = None,
) -> Path:
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / filename
    output_path.write_text(
        json.dumps(
            {
                "provider": provider,
                "created_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
                "warnings": warnings,
                "rows": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _merge_market_cache_rows(
    output_dir: Path,
    new_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    existing = _read_cache(output_dir, "market-prices.json").rows
    new_symbols = {
        symbol
        for row in new_rows
        if (symbol := _cache_row_symbol(row))
    }
    preserved = [
        row
        for row in existing
        if (symbol := _cache_row_symbol(row)) and symbol not in new_symbols
    ]
    return preserved + new_rows


def _merge_symbol_cache_rows(
    *,
    output_dir: Path,
    filename: str,
    new_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    existing = _read_cache(output_dir, filename).rows
    new_symbols = {
        symbol
        for row in new_rows
        if (symbol := _cache_row_symbol(row))
    }
    preserved = [
        row
        for row in existing
        if (symbol := _cache_row_symbol(row)) and symbol not in new_symbols
    ]
    return preserved + new_rows


def _merge_research_metric_cache_rows(
    output_dir: Path,
    new_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    existing = _read_cache(output_dir, "research-metrics.json").rows
    new_keys = {
        key
        for row in new_rows
        if (key := _research_metric_cache_row_key(row))
    }
    preserved = [
        row
        for row in existing
        if (key := _research_metric_cache_row_key(row)) and key not in new_keys
    ]
    return preserved + new_rows


def _research_metric_cache_row_key(row: dict[str, object]) -> str:
    symbol = str(row.get("symbol") or "").strip().upper()
    domain = str(row.get("domain") or "").strip().lower()
    name = str(row.get("name") or "").strip().casefold()
    return f"{symbol}:{domain}:{name}" if symbol and domain and name else ""


def _merge_news_cache_rows(
    output_dir: Path,
    new_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    existing = _read_cache(output_dir, "news-events.json").rows
    keys: list[str] = []
    merged_by_key: dict[str, dict[str, object]] = {}
    for row in existing:
        key = _news_cache_row_key(row)
        if key in merged_by_key:
            continue
        keys.append(key)
        merged_by_key[key] = row
    for row in new_rows:
        key = _news_cache_row_key(row)
        if key not in merged_by_key:
            keys.append(key)
        # A fresh provider response upgrades stale rows from older cache schemas.
        merged_by_key[key] = row
    return [merged_by_key[key] for key in keys]


def _merge_filing_cache_rows(
    output_dir: Path,
    new_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    existing = _read_cache(output_dir, "filings.json").rows
    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in [*existing, *new_rows]:
        key = _filing_cache_row_key(row)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def _filing_cache_row_key(row: dict[str, object]) -> str:
    source_url = str(row.get("source_url") or "").strip()
    if source_url:
        return f"url:{source_url}"
    company = str(row.get("company") or "").strip().casefold()
    form = str(row.get("form") or "").strip().upper()
    filing_date = str(row.get("date") or "").strip()
    return f"filing:{company}:{form}:{filing_date}"


def _news_cache_row_key(row: dict[str, object]) -> str:
    source_url = str(row.get("source_url") or "").strip()
    if source_url:
        return f"url:{source_url}"
    headline = str(row.get("headline") or "").strip().lower()
    timestamp = str(row.get("timestamp") or "").strip()
    return f"headline:{headline}:{timestamp}"


def _matching_news_cache_rows(
    rows: list[dict[str, object]],
    symbols: list[str],
) -> list[dict[str, object]]:
    if not symbols:
        return rows
    requested = {symbol.upper() for symbol in symbols}
    return [
        row
        for row in rows
        if requested.intersection(_news_cache_row_symbols(row))
    ]


def _news_cache_covers_symbols(
    rows: list[dict[str, object]],
    symbols: list[str],
) -> bool:
    if not symbols:
        return bool(rows)
    cached_symbols: set[str] = set()
    for row in rows:
        cached_symbols.update(_news_cache_row_symbols(row))
    return all(symbol.upper() in cached_symbols for symbol in symbols)


def _news_cache_row_symbols(row: dict[str, object]) -> set[str]:
    raw_symbols = row.get("symbols")
    if not isinstance(raw_symbols, list):
        return set()
    return {
        str(symbol).strip().upper()
        for symbol in raw_symbols
        if str(symbol).strip()
    }


def _market_cache_covers_symbols(
    rows: list[dict[str, object]],
    symbols: list[str],
) -> bool:
    cached_symbols = {
        symbol
        for row in rows
        if (symbol := _cache_row_symbol(row))
    }
    return all(symbol.upper() in cached_symbols for symbol in symbols)


def _financial_cache_covers_symbols(
    rows: list[dict[str, object]],
    symbols: list[str],
) -> bool:
    return _market_cache_covers_symbols(rows, symbols)


def _read_cache(output_dir: Path, filename: str) -> _CachePayload:
    path = output_dir / "data" / filename
    if not path.exists():
        return _CachePayload(provider="", rows=[])
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return _CachePayload(provider="", rows=[])
    provider = payload.get("provider")
    rows = payload.get("rows")
    warnings = payload.get("warnings")
    return _CachePayload(
        provider=provider if isinstance(provider, str) else "",
        rows=[row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else [],
        warnings=tuple(item for item in warnings if isinstance(item, str))
        if isinstance(warnings, list)
        else (),
    )


def _cache_row_symbol(row: dict[str, object]) -> str:
    symbol = row.get("symbol")
    return symbol.upper() if isinstance(symbol, str) else ""


def _price_row_from_dict(row: dict[str, object]) -> PriceRow:
    return PriceRow(
        symbol=str(row["symbol"]),
        date=str(row["date"]),
        close=float(str(row["close"])),
        volume=int(str(row["volume"])),
        currency=str(row["currency"]),
    )


def _fund_metadata_from_dict(row: dict[str, object]) -> FundMetadata:
    return FundMetadata(
        symbol=str(row.get("symbol") or "").upper(),
        display_name=str(row.get("display_name") or ""),
        market=str(row.get("market") or ""),
        tracking_index=str(row.get("tracking_index") or ""),
        expense_ratio=str(row.get("expense_ratio") or ""),
        holdings_summary=str(row.get("holdings_summary") or ""),
        source_url=str(row.get("source_url") or ""),
        as_of=str(row.get("as_of") or ""),
        provider=str(row.get("provider") or ""),
    )


def _research_metric_from_dict(row: dict[str, object]) -> ResearchMetric:
    return ResearchMetric(
        symbol=str(row.get("symbol") or "").upper(),
        domain=str(row.get("domain") or ""),
        name=str(row.get("name") or ""),
        value=str(row.get("value") or ""),
        as_of=str(row.get("as_of") or ""),
        source_url=str(row.get("source_url") or ""),
        note=str(row.get("note") or ""),
        provider=str(row.get("provider") or ""),
    )


def _news_event_from_dict(row: dict[str, object]) -> NewsEvent:
    symbols = row.get("symbols")
    return NewsEvent(
        timestamp=str(row["timestamp"]),
        headline=str(row["headline"]),
        summary=str(row["summary"]),
        symbols=[str(symbol) for symbol in symbols] if isinstance(symbols, list) else [],
        source_url=str(row["source_url"]),
        is_symbol_scoped=row.get("is_symbol_scoped") is True,
    )


def _filing_summary_from_dict(row: dict[str, object]) -> FilingSummary:
    return FilingSummary(
        date=str(row["date"]),
        company=str(row["company"]),
        form=str(row["form"]),
        summary=str(row["summary"]),
        source_url=str(row["source_url"]),
        symbol=str(row.get("symbol") or "").upper(),
    )


def _financial_snapshot_from_dict(row: dict[str, object]) -> FinancialSnapshot:
    fiscal_year = row.get("fiscal_year")
    cik = _financial_integer_from_dict(row.get("cik"))
    return FinancialSnapshot(
        symbol=str(row.get("symbol") or "").upper(),
        company=str(row.get("company") or ""),
        cik=cik,
        form=str(row.get("form") or ""),
        fiscal_year=fiscal_year if isinstance(fiscal_year, int) else None,
        fiscal_period=str(row.get("fiscal_period") or ""),
        period_end=str(row.get("period_end") or ""),
        filing_date=str(row.get("filing_date") or ""),
        currency=str(row.get("currency") or "USD"),
        revenue=_financial_number_from_dict(row.get("revenue")),
        revenue_period_start=str(row.get("revenue_period_start") or ""),
        revenue_period_end=str(row.get("revenue_period_end") or ""),
        revenue_prior=_financial_number_from_dict(row.get("revenue_prior")),
        revenue_prior_period_start=str(row.get("revenue_prior_period_start") or ""),
        revenue_prior_period_end=str(row.get("revenue_prior_period_end") or ""),
        net_income=_financial_number_from_dict(row.get("net_income")),
        net_income_period_start=str(row.get("net_income_period_start") or ""),
        net_income_period_end=str(row.get("net_income_period_end") or ""),
        net_income_prior=_financial_number_from_dict(row.get("net_income_prior")),
        net_income_prior_period_start=str(row.get("net_income_prior_period_start") or ""),
        net_income_prior_period_end=str(row.get("net_income_prior_period_end") or ""),
        operating_cash_flow=_financial_number_from_dict(row.get("operating_cash_flow")),
        operating_cash_flow_period_start=str(row.get("operating_cash_flow_period_start") or ""),
        operating_cash_flow_period_end=str(row.get("operating_cash_flow_period_end") or ""),
        operating_cash_flow_prior=_financial_number_from_dict(
            row.get("operating_cash_flow_prior")
        ),
        operating_cash_flow_prior_period_start=str(
            row.get("operating_cash_flow_prior_period_start") or ""
        ),
        operating_cash_flow_prior_period_end=str(
            row.get("operating_cash_flow_prior_period_end") or ""
        ),
        source_url=str(row.get("source_url") or ""),
    )


def _financial_number_from_dict(value: object) -> int | float | None:
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _financial_integer_from_dict(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _cache_health_check(
    *,
    output_dir: Path,
    filename: str,
    name: str,
    noun: str,
) -> DataQualityCheck:
    path = output_dir / "data" / filename
    if not path.exists():
        return DataQualityCheck(
            name=name,
            status="error",
            provider="local-cache",
            message=f"缺少 {filename}；请先运行 `lychee data pull`。",
        )

    payload = _read_cache(output_dir, filename)
    if not payload.rows:
        return DataQualityCheck(
            name=name,
            status="warning",
            provider=payload.provider or "local-cache",
            message=f"{filename} 已存在，但不包含{noun}。",
        )

    if payload.warnings:
        return DataQualityCheck(
            name=name,
            status="warning",
            provider=payload.provider or "local-cache",
            message=(
                f"已加载 {len(payload.rows)} 条{noun}，"
                f"但最近数据源警告: {'；'.join(payload.warnings[:2])}"
            ),
        )

    return DataQualityCheck(
        name=name,
        status="pass",
        provider=payload.provider or "local-cache",
        message=f"已从 {filename} 加载 {len(payload.rows)} 条{noun}。",
    )


def _market_coverage_checks(cache: _CachePayload) -> list[DataQualityCheck]:
    return [
        _market_coverage_check(cache, market)
        for market in ("US", "HK", "CN")
    ]


def _market_coverage_check(cache: _CachePayload, market: str) -> DataQualityCheck:
    market_name = {"US": "美股", "HK": "港股", "CN": "A 股"}[market]
    market_rows = [
        row for row in cache.rows if _symbol_market(_cache_row_symbol(row)) == market
    ]
    if market_rows:
        latest_date = max(
            str(row.get("date") or "") for row in market_rows
        )
        date_suffix = f"，最新样本日期 {latest_date}" if latest_date else ""
        return DataQualityCheck(
            name=f"market-{market.lower()}-coverage",
            status="pass",
            provider=cache.provider or "local-cache",
            message=f"已缓存 {len(market_rows)} 条{market_name}行情{date_suffix}。",
        )

    entitlement = _tushare_entitlement_warning(cache.warnings, market)
    if entitlement:
        return DataQualityCheck(
            name=f"market-{market.lower()}-coverage",
            status="warning",
            provider="Tushare Pro",
            message=(
                f"{market_name}暂无可用行情缓存。Tushare `{entitlement}` 接口权限不足；"
                "请在 Tushare 后台开通对应接口或配置可用的替代数据源。"
            ),
        )

    return DataQualityCheck(
        name=f"market-{market.lower()}-coverage",
        status="warning",
        provider=cache.provider or "local-cache",
        message=(
            f"{market_name}暂无可用行情缓存。"
            "请先通过 `lychee data pull market --provider auto` 拉取一个观察代码。"
        ),
    )


def _symbol_market(symbol: str) -> str:
    normalized = symbol.upper()
    if normalized.endswith(".HK"):
        return "HK"
    if normalized.endswith((".SH", ".SS", ".SZ")):
        return "CN"
    return "US"


def _tushare_entitlement_warning(warnings: tuple[str, ...], market: str) -> str:
    market_suffixes = {
        "US": (),
        "HK": (".HK",),
        "CN": (".SH", ".SS", ".SZ"),
    }[market]
    if not market_suffixes:
        return ""
    for warning in warnings:
        if not any(suffix in warning.upper() for suffix in market_suffixes):
            continue
        match = re.search(r"Tushare ([a-z_]+) 接口权限不足", warning)
        if match:
            return match.group(1)
    return ""
