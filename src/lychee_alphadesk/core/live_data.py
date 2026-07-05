import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import (
    evaluate_market_cache,
    evaluate_news_cache,
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

SEC_USER_AGENT = (
    "LycheeAlphaDesk/0.1 research-workbench "
    "(https://github.com/Fankouzu/LycheeAlphaDesk)"
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
    "GOOGL": (1652044, "Alphabet Inc."),
    "GOOG": (1652044, "Alphabet Inc."),
    "META": (1326801, "Meta Platforms, Inc."),
    "BRK.B": (1067983, "Berkshire Hathaway Inc."),
}


@dataclass(frozen=True)
class PullResult:
    domain: str
    provider: str
    count: int
    output_path: Path
    warnings: list[str]
    refreshed: bool = True


def pull_market_prices(
    *,
    symbols: list[str],
    config_path: Path | None = None,
    output_dir: Path,
    provider_id: str = "alpha_vantage",
    fetch_json: JsonFetcher | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> PullResult:
    if provider_id != "alpha_vantage":
        raise ValueError("当前版本仅支持通过 alpha_vantage 拉取行情")

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
        return PullResult(
            "market",
            freshness.entry.provider,
            len(cache.rows),
            freshness.entry.artifact_path,
            [freshness.reason],
            refreshed=False,
        )

    config = load_config(config_path)
    api_key = _configured_value(config.providers["alpha_vantage"].value, "Alpha Vantage")
    rows: list[PriceRow] = []
    warnings: list[str] = []
    fetcher = fetch_json or _fetch_json

    for symbol in symbols:
        url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "apikey": api_key,
                "outputsize": "compact",
            }
        )
        payload = _fetch_provider_json(fetcher, url, None)
        row = _parse_alpha_vantage_daily(symbol, payload)
        if row is None:
            warnings.append(f"Alpha Vantage 没有返回 {symbol} 的日线行情")
        else:
            rows.append(row)

    output_path = _write_cache(
        output_dir=output_dir,
        filename="market-prices.json",
        provider=provider_id,
        rows=[asdict(row) for row in rows],
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
    config_path: Path | None = None,
    output_dir: Path,
    provider_id: str = "auto",
    start_date: str | None = None,
    end_date: str | None = None,
    fetch_json: JsonFetcher | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> PullResult:
    config = load_config(config_path)
    start, end = _date_window(start_date, end_date)
    freshness = evaluate_news_cache(
        output_dir=output_dir,
        provider=provider_id,
        symbols=symbols,
        start_date=start,
        end_date=end,
        now=now,
        force=force,
    )
    if not freshness.should_refresh and freshness.entry is not None:
        cache = _read_cache(output_dir, "news-events.json")
        return PullResult(
            "news",
            freshness.entry.provider,
            len(cache.rows),
            freshness.entry.artifact_path,
            [freshness.reason],
            refreshed=False,
        )

    fetcher = fetch_json or _fetch_json
    warnings: list[str] = []
    rows: list[NewsEvent] = []
    selected_provider = ""
    last_error: RuntimeError | None = None

    for candidate in _news_provider_candidates(config, provider_id, symbols):
        try:
            rows = _pull_news_for_provider(
                provider_id=candidate,
                symbols=symbols,
                start=start,
                end=end,
                config=config,
                fetch_json=fetcher,
            )
        except RuntimeError as error:
            last_error = RuntimeError(_sanitize_error_message(str(error)))
            if provider_id == "auto":
                warnings.append(f"{candidate} 失败，正在尝试下一个已配置新闻数据源")
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

    output_path = _write_cache(
        output_dir=output_dir,
        filename="news-events.json",
        provider=selected_provider,
        rows=[asdict(row) for row in rows],
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


def build_cached_data_snapshot(output_dir: Path) -> DataSnapshot:
    market_cache = _read_cache(output_dir, "market-prices.json")
    news_cache = _read_cache(output_dir, "news-events.json")
    filing_cache = _read_cache(output_dir, "filings.json")

    prices = [_price_row_from_dict(row) for row in market_cache.rows]
    news_events = [_news_event_from_dict(row) for row in news_cache.rows]
    filings = [_filing_summary_from_dict(row) for row in filing_cache.rows]
    provider_names = [
        provider
        for provider in [
            market_cache.provider,
            news_cache.provider,
            filing_cache.provider,
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
        forecasts={},
        quality_checks=run_cached_data_health(output_dir),
    )


def run_cached_data_health(output_dir: Path) -> list[DataQualityCheck]:
    checks = [
        _cache_health_check(
            output_dir=output_dir,
            filename="market-prices.json",
            name="market-cache-present",
            noun="行情",
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
    return checks


def parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",")]
    return [symbol for symbol in symbols if symbol]


@dataclass(frozen=True)
class _CachePayload:
    provider: str
    rows: list[dict[str, object]]


def _fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers=headers or {})
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


def _configured_value(value: str | None, provider_name: str) -> str:
    if value and value.strip():
        return value.strip()
    raise ValueError(f"{provider_name} 尚未配置。请先运行 `lychee setup`。")


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


def _infer_symbol_currency(symbol: str) -> str:
    normalized = symbol.upper()
    if normalized.endswith(".HK"):
        return "HKD"
    if normalized.endswith((".SH", ".SZ", ".SS")):
        return "CNY"
    return "USD"


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
    start: str,
    end: str,
    config: AlphaDeskConfig,
    fetch_json: JsonFetcher,
) -> list[NewsEvent]:
    if provider_id == "finnhub":
        api_key = _configured_value(config.providers["finnhub"].value, "Finnhub")
        return _pull_finnhub_news(symbols, start, end, api_key, fetch_json)
    if provider_id == "marketaux":
        api_key = _configured_value(config.providers["marketaux"].value, "Marketaux")
        return _pull_marketaux_news(symbols, api_key, fetch_json)
    if provider_id == "newsapi":
        api_key = _configured_value(config.providers["newsapi"].value, "NewsAPI")
        return _pull_newsapi_events(symbols, start, end, api_key, fetch_json)
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
    api_key: str,
    fetch_json: JsonFetcher,
) -> list[NewsEvent]:
    params = {
        "api_token": api_key,
        "language": "en",
        "limit": "20",
    }
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
    start: str,
    end: str,
    api_key: str,
    fetch_json: JsonFetcher,
) -> list[NewsEvent]:
    query = " OR ".join(symbols) if symbols else MARKET_NEWS_QUERY
    url = "https://newsapi.org/v2/everything?" + urllib.parse.urlencode(
        {
            "q": query,
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
    for form, date, accession, document in list(
        zip(forms, dates, accessions, documents, strict=False)
    )[:limit]:
        accession_path = accession.replace("-", "")
        rows.append(
            FilingSummary(
                date=date,
                company=company,
                form=form,
                summary=f"{symbol} 在 {date} 提交了 {form}。",
                source_url=(
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{cik}/{accession_path}/{document}"
                ),
            )
        )
    return rows


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


def _read_cache(output_dir: Path, filename: str) -> _CachePayload:
    path = output_dir / "data" / filename
    if not path.exists():
        return _CachePayload(provider="", rows=[])
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return _CachePayload(provider="", rows=[])
    provider = payload.get("provider")
    rows = payload.get("rows")
    return _CachePayload(
        provider=provider if isinstance(provider, str) else "",
        rows=[row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else [],
    )


def _price_row_from_dict(row: dict[str, object]) -> PriceRow:
    return PriceRow(
        symbol=str(row["symbol"]),
        date=str(row["date"]),
        close=float(str(row["close"])),
        volume=int(str(row["volume"])),
        currency=str(row["currency"]),
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
