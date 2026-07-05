import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.evidence import EvidenceItem, build_news_evidence_pack
from lychee_alphadesk.core.live_data import build_cached_data_snapshot
from lychee_alphadesk.core.research_db import (
    ResearchQueueItem,
    init_research_db,
    list_research_queue,
    research_db_path,
    write_research_packet,
)
from lychee_alphadesk.providers.demo import FilingSummary, NewsEvent, PriceRow


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


def deepen_research_queue(
    *,
    output_dir: Path,
    status: str | None = "new",
    limit: int = 5,
    now: datetime | None = None,
) -> ResearchDeepenResult:
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    db_path = init_research_db(output_dir)
    queue = list_research_queue(output_dir, status=status, limit=limit)
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
    related_news = _related_news(symbol, item.display_name, news_events)
    related_filings = _related_filings(symbol, item.display_name, filings)
    data_gaps = _data_gaps(
        item=item,
        symbol=symbol,
        evidence_items=evidence_items,
        missing_evidence=missing_evidence,
        price=price,
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
            "related_news": related_news,
            "filings": related_filings,
        },
        "risk_flags": item.risk_flags,
        "data_gaps": data_gaps,
        "next_actions": _next_actions(item, symbol, data_gaps),
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


def _related_news(
    symbol: str | None,
    display_name: str,
    news_events: list[NewsEvent],
) -> list[dict[str, object]]:
    terms = _match_terms(symbol, display_name)
    rows: list[dict[str, object]] = []
    for event in news_events:
        text = f"{event.headline} {event.summary}".lower()
        event_symbols = {event_symbol.upper() for event_symbol in event.symbols}
        matches_symbol = bool(symbol and symbol in event_symbols)
        if matches_symbol or any(term in text for term in terms):
            rows.append(asdict(event))
        if len(rows) >= 5:
            break
    return rows


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
    related_news: list[dict[str, object]],
    related_filings: list[dict[str, object]],
) -> list[str]:
    gaps: list[str] = []
    if not symbol:
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
    data_gaps: list[str],
) -> list[str]:
    if symbol:
        actions = list(dict.fromkeys(item.next_actions))
        actions.append(f"核对 {symbol} 行情、成交量和新闻证据是否支持同一主题。")
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
