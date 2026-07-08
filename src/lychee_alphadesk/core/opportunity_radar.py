import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from lychee_alphadesk.core.live_data import build_cached_data_snapshot
from lychee_alphadesk.providers.demo import NewsEvent, PriceRow

RADAR_CACHE_FILENAME = "opportunity-radar.json"


@dataclass(frozen=True)
class OpportunitySignal:
    symbol: str
    market: str
    theme: str
    score: int
    news_count: int
    theme_hits: int
    volume_rank: int
    price_snapshot: str
    why_it_matters: str
    evidence: list[str]
    next_steps: list[str]


@dataclass(frozen=True)
class OpportunityRadarReport:
    created_at: str
    status: str
    signals: list[OpportunitySignal]
    warnings: list[str]
    disclaimer: str


@dataclass(frozen=True)
class _ThemeProfile:
    name: str
    query: str
    terms: tuple[str, ...]


THEME_PROFILES = (
    _ThemeProfile(
        name="AI 基础设施扩散",
        query="AI storage data center semiconductor cloud",
        terms=(
            "ai",
            "artificial intelligence",
            "data center",
            "datacenter",
            "semiconductor",
            "chip",
            "cloud",
            "server",
            "storage",
            "memory",
            "hard drive",
        ),
    ),
    _ThemeProfile(
        name="利率与流动性压力",
        query="rates liquidity treasury bond market",
        terms=(
            "rate",
            "rates",
            "liquidity",
            "treasury",
            "yield",
            "bond",
            "sofr",
            "central bank",
        ),
    ),
    _ThemeProfile(
        name="中国资产与政策变化",
        query="China policy consumption Hong Kong stocks",
        terms=(
            "china",
            "chinese",
            "hong kong",
            "policy",
            "stimulus",
            "consumption",
            "southbound",
        ),
    ),
    _ThemeProfile(
        name="电动车、机器人与自动驾驶",
        query="EV robotics autonomous driving battery",
        terms=(
            "ev",
            "electric vehicle",
            "robot",
            "robotics",
            "autonomous",
            "robotaxi",
            "battery",
        ),
    ),
)


def build_opportunity_radar(
    *,
    output_dir: Path,
    limit: int = 8,
) -> OpportunityRadarReport:
    snapshot = build_cached_data_snapshot(output_dir)
    if not snapshot.prices or not snapshot.news_events:
        return OpportunityRadarReport(
            created_at=_snapshot_created_at(),
            status="blocked",
            signals=[],
            warnings=[
                "缺少本地行情或新闻缓存。请先运行 `lychee data pull market` "
                "和 `lychee data pull news`，或运行 `lychee discover today`。"
            ],
            disclaimer="非投资建议。机会雷达只用于决定下一步研究什么。",
        )

    prices_by_symbol = {price.symbol.upper(): price for price in snapshot.prices}
    volume_ranks = _volume_ranks(snapshot.prices)
    news_by_symbol = _news_by_symbol(snapshot.news_events, prices_by_symbol)
    signals = [
        _signal_for_symbol(
            symbol=symbol,
            price=prices_by_symbol[symbol],
            matches=matches,
            volume_rank=volume_ranks[symbol],
            total_symbols=len(prices_by_symbol),
        )
        for symbol, matches in news_by_symbol.items()
        if symbol in prices_by_symbol and matches
    ]
    signals.sort(key=lambda signal: (signal.score, signal.news_count), reverse=True)
    return OpportunityRadarReport(
        created_at=_snapshot_created_at(),
        status="ready" if signals else "empty",
        signals=signals[:limit],
        warnings=[] if signals else ["本地缓存存在，但没有找到同时具备行情和新闻的标的。"],
        disclaimer="非投资建议。机会雷达只用于决定下一步研究什么。",
    )


def write_opportunity_radar_report(
    report: OpportunityRadarReport,
    output_dir: Path,
) -> Path:
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / RADAR_CACHE_FILENAME
    output_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def _signal_for_symbol(
    *,
    symbol: str,
    price: PriceRow,
    matches: list[tuple[NewsEvent, _ThemeProfile, int]],
    volume_rank: int,
    total_symbols: int,
) -> OpportunitySignal:
    theme, theme_matches = _dominant_theme(matches)
    theme_hits = sum(match_hits for _, _, match_hits in theme_matches)
    news_count = len(theme_matches)
    volume_score = max(total_symbols - volume_rank + 1, 1)
    score = news_count * 4 + theme_hits * 3 + volume_score
    evidence = [event.headline for event, _, _ in theme_matches[:3]]
    why_parts = [
        f"新闻热度 {news_count} 条",
        f"主题命中 {theme_hits} 次",
        f"成交量在本地样本中排第 {volume_rank}",
    ]
    return OpportunitySignal(
        symbol=symbol,
        market=_infer_market(symbol),
        theme=theme.name,
        score=score,
        news_count=news_count,
        theme_hits=theme_hits,
        volume_rank=volume_rank,
        price_snapshot=f"{price.close:.2f} {price.currency} | {price.date}",
        why_it_matters=(
            "，".join(why_parts)
            + "；这类组合适合先进入研究队列核验证据方向，而不是直接得出结论。"
        ),
        evidence=evidence,
        next_steps=[
            f'lychee data pull news --symbols {symbol} --query "{theme.query}" --force',
            f"lychee research run --symbol {symbol} --force",
        ],
    )


def _news_by_symbol(
    news_events: list[NewsEvent],
    prices_by_symbol: dict[str, PriceRow],
) -> dict[str, list[tuple[NewsEvent, _ThemeProfile, int]]]:
    grouped: dict[str, list[tuple[NewsEvent, _ThemeProfile, int]]] = {}
    for event in news_events:
        for raw_symbol in event.symbols:
            symbol = raw_symbol.upper()
            if symbol == "MARKET" or symbol not in prices_by_symbol:
                continue
            match = _event_theme_match(symbol, event)
            if match is None:
                continue
            grouped.setdefault(symbol, []).append(match)
    return grouped


def _event_theme_match(
    symbol: str,
    event: NewsEvent,
) -> tuple[NewsEvent, _ThemeProfile, int] | None:
    text = f"{event.headline} {event.summary}".lower()
    if not _has_financial_context(text) or not _has_market_context(symbol, text):
        return None
    scored = [
        (profile, sum(_count_term(text, term) for term in profile.terms))
        for profile in THEME_PROFILES
    ]
    theme, hits = max(scored, key=lambda item: item[1])
    if hits < 2:
        return None
    return event, theme, hits


def _dominant_theme(
    matches: list[tuple[NewsEvent, _ThemeProfile, int]],
) -> tuple[_ThemeProfile, list[tuple[NewsEvent, _ThemeProfile, int]]]:
    by_theme: dict[str, list[tuple[NewsEvent, _ThemeProfile, int]]] = {}
    for match in matches:
        _, theme, _ = match
        by_theme.setdefault(theme.name, []).append(match)
    for items in by_theme.values():
        items.sort(key=lambda item: item[2], reverse=True)
    return max(
        ((items[0][1], items) for items in by_theme.values()),
        key=lambda item: (sum(match[2] for match in item[1]), len(item[1])),
    )


def _has_financial_context(text: str) -> bool:
    terms = (
        "stock",
        "stocks",
        "share",
        "shares",
        "market",
        "markets",
        "etf",
        "fund",
        "index",
        "turnover",
        "liquidity",
        "trading",
        "investor",
        "investors",
        "earnings",
        "revenue",
        "guidance",
        "capex",
        "demand",
        "supply",
        "backlog",
    )
    return any(term in text for term in terms)


def _count_term(text: str, term: str) -> int:
    if " " in term or "-" in term:
        return text.count(term)
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def _has_market_context(symbol: str, text: str) -> bool:
    if symbol.endswith(".HK"):
        return any(
            term in text
            for term in (
                "hong kong",
                "hk",
                "hang seng",
                "china",
                "chinese",
                "southbound",
                "h-share",
            )
        )
    if symbol.endswith((".SH", ".SZ")):
        return any(
            term in text
            for term in (
                "china",
                "chinese",
                "a-share",
                "ashare",
                "shanghai",
                "shenzhen",
                "csi",
            )
        )
    return True


def _volume_ranks(prices: list[PriceRow]) -> dict[str, int]:
    ranked = sorted(prices, key=lambda price: price.volume, reverse=True)
    return {price.symbol.upper(): index for index, price in enumerate(ranked, start=1)}


def _infer_market(symbol: str) -> str:
    if symbol.endswith(".HK"):
        return "HK"
    if symbol.endswith((".SH", ".SZ")):
        return "CN"
    return "US"


def _snapshot_created_at() -> str:
    # Local radar is deterministic from current cache; use a stable marker in tests and
    # audit artifacts rather than pretending this is a provider timestamp.
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")
