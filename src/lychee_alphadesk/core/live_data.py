import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import (
    evaluate_financials_cache,
    evaluate_market_cache,
    evaluate_news_cache,
    record_financials_cache,
    record_market_cache,
    record_news_cache,
)
from lychee_alphadesk.core.config import AlphaDeskConfig, load_config
from lychee_alphadesk.core.data_engine import DataQualityCheck, DataSnapshot
from lychee_alphadesk.providers.demo import (
    FilingSummary,
    NewsEvent,
    PriceRow,
)

JsonFetcher = Callable[[str, dict[str, str] | None], object]
JsonPoster = Callable[[str, dict[str, str] | None, dict[str, object]], object]

SEC_USER_AGENT = (
    "LycheeAlphaDesk/0.1 support@lychee.ai"
)
MARKET_NEWS_SYMBOL = "MARKET"
MARKET_NEWS_QUERY = (
    "stock market OR financial markets OR earnings OR central bank "
    "OR US stocks OR Hong Kong stocks OR China stocks"
)
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
    rows: list[NewsEvent] = []
    selected_provider = ""
    last_error: RuntimeError | None = None

    for candidate in _news_provider_candidates(active_config, provider_id, symbols):
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
            )
        except RuntimeError as error:
            last_error = RuntimeError(_sanitize_error_message(str(error)))
            if provider_id == "auto":
                warnings.append(_news_provider_retry_warning(candidate, last_error))
                continue
            raise last_error from error
        selected_provider = candidate
        break

    if not selected_provider:
        if last_error:
            raise last_error
        raise ValueError(
            "尚未配置新闻数据源。请配置 Marketaux、Finnhub 或 NewsAPI。"
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
    limit_per_symbol: int = 5,
) -> PullResult:
    headers = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}
    fetcher = fetch_json or _fetch_json
    rows: list[FilingSummary] = []
    warnings: list[str] = []
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

    for symbol in symbols:
        normalized_symbol = symbol.upper()
        cik = cik_by_symbol.get(normalized_symbol)
        company = company_by_symbol.get(normalized_symbol, normalized_symbol)
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
            warnings.append(f"{normalized_symbol} SEC 提交记录拉取失败: {error}")
            continue
        rows.extend(
            _parse_sec_recent_filings(
                cik=cik,
                symbol=normalized_symbol,
                company=company,
                payload=payload,
                limit=limit_per_symbol,
            )
        )

    output_path = _write_cache(
        output_dir=output_dir,
        filename="filings.json",
        provider="sec_edgar",
        rows=[asdict(row) for row in rows],
        warnings=warnings,
    )
    return PullResult("filings", "sec_edgar", len(rows), output_path, warnings)


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


def _fetch_provider_json(
    fetch_json: JsonFetcher,
    url: str,
    headers: dict[str, str] | None,
) -> object:
    try:
        return fetch_json(url, headers)
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


def _news_provider_retry_warning(provider_id: str, error: RuntimeError) -> str:
    provider_name = {
        "marketaux": "Marketaux",
        "finnhub": "Finnhub",
        "newsapi": "NewsAPI",
    }.get(provider_id, provider_id)
    reason = _news_provider_error_summary(str(error))
    return f"{provider_name} {reason}，正在尝试下一个已配置新闻数据源"


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


def _news_provider_candidates(
    config: AlphaDeskConfig,
    provider_id: str,
    symbols: list[str],
) -> list[str]:
    if provider_id != "auto":
        if provider_id not in {"marketaux", "finnhub", "newsapi"}:
            raise ValueError(f"不支持的新闻数据源: {provider_id}")
        if not symbols and provider_id == "finnhub":
            raise ValueError("Finnhub 当前仅支持个股新闻；市场级新闻请使用 Marketaux 或 NewsAPI。")
        return [provider_id]

    candidates: list[str] = []
    provider_order = (
        ("marketaux", "newsapi")
        if not symbols
        else ("marketaux", "finnhub", "newsapi")
    )
    for candidate in provider_order:
        provider = config.providers[candidate]
        if provider.value and provider.value.strip():
            candidates.append(candidate)
    return candidates


def _pull_news_for_provider(
    *,
    provider_id: str,
    symbols: list[str],
    query: str | None,
    start: str,
    end: str,
    config: AlphaDeskConfig,
    fetch_json: JsonFetcher,
) -> list[NewsEvent]:
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
                    )
                )
    return rows


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
    effective_query = query.strip() if query and query.strip() else ""
    if not effective_query:
        effective_query = " OR ".join(symbols) if symbols else MARKET_NEWS_QUERY
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
        return []
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []

    rows: list[NewsEvent] = []
    for item in articles:
        if not isinstance(item, dict):
            continue
        rows.append(
            NewsEvent(
                timestamp=str(item.get("publishedAt") or ""),
                headline=str(item.get("title") or ""),
                summary=str(item.get("description") or ""),
                symbols=symbols or [MARKET_NEWS_SYMBOL],
                source_url=str(item.get("url") or ""),
            )
        )
    return rows


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
            )
        )
    return rows


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
    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in [*existing, *new_rows]:
        key = _news_cache_row_key(row)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


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
    )


def _filing_summary_from_dict(row: dict[str, object]) -> FilingSummary:
    return FilingSummary(
        date=str(row["date"]),
        company=str(row["company"]),
        form=str(row["form"]),
        summary=str(row["summary"]),
        source_url=str(row["source_url"]),
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
