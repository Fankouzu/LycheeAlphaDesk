import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lychee_alphadesk.core.live_data import build_cached_data_snapshot
from lychee_alphadesk.providers.demo import NewsEvent, PriceRow

RADAR_CACHE_FILENAME = "opportunity-radar.json"


@dataclass(frozen=True)
class OpportunityDrilldownTarget:
    symbol: str
    market: str
    display_name: str
    category: str
    reason: str
    evidence_gap: str
    next_steps: list[str]


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
    drilldown_targets: list[OpportunityDrilldownTarget] = field(default_factory=list)


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


@dataclass(frozen=True)
class _DrilldownTemplate:
    symbol: str
    market: str
    display_name: str
    category: str
    reason: str
    query: str


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

DRILLDOWN_TEMPLATES: dict[str, tuple[_DrilldownTemplate, ...]] = {
    "AI 基础设施扩散": (
        _DrilldownTemplate(
            symbol="STX",
            market="US",
            display_name="Seagate",
            category="存储供应链",
            reason="用存储硬件验证 AI 数据中心需求是否扩散到更细分的基础设施环节。",
            query="AI data center storage hard drive demand",
        ),
        _DrilldownTemplate(
            symbol="NVDA",
            market="US",
            display_name="NVIDIA",
            category="算力芯片锚点",
            reason="用算力芯片龙头校验 AI 主题是个股集中，还是正在向供应链扩散。",
            query="AI chip data center semiconductor demand",
        ),
        _DrilldownTemplate(
            symbol="BABA",
            market="US",
            display_name="Alibaba",
            category="中国云服务代理",
            reason="用中国云服务公司观察 AI 云需求是否与美国算力主题同步。",
            query="AI cloud revenue Alibaba data center",
        ),
        _DrilldownTemplate(
            symbol="9988.HK",
            market="HK",
            display_name="阿里巴巴-W",
            category="港股中国云服务代理",
            reason="用港股同一公司入口检查跨市场价格和情绪是否一致。",
            query="AI cloud Hong Kong Alibaba stock",
        ),
        _DrilldownTemplate(
            symbol="512480.SH",
            market="CN",
            display_name="半导体 ETF",
            category="A 股半导体代理",
            reason="用 A 股半导体 ETF 观察 AI 硬件链条是否映射到本地市场。",
            query="AI semiconductor China ETF",
        ),
        _DrilldownTemplate(
            symbol="515050.SH",
            market="CN",
            display_name="5G 通信 ETF",
            category="A 股通信设备代理",
            reason="用通信设备 ETF 观察数据中心网络和通信链条是否被资金关注。",
            query="AI data center telecom equipment China ETF",
        ),
        _DrilldownTemplate(
            symbol="159819.SZ",
            market="CN",
            display_name="人工智能 ETF",
            category="A 股 AI 主题代理",
            reason="用主题 ETF 先验证 A 股 AI 情绪，再决定是否下钻成分股。",
            query="China artificial intelligence ETF data center",
        ),
    ),
    "中国资产与政策变化": (
        _DrilldownTemplate(
            symbol="0700.HK",
            market="HK",
            display_name="腾讯控股",
            category="港股平台公司",
            reason="用大型平台公司观察政策、消费和港股中国资产情绪是否互相印证。",
            query="China policy consumption Tencent Hong Kong stocks",
        ),
        _DrilldownTemplate(
            symbol="2800.HK",
            market="HK",
            display_name="盈富基金",
            category="港股宽基代理",
            reason="用港股宽基 ETF 区分单一板块变化和整体市场 beta。",
            query="Hong Kong stocks southbound Hang Seng ETF",
        ),
        _DrilldownTemplate(
            symbol="3033.HK",
            market="HK",
            display_name="南方恒生科技",
            category="港股科技代理",
            reason="用恒生科技代理观察中国科技资产是否强于港股宽基。",
            query="Hang Seng Tech ETF China policy liquidity",
        ),
        _DrilldownTemplate(
            symbol="3067.HK",
            market="HK",
            display_name="恒生科技 ETF",
            category="港股科技代理",
            reason="用另一个港股科技 ETF 交叉检查成交和流动性是否稳定。",
            query="Hong Kong technology ETF turnover",
        ),
        _DrilldownTemplate(
            symbol="510300.SH",
            market="CN",
            display_name="沪深 300 ETF",
            category="A 股宽基代理",
            reason="用 A 股宽基 ETF 判断政策线索是否映射到内地核心资产。",
            query="China policy CSI 300 ETF liquidity",
        ),
    ),
    "利率与流动性压力": (
        _DrilldownTemplate(
            symbol="QQQ",
            market="US",
            display_name="纳斯达克 100 ETF",
            category="美股科技 beta",
            reason="用科技宽基观察利率变化对成长股估值的压力或支撑。",
            query="rates liquidity QQQ technology stocks",
        ),
        _DrilldownTemplate(
            symbol="2800.HK",
            market="HK",
            display_name="盈富基金",
            category="港股宽基代理",
            reason="用港股宽基观察全球流动性变化是否传导到港股中国资产。",
            query="Hong Kong stocks liquidity rates ETF",
        ),
        _DrilldownTemplate(
            symbol="510300.SH",
            market="CN",
            display_name="沪深 300 ETF",
            category="A 股宽基代理",
            reason="用 A 股宽基观察流动性主题是否只发生在离岸市场。",
            query="China stocks liquidity CSI 300 ETF",
        ),
    ),
    "电动车、机器人与自动驾驶": (
        _DrilldownTemplate(
            symbol="TSLA",
            market="US",
            display_name="Tesla",
            category="电动车与自动驾驶锚点",
            reason="用特斯拉观察电动车需求、储能、robotaxi 和机器人叙事是否一致。",
            query="Tesla EV robotaxi robotics battery demand",
        ),
        _DrilldownTemplate(
            symbol="1211.HK",
            market="HK",
            display_name="比亚迪股份",
            category="港股电动车代理",
            reason="用中国电动车龙头交叉检查电动车需求是否只是单一公司叙事。",
            query="BYD electric vehicle Hong Kong stock demand",
        ),
        _DrilldownTemplate(
            symbol="002594.SZ",
            market="CN",
            display_name="比亚迪",
            category="A 股电动车代理",
            reason="用 A 股入口观察电动车主题在内地市场的映射。",
            query="BYD electric vehicle China A share battery",
        ),
    ),
}


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
            prices_by_symbol=prices_by_symbol,
            news_by_symbol=news_by_symbol,
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
    prices_by_symbol: dict[str, PriceRow],
    news_by_symbol: dict[str, list[tuple[NewsEvent, _ThemeProfile, int]]],
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
        drilldown_targets=_drilldown_targets_for_signal(
            signal_symbol=symbol,
            theme=theme,
            prices_by_symbol=prices_by_symbol,
            news_by_symbol=news_by_symbol,
        ),
    )


def _drilldown_targets_for_signal(
    *,
    signal_symbol: str,
    theme: _ThemeProfile,
    prices_by_symbol: dict[str, PriceRow],
    news_by_symbol: dict[str, list[tuple[NewsEvent, _ThemeProfile, int]]],
) -> list[OpportunityDrilldownTarget]:
    targets: list[OpportunityDrilldownTarget] = []
    for template in DRILLDOWN_TEMPLATES.get(theme.name, ()):
        symbol = template.symbol.upper()
        if symbol == signal_symbol or symbol not in prices_by_symbol:
            continue
        target_theme_news = [
            match
            for match in news_by_symbol.get(symbol, [])
            if match[1].name == theme.name
        ]
        evidence_gap = (
            "已有行情和主题新闻，可进入下钻核验。"
            if target_theme_news
            else "缺少该标的的主题新闻缓存，需补新闻验证。"
        )
        targets.append(
            OpportunityDrilldownTarget(
                symbol=symbol,
                market=template.market,
                display_name=template.display_name,
                category=template.category,
                reason=template.reason,
                evidence_gap=evidence_gap,
                next_steps=[
                    (
                        f'lychee data pull news --symbols {symbol} '
                        f'--query "{template.query}" --force'
                    ),
                    f"lychee research run --symbol {symbol} --force",
                ],
            )
        )
    return targets[:5]


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
