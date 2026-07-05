import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    source_type: str
    provider: str
    timestamp: str
    headline: str
    summary: str
    symbols: list[str]
    source_url: str
    tags: list[str]


NOISE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bstrong buy\b",
        r"\bbuy picks?\b",
        r"\bstock picks?\b",
        r"\btarget price\b",
        r"\banalyst ratings?\b",
    )
]


def build_news_evidence_pack(output_dir: Path, limit: int = 20) -> list[EvidenceItem]:
    path = output_dir / "data" / "news-events.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    provider = payload.get("provider")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []

    items: list[EvidenceItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        headline = _string(row.get("headline"))
        summary = _string(row.get("summary"))
        source_url = _string(row.get("source_url"))
        if not headline or not source_url:
            continue
        if _is_noise(headline, summary):
            continue
        items.append(
            EvidenceItem(
                id=f"news_{len(items) + 1:03d}",
                source_type="news",
                provider=_string(provider) or "local-news-cache",
                timestamp=_string(row.get("timestamp")),
                headline=headline,
                summary=summary,
                symbols=_string_list(row.get("symbols")) or ["MARKET"],
                source_url=source_url,
                tags=_tags_for_news(headline, summary),
            )
        )
        if len(items) >= limit:
            break
    return items


def evidence_lookup(items: list[EvidenceItem]) -> dict[str, EvidenceItem]:
    return {item.id: item for item in items}


def _is_noise(headline: str, summary: str) -> bool:
    text = f"{headline} {summary}"
    return any(pattern.search(text) for pattern in NOISE_PATTERNS)


def _tags_for_news(headline: str, summary: str) -> list[str]:
    text = f"{headline} {summary}".lower()
    tags: list[str] = []
    if any(
        keyword in text
        for keyword in (
            "ai",
            "artificial intelligence",
            "data center",
            "chip",
            "semiconductor",
        )
    ):
        tags.append("ai_infrastructure")
    if any(keyword in text for keyword in ("hong kong", "hk", "china", "chinese")):
        tags.append("china_hk")
    if any(keyword in text for keyword in ("fed", "central bank", "rate", "yield", "rba")):
        tags.append("rates_macro")
    if any(keyword in text for keyword in ("tech", "nasdaq", "software", "cloud")):
        tags.append("technology")
    if "earnings" in text:
        tags.append("earnings")
    return tags or ["market_news"]


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
