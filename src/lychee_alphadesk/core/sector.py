from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import (
    evaluate_research_metrics_cache,
    record_research_metrics_cache,
)
from lychee_alphadesk.core.live_data import (
    PullResult,
    ResearchMetric,
    _merge_research_metric_cache_rows,
    _read_cache,
    _write_cache,
    pull_market_history,
)

SECTOR_PROXY_UNIVERSE: dict[str, tuple[tuple[str, str, str], ...]] = {
    "US": (
        ("XLK", "美国科技板块", "Technology Select Sector SPDR Fund"),
        ("XLF", "美国金融板块", "Financial Select Sector SPDR Fund"),
        ("XLE", "美国能源板块", "Energy Select Sector SPDR Fund"),
        ("XLV", "美国医疗板块", "Health Care Select Sector SPDR Fund"),
        ("XLI", "美国工业板块", "Industrial Select Sector SPDR Fund"),
        ("XLC", "美国通信板块", "Communication Services Select Sector SPDR Fund"),
    ),
    "HK": (
        ("3033.HK", "港股科技代理", "Hang Seng TECH Index ETF proxy"),
        ("3067.HK", "港股科技 100 代理", "Hong Kong technology ETF proxy"),
        ("2800.HK", "港股宽基基准", "Tracker Fund of Hong Kong proxy"),
    ),
    "CN": (
        ("512480.SH", "A 股半导体代理", "Semiconductor ETF proxy"),
        ("515050.SH", "A 股通信设备代理", "5G communications ETF proxy"),
        ("159819.SZ", "A 股人工智能代理", "Artificial intelligence ETF proxy"),
    ),
}

HistoryPuller = Callable[..., PullResult]


def pull_sector_performance(
    *,
    markets: list[str],
    output_dir: Path,
    days: int = 60,
    force: bool = False,
    now: datetime | None = None,
    pull_history: HistoryPuller = pull_market_history,
) -> PullResult:
    normalized_markets = sorted({market.strip().upper() for market in markets if market.strip()})
    invalid = sorted(set(normalized_markets).difference(SECTOR_PROXY_UNIVERSE))
    if invalid:
        raise ValueError(f"行业代理只支持 US、HK、CN，无法识别: {', '.join(invalid)}")
    if not normalized_markets:
        raise ValueError("请至少选择一个市场。")
    if days < 30 or days > 3650:
        raise ValueError("行业表现历史窗口必须在 30 到 3650 天之间。")

    proxies = [
        row
        for market in normalized_markets
        for row in SECTOR_PROXY_UNIVERSE[market]
    ]
    symbols = [symbol for symbol, _, _ in proxies]
    cache_symbols = [f"SECTOR:{symbol}" for symbol in symbols]
    freshness = evaluate_research_metrics_cache(
        output_dir=output_dir,
        provider="sector_proxy_yahoo",
        symbols=cache_symbols,
        now=now,
        force=force,
    )
    if not freshness.should_refresh and freshness.entry is not None:
        rows = [
            row
            for row in _read_cache(output_dir, "research-metrics.json").rows
            if str(row.get("domain") or "").strip().lower() == "sector_performance"
            and str(row.get("provider") or "").strip() == "sector_proxy_yahoo"
            and str(row.get("symbol") or "").strip().upper() in set(symbols)
        ]
        return PullResult(
            "sector_performance",
            freshness.entry.provider,
            len(rows),
            freshness.entry.artifact_path,
            [freshness.reason],
            refreshed=False,
        )

    history_result = pull_history(
        symbols=symbols,
        output_dir=output_dir,
        days=days,
        force=force,
        now=now,
    )
    history_rows = _read_history_rows(output_dir)
    metrics: list[ResearchMetric] = []
    warnings = list(history_result.warnings)
    for symbol, display_name, proxy_name in proxies:
        row = _build_sector_metric(
            symbol=symbol,
            display_name=display_name,
            proxy_name=proxy_name,
            history_rows=history_rows,
            source_url=f"https://finance.yahoo.com/quote/{symbol.replace('.', '-')}",
        )
        if row is None:
            warnings.append(f"{symbol} 没有足够历史行情，无法计算行业代理表现。")
            continue
        metrics.append(row)

    output_path = _write_cache(
        output_dir=output_dir,
        filename="research-metrics.json",
        provider="sector_proxy_yahoo",
        rows=_merge_research_metric_cache_rows(
            output_dir,
            [asdict(row) for row in metrics],
        ),
        warnings=warnings,
        now=now,
    )
    record_research_metrics_cache(
        output_dir=output_dir,
        provider="sector_proxy_yahoo",
        symbols=cache_symbols,
        artifact_path=output_path,
        row_count=len(metrics),
        now=now,
        forced=force,
    )
    return PullResult(
        "sector_performance",
        "sector_proxy_yahoo",
        len(metrics),
        output_path,
        warnings,
    )


def _read_history_rows(output_dir: Path) -> list[dict[str, object]]:
    path = output_dir / "data" / "market-history.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _build_sector_metric(
    *,
    symbol: str,
    display_name: str,
    proxy_name: str,
    history_rows: list[dict[str, object]],
    source_url: str,
) -> ResearchMetric | None:
    rows = [
        row
        for row in history_rows
        if str(row.get("symbol") or "").strip().upper() == symbol
        and _number(row.get("close")) is not None
        and isinstance(row.get("date"), str)
    ]
    rows.sort(key=lambda row: str(row.get("date")), reverse=True)
    if len(rows) < 20:
        return None
    latest = _number(rows[0].get("close"))
    comparison = _number(rows[19].get("close"))
    if latest is None or comparison in {None, 0}:
        return None
    change = (latest - comparison) / comparison * 100
    return ResearchMetric(
        symbol=symbol,
        domain="sector_performance",
        name=f"{display_name} 20交易日变化",
        value=f"{change:+.2f}%",
        as_of=str(rows[0]["date"]),
        source_url=source_url,
        note=(
            f"使用 {proxy_name} 作为行业代理；不等于完整行业指数、成分股广度或实时行业行情。"
        ),
        provider="sector_proxy_yahoo",
    )


def _number(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
