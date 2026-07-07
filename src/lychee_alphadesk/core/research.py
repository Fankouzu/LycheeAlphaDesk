import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.evidence import EvidenceItem, build_news_evidence_pack
from lychee_alphadesk.core.live_data import (
    PullResult,
    build_cached_data_snapshot,
    pull_market_prices,
    pull_sec_filings,
)
from lychee_alphadesk.core.research_db import (
    ResearchQueueItem,
    init_research_db,
    list_research_queue,
    research_db_path,
    write_research_packet,
)
from lychee_alphadesk.core.symbol_mapping import (
    SymbolMappingProposal,
    suggest_symbol_mappings,
)
from lychee_alphadesk.providers.demo import FilingSummary, NewsEvent, PriceRow

PullMarket = Callable[..., PullResult]
PullFilings = Callable[..., PullResult]


@dataclass(frozen=True)
class ResearchPacket:
    packet_id: str
    candidate_id: int
    created_at: str
    display_name: str
    symbol: str | None
    market: str
    packet: dict[str, object]


@dataclass(frozen=True)
class ResearchDeepenResult:
    created_at: str
    packets: list[ResearchPacket]
    artifact_path: Path | None
    db_path: Path

    @property
    def count(self) -> int:
        return len(self.packets)


@dataclass(frozen=True)
class ResearchGapFillAction:
    action_type: str
    status: str
    symbols: list[str]
    count: int
    output_path: Path | None
    warnings: list[str]
    message: str


@dataclass(frozen=True)
class ResearchGapFillResult:
    candidates_checked: int
    market_symbols: list[str]
    filing_symbols: list[str]
    symbol_mapping_candidates: list[str]
    actions: list[ResearchGapFillAction]

    @property
    def warnings(self) -> list[str]:
        warnings: list[str] = []
        for action in self.actions:
            warnings.extend(action.warnings)
        return warnings


def deepen_research_queue(
    *,
    output_dir: Path,
    status: str | None = "new",
    limit: int = 5,
    now: datetime | None = None,
) -> ResearchDeepenResult:
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    db_path = init_research_db(output_dir)
    queue = list_research_queue(
        output_dir,
        status=status,
        limit=max(limit * 4, limit, 1),
    )
    if not queue:
        return ResearchDeepenResult(created_at, [], None, db_path)

    snapshot = build_cached_data_snapshot(output_dir)
    evidence_pack = build_news_evidence_pack(output_dir, limit=100)
    evidence_by_id = {item.id: item for item in evidence_pack}

    packets = [
        _build_research_packet(
            item=item,
            created_at=created_at,
            evidence_by_id=evidence_by_id,
            prices=snapshot.prices,
            news_events=snapshot.news_events,
            filings=snapshot.filings,
        )
        for item in queue
    ]
    packets = sorted(packets, key=_research_packet_sort_key)[:limit]
    artifact_path = _write_research_packets_artifact(
        output_dir=output_dir,
        created_at=created_at,
        packets=packets,
    )
    for packet in packets:
        write_research_packet(
            output_dir=output_dir,
            candidate_id=packet.candidate_id,
            packet_id=packet.packet_id,
            created_at=packet.created_at,
            display_name=packet.display_name,
            symbol=packet.symbol,
            market=packet.market,
            packet=packet.packet,
            artifact_path=artifact_path,
        )
    return ResearchDeepenResult(created_at, packets, artifact_path, research_db_path(output_dir))


def _research_packet_sort_key(packet: ResearchPacket) -> tuple[int, int, int, int, int]:
    payload = packet.packet
    data_gaps = _text_list(payload.get("data_gaps"))
    evidence_ids = _text_list(payload.get("evidence_ids"))
    local_data = _dict_value(payload.get("local_data"))
    symbol_mapping = _dict_list(local_data.get("symbol_mapping"))
    has_symbol = bool(packet.symbol)
    if has_symbol and len(evidence_ids) >= 2:
        actionability_rank = 0
    elif has_symbol:
        actionability_rank = 1
    elif symbol_mapping:
        actionability_rank = 2
    else:
        actionability_rank = 3
    return (
        1 if data_gaps else 0,
        actionability_rank,
        -len(evidence_ids),
        _packet_confidence_rank(payload),
        -packet.candidate_id,
    )


def _packet_confidence_rank(payload: dict[str, object]) -> int:
    candidate = _dict_value(payload.get("candidate"))
    confidence = _string_value(candidate.get("confidence"))
    return {"high": 0, "medium": 1, "low": 2}.get(confidence.strip().lower(), 3)


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def fill_research_data_gaps(
    *,
    output_dir: Path,
    status: str | None = "new",
    limit: int = 5,
    market_provider: str = "auto",
    force: bool = False,
    pull_market: PullMarket = pull_market_prices,
    pull_filings: PullFilings = pull_sec_filings,
) -> ResearchGapFillResult:
    queue = list_research_queue(output_dir, status=status, limit=limit)
    if not queue:
        return ResearchGapFillResult(0, [], [], [], [])

    snapshot = build_cached_data_snapshot(output_dir)
    market_symbols = _symbols_missing_prices(queue, snapshot.prices, force=force)
    filing_symbols = _symbols_missing_filings(queue, snapshot.filings, force=force)
    symbol_mapping_items = [item for item in queue if not _normalized_symbol(item)]
    symbol_mapping_candidates = [item.display_name for item in symbol_mapping_items]

    actions: list[ResearchGapFillAction] = []
    actions.append(
        _pull_market_gap_action(
            symbols=market_symbols,
            output_dir=output_dir,
            provider=market_provider,
            force=force,
            pull_market=pull_market,
        )
    )
    actions.append(
        _pull_filings_gap_action(
            symbols=filing_symbols,
            output_dir=output_dir,
            pull_filings=pull_filings,
        )
    )
    if symbol_mapping_items:
        actions.append(_symbol_mapping_gap_action(symbol_mapping_items))

    return ResearchGapFillResult(
        candidates_checked=len(queue),
        market_symbols=market_symbols,
        filing_symbols=filing_symbols,
        symbol_mapping_candidates=symbol_mapping_candidates,
        actions=actions,
    )


def _build_research_packet(
    *,
    item: ResearchQueueItem,
    created_at: str,
    evidence_by_id: dict[str, EvidenceItem],
    prices: list[PriceRow],
    news_events: list[NewsEvent],
    filings: list[FilingSummary],
) -> ResearchPacket:
    symbol = item.symbol.upper() if item.symbol else None
    evidence_items = [
        _evidence_to_dict(evidence_by_id[evidence_id])
        for evidence_id in item.evidence
        if evidence_id in evidence_by_id
    ]
    missing_evidence = [
        evidence_id for evidence_id in item.evidence if evidence_id not in evidence_by_id
    ]
    price = _latest_price(symbol, prices) if symbol else None
    symbol_mapping = _symbol_mapping_rows(item, prices) if not symbol else []
    related_news = _related_news(
        symbol,
        item.display_name,
        item.market,
        news_events,
        topic_terms=_research_topic_terms(item),
    )
    related_filings = _related_filings(symbol, item.display_name, filings)
    data_gaps = _data_gaps(
        item=item,
        symbol=symbol,
        evidence_items=evidence_items,
        missing_evidence=missing_evidence,
        price=price,
        symbol_mapping=symbol_mapping,
        related_news=related_news,
        related_filings=related_filings,
    )
    packet: dict[str, object] = {
        "candidate": {
            "candidate_id": item.candidate_id,
            "display_name": item.display_name,
            "symbol": item.symbol,
            "market": item.market,
            "asset_type": item.asset_type,
            "related_theme": item.related_theme,
            "why_watch": item.why_watch,
            "confidence": item.confidence,
            "status": item.status,
        },
        "evidence_ids": item.evidence,
        "evidence": evidence_items,
        "missing_evidence_ids": missing_evidence,
        "local_data": {
            "price": price,
            "symbol_mapping": symbol_mapping,
            "related_news": related_news,
            "filings": related_filings,
        },
        "risk_flags": item.risk_flags,
        "data_gaps": data_gaps,
        "next_actions": _next_actions(item, symbol, symbol_mapping, data_gaps),
        "disclaimer": "研究深挖包只用于决定下一步研究什么，不构成买卖建议。",
    }
    return ResearchPacket(
        packet_id=f"research:{created_at}:{item.candidate_id}",
        candidate_id=item.candidate_id,
        created_at=created_at,
        display_name=item.display_name,
        symbol=item.symbol,
        market=item.market,
        packet=packet,
    )


def _symbols_missing_prices(
    queue: list[ResearchQueueItem],
    prices: list[PriceRow],
    *,
    force: bool,
) -> list[str]:
    cached_symbols = {price.symbol.upper() for price in prices}
    symbols: list[str] = []
    for item in queue:
        symbol = _normalized_symbol(item)
        if symbol and (force or symbol not in cached_symbols):
            symbols.append(symbol)
        if not symbol:
            for proposal in suggest_symbol_mappings(item):
                proposal_symbol = proposal.symbol.upper()
                if force or proposal_symbol not in cached_symbols:
                    symbols.append(proposal_symbol)
    return _unique_preserving_order(symbols)


def _symbols_missing_filings(
    queue: list[ResearchQueueItem],
    filings: list[FilingSummary],
    *,
    force: bool,
) -> list[str]:
    symbols: list[str] = []
    for item in queue:
        symbol = _normalized_symbol(item)
        if (
            symbol
            and item.market == "US"
            and item.asset_type.lower() == "stock"
            and (force or not _related_filings(symbol, item.display_name, filings))
        ):
            symbols.append(symbol)
    return _unique_preserving_order(symbols)


def _pull_market_gap_action(
    *,
    symbols: list[str],
    output_dir: Path,
    provider: str,
    force: bool,
    pull_market: PullMarket,
) -> ResearchGapFillAction:
    if not symbols:
        return ResearchGapFillAction(
            action_type="market_prices",
            status="skipped",
            symbols=[],
            count=0,
            output_path=None,
            warnings=[],
            message="行情缓存没有需要自动补齐的 symbol。",
        )
    try:
        result = pull_market(
            symbols=symbols,
            output_dir=output_dir,
            provider_id=provider,
            force=force,
        )
    except (RuntimeError, ValueError) as error:
        return ResearchGapFillAction(
            action_type="market_prices",
            status="failed",
            symbols=symbols,
            count=0,
            output_path=None,
            warnings=[str(error)],
            message="行情补齐失败。",
        )
    return ResearchGapFillAction(
        action_type="market_prices",
        status=_gap_pull_status(result, requested_count=len(symbols)),
        symbols=symbols,
        count=result.count,
        output_path=result.output_path,
        warnings=result.warnings,
        message=_gap_pull_message(
            result,
            requested_count=len(symbols),
            success_message="行情缓存已补齐。",
            partial_message="行情缓存已部分补齐。",
            failed_message="行情补齐未完成。",
        ),
    )


def _pull_filings_gap_action(
    *,
    symbols: list[str],
    output_dir: Path,
    pull_filings: PullFilings,
) -> ResearchGapFillAction:
    if not symbols:
        return ResearchGapFillAction(
            action_type="sec_filings",
            status="skipped",
            symbols=[],
            count=0,
            output_path=None,
            warnings=[],
            message="SEC 公告缓存没有需要自动补齐的美股股票。",
        )
    try:
        result = pull_filings(symbols=symbols, output_dir=output_dir)
    except (RuntimeError, ValueError) as error:
        return ResearchGapFillAction(
            action_type="sec_filings",
            status="failed",
            symbols=symbols,
            count=0,
            output_path=None,
            warnings=[str(error)],
            message="SEC 公告补齐失败。",
        )
    return ResearchGapFillAction(
        action_type="sec_filings",
        status=_gap_pull_status(result, requested_count=len(symbols)),
        symbols=symbols,
        count=result.count,
        output_path=result.output_path,
        warnings=result.warnings,
        message=_gap_pull_message(
            result,
            requested_count=len(symbols),
            success_message="SEC 公告缓存已补齐。",
            partial_message="SEC 公告缓存已部分补齐。",
            failed_message="SEC 公告补齐未完成。",
        ),
    )


def _symbol_mapping_gap_action(
    items: list[ResearchQueueItem],
) -> ResearchGapFillAction:
    proposals = [
        proposal
        for item in items
        for proposal in suggest_symbol_mappings(item)
    ]
    symbols = _unique_preserving_order([proposal.symbol for proposal in proposals])
    if not proposals:
        return ResearchGapFillAction(
            action_type="symbol_mapping",
            status="needs_input",
            symbols=[],
            count=len(items),
            output_path=None,
            warnings=[
                "以下候选缺少可直接拉取的证券代码: "
                + ", ".join(item.display_name for item in items)
            ],
            message="需要先映射到可交易标的或指数/ETF。",
        )
    return ResearchGapFillAction(
        action_type="symbol_mapping",
        status="mapped",
        symbols=symbols,
        count=len(symbols),
        output_path=None,
        warnings=[
            "代理映射仅用于研究下钻，需人工核对成分、流动性和是否可交易。"
        ],
        message="已生成可审计代理标的；代理行情由行情动作补齐。",
    )


def _gap_pull_status(result: PullResult, *, requested_count: int) -> str:
    if not result.refreshed:
        return "cached"
    if result.count == 0 and result.warnings:
        return "failed"
    if result.count < requested_count:
        return "partial"
    return "pulled"


def _gap_pull_message(
    result: PullResult,
    *,
    requested_count: int,
    success_message: str,
    partial_message: str,
    failed_message: str,
) -> str:
    status = _gap_pull_status(result, requested_count=requested_count)
    if status == "failed":
        return failed_message
    if status == "partial":
        return partial_message
    return success_message


def _normalized_symbol(item: ResearchQueueItem) -> str | None:
    return item.symbol.upper() if item.symbol else None


def _unique_preserving_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _write_research_packets_artifact(
    *,
    output_dir: Path,
    created_at: str,
    packets: list[ResearchPacket],
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = research_dir / f"research-packets-{_safe_timestamp(created_at)}.json"
    output_path.write_text(
        json.dumps(
            {
                "created_at": created_at,
                "packet_count": len(packets),
                "packets": [_packet_to_dict(packet) for packet in packets],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _packet_to_dict(packet: ResearchPacket) -> dict[str, object]:
    return {
        "packet_id": packet.packet_id,
        "candidate_id": packet.candidate_id,
        "created_at": packet.created_at,
        "display_name": packet.display_name,
        "symbol": packet.symbol,
        "market": packet.market,
        "packet": packet.packet,
    }


def _evidence_to_dict(item: EvidenceItem) -> dict[str, object]:
    return asdict(item)


def _latest_price(symbol: str | None, prices: list[PriceRow]) -> dict[str, object] | None:
    if not symbol:
        return None
    for price in prices:
        if price.symbol.upper() == symbol:
            return asdict(price)
    return None


def _symbol_mapping_rows(
    item: ResearchQueueItem,
    prices: list[PriceRow],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for proposal in suggest_symbol_mappings(item):
        row = _symbol_mapping_to_dict(proposal)
        row["latest_price"] = _latest_price(proposal.symbol, prices)
        rows.append(row)
    return rows


def _symbol_mapping_to_dict(proposal: SymbolMappingProposal) -> dict[str, object]:
    return asdict(proposal)


def _related_news(
    symbol: str | None,
    display_name: str,
    market: str,
    news_events: list[NewsEvent],
    *,
    topic_terms: list[str] | None = None,
) -> list[dict[str, object]]:
    terms = _match_terms(symbol, display_name)
    normalized_topic_terms = [term.lower() for term in topic_terms or [] if term.strip()]
    market_terms = _market_context_terms(market)
    rows: list[dict[str, object]] = []
    for event in news_events:
        text = f"{event.headline} {event.summary}".lower()
        event_symbols = {event_symbol.upper() for event_symbol in event.symbols}
        matches_symbol = bool(symbol and symbol in event_symbols)
        topic_score = _news_topic_score(text, normalized_topic_terms)
        matches_topic = topic_score > 0 and _matches_market_context(text, market_terms)
        if matches_symbol or any(term in text for term in terms) or matches_topic:
            row = asdict(event)
            row["_topic_score"] = topic_score
            rows.append(row)
    selected = sorted(rows, key=_news_relevance_sort_key, reverse=True)[:5]
    for row in selected:
        row.pop("_topic_score", None)
    return selected


def _news_relevance_sort_key(row: dict[str, object]) -> tuple[int, str]:
    score = row.get("_topic_score")
    return (int(score) if isinstance(score, int) else 0, _news_timestamp_sort_key(row))


def _news_timestamp_sort_key(row: dict[str, object]) -> str:
    return _string_value(row.get("timestamp"))


def _news_topic_score(text: str, topic_terms: list[str]) -> int:
    return sum(1 for term in topic_terms if _topic_term_matches(text, term))


def _topic_term_matches(text: str, term: str) -> bool:
    cleaned = term.strip().lower()
    if not cleaned:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 .+-]*", cleaned):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(cleaned)}(?![a-z0-9])", text))
    return cleaned in text


def _market_context_terms(market: str) -> list[str]:
    normalized = market.strip().upper()
    if normalized == "HK":
        return ["hong kong", "hang seng", "港股", "香港", ".hk"]
    if normalized == "CN":
        return [
            "china",
            "chinese",
            "a-share",
            "a shares",
            "a股",
            "中国",
            "沪深",
            "上证",
            "深证",
            ".sh",
            ".sz",
        ]
    return []


def _matches_market_context(text: str, market_terms: list[str]) -> bool:
    if not market_terms:
        return True
    return any(term in text for term in market_terms)


def _research_topic_terms(item: ResearchQueueItem) -> list[str]:
    terms: list[str] = []
    for value in [
        item.related_theme,
        item.why_watch,
        item.display_name,
        item.symbol or "",
    ]:
        terms.extend(_topic_terms_from_text(value))
    return _unique_preserving_order([term.lower() for term in terms if term.strip()])


def _topic_terms_from_text(value: str) -> list[str]:
    text = value.lower()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9.+-]*", text):
        if len(token) >= 3 or token == "ai":
            terms.append(token)
    for keyword, aliases in _TOPIC_ALIASES.items():
        if keyword.lower() in text:
            terms.extend(aliases)
    return terms


_TOPIC_ALIASES = {
    "AI": ["ai", "artificial intelligence"],
    "人工智能": ["ai", "artificial intelligence"],
    "存储": ["storage", "hard drive"],
    "硬盘": ["storage", "hard drive"],
    "需求": ["demand"],
    "数据中心": ["data center", "cloud"],
    "科技": ["technology", "tech"],
    "纳斯达克": ["nasdaq", "qqq"],
    "美股": ["us stocks"],
    "港股": ["hong kong stocks"],
    "恒生": ["hang seng"],
    "半导体": ["semiconductor", "chip"],
    "利率": ["interest rates", "yields"],
    "大盘": ["broad market"],
}


def _related_filings(
    symbol: str | None,
    display_name: str,
    filings: list[FilingSummary],
) -> list[dict[str, object]]:
    terms = _match_terms(symbol, display_name)
    rows: list[dict[str, object]] = []
    for filing in filings:
        text = f"{filing.company} {filing.summary}".lower()
        if any(term in text for term in terms):
            rows.append(asdict(filing))
        if len(rows) >= 5:
            break
    return rows


def _match_terms(symbol: str | None, display_name: str) -> list[str]:
    terms = [display_name.lower()]
    if symbol:
        terms.append(symbol.lower())
    return [term for term in terms if term]


def _data_gaps(
    *,
    item: ResearchQueueItem,
    symbol: str | None,
    evidence_items: list[dict[str, object]],
    missing_evidence: list[str],
    price: dict[str, object] | None,
    symbol_mapping: list[dict[str, object]],
    related_news: list[dict[str, object]],
    related_filings: list[dict[str, object]],
) -> list[str]:
    gaps: list[str] = []
    if not symbol:
        if symbol_mapping:
            missing_proxy_prices = [
                str(row["symbol"])
                for row in symbol_mapping
                if row.get("latest_price") is None
            ]
            if missing_proxy_prices:
                gaps.append(
                    "代理标的行情尚未补齐: "
                    + ", ".join(missing_proxy_prices)
                    + "。"
                )
        else:
            gaps.append("缺少可直接拉取的证券代码，需先映射到可交易标的或指数/ETF。")
    if missing_evidence:
        gaps.append("部分 discovery 证据 ID 未在当前本地新闻缓存中找到。")
    if not evidence_items and not related_news:
        gaps.append("缺少可审计新闻证据，需先刷新市场级或个股新闻缓存。")
    if symbol and price is None:
        gaps.append(f"缺少 {symbol} 本地行情缓存。")
    if symbol and item.market == "US" and item.asset_type == "stock" and not related_filings:
        gaps.append(f"缺少 {symbol} SEC 公告缓存。")
    return gaps


def _next_actions(
    item: ResearchQueueItem,
    symbol: str | None,
    symbol_mapping: list[dict[str, object]],
    data_gaps: list[str],
) -> list[str]:
    if symbol:
        actions = list(dict.fromkeys(item.next_actions))
        actions.append(f"核对 {symbol} 行情、成交量和新闻证据是否支持同一主题。")
    elif symbol_mapping:
        actions = ["先审查代理标的映射，再把通过的代理标的加入下钻研究。"]
        actions.extend(item.next_actions)
        actions.append("核对代理标的行情、成交量和主题证据是否一致。")
    else:
        actions = ["先把观察对象映射到可交易代码，再进入行情和公告核验。"]
        actions.extend(item.next_actions)
    if data_gaps:
        actions.append("优先补齐 data_gaps 中列出的数据缺口。")
    actions.append("补齐证据后再进入 LLM 二阶段对比分析。")
    return list(dict.fromkeys(actions))


def _safe_timestamp(value: str) -> str:
    normalized = value.replace("+00:00", "Z")
    return (
        normalized.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace(".", "")
        .replace("T", "-")
    )
