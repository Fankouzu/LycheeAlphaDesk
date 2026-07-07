import shlex
from dataclasses import dataclass
from pathlib import Path

from lychee_alphadesk.core.research_db import ResearchMemoRecord, list_research_memos


@dataclass(frozen=True)
class ResearchDataRequest:
    request_id: str
    created_at: str
    display_name: str
    symbol: str | None
    market: str
    confidence: str
    request_text: str
    suggested_commands: list[str]
    memo_path: str
    verification_path: str


def list_research_data_requests(
    output_dir: Path,
    *,
    symbol: str | None = None,
    name: str | None = None,
    limit: int = 20,
    latest_per_task: bool = True,
) -> list[ResearchDataRequest]:
    records = list_research_memos(
        output_dir,
        symbol=symbol,
        name=name,
        limit=limit,
    )
    requests: list[ResearchDataRequest] = []
    seen_tasks: set[tuple[str, str, str]] = set()
    for record in records:
        task_key = _memo_task_key(record)
        if latest_per_task and task_key in seen_tasks:
            continue
        seen_tasks.add(task_key)
        request_texts = _memo_data_requests(record)
        for index, request_text in enumerate(request_texts, start=1):
            requests.append(
                ResearchDataRequest(
                    request_id=f"{record.memo_id}:data-request:{index}",
                    created_at=record.created_at,
                    display_name=record.display_name,
                    symbol=record.symbol,
                    market=record.market,
                    confidence=record.confidence,
                    request_text=request_text,
                    suggested_commands=_suggest_data_request_commands(
                        record,
                        request_text,
                    ),
                    memo_path=record.memo_path,
                    verification_path=record.verification_path,
                )
            )
    return requests


def research_data_request_needs_manual_source(item: ResearchDataRequest) -> bool:
    return (
        len(item.suggested_commands) == 1
        and item.suggested_commands[0].startswith("lychee research verify ")
    )


def _memo_task_key(record: ResearchMemoRecord) -> tuple[str, str, str]:
    return (
        (record.symbol or "").strip().upper(),
        record.display_name.strip().casefold(),
        record.market.strip().upper(),
    )


def _memo_data_requests(record: ResearchMemoRecord) -> list[str]:
    memo = record.payload.get("memo")
    if not isinstance(memo, dict):
        return []
    raw_requests = memo.get("next_data_requests")
    if not isinstance(raw_requests, list):
        return []
    return [item.strip() for item in raw_requests if isinstance(item, str) and item.strip()]


def _suggest_data_request_commands(
    record: ResearchMemoRecord,
    request_text: str,
) -> list[str]:
    commands: list[str] = []
    selector = _research_selector(record)
    lowered = request_text.casefold()
    if _looks_like_fund_metadata_request(lowered) and record.symbol:
        commands.extend(
            [
                (
                    f"lychee data guide fund --symbol {record.symbol} "
                    f"--name {_quote_cli_value(record.display_name)} "
                    f"--market {record.market.upper() or '<MARKET>'}"
                ),
                (
                    "lychee data set fund --from-file "
                    f".alphadesk/data/fund-metadata-guide-{record.symbol.upper()}.json"
                ),
            ]
        )
    if _looks_like_market_request(lowered) and record.symbol:
        commands.append(
            f"lychee data pull market --symbols {record.symbol} --provider auto --force"
        )
    if _looks_like_news_request(lowered):
        query = _quote_cli_value(record.display_name)
        if record.symbol:
            commands.append(
                "lychee data pull news "
                f"--symbols {record.symbol} --query {query} --force"
            )
        else:
            commands.append(f"lychee data pull news --query {query} --force")
    if _looks_like_filing_request(lowered) and record.symbol and record.market.upper() == "US":
        commands.append(f"lychee data pull filings --symbols {record.symbol}")
    commands.append(f"lychee research verify {selector}")
    return _dedupe_preserve_order(commands)


def _looks_like_fund_metadata_request(text: str) -> bool:
    keywords = (
        "etf",
        "基金",
        "费用",
        "费率",
        "跟踪指数",
        "持仓",
        "成分摘要",
        "成分或持仓",
        "tracking index",
        "expense",
        "holdings",
    )
    return any(keyword in text for keyword in keywords)


def _looks_like_market_request(text: str) -> bool:
    keywords = (
        "行情",
        "成交量",
        "价格",
        "相对强弱",
        "波动",
        "近 20 日",
        "volume",
        "price",
        "relative strength",
    )
    return any(keyword in text for keyword in keywords)


def _looks_like_news_request(text: str) -> bool:
    keywords = (
        "新闻",
        "报道",
        "原文",
        "headline",
        "article",
    )
    return any(keyword in text for keyword in keywords)


def _looks_like_filing_request(text: str) -> bool:
    keywords = (
        "sec",
        "公告",
        "财报",
        "10-q",
        "10-k",
        "8-k",
        "filing",
        "利润",
        "毛利率",
    )
    return any(keyword in text for keyword in keywords)


def _research_selector(record: ResearchMemoRecord) -> str:
    if record.symbol:
        return f"--symbol {record.symbol}"
    return f"--name {_quote_cli_value(record.display_name)}"


def _quote_cli_value(value: str) -> str:
    return shlex.quote(value)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique_items.append(item)
    return unique_items
