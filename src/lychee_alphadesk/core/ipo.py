import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class IPOEvent:
    symbol: str
    name: str
    market: str
    exchange: str
    announcement_date: str
    subscription_start: str
    subscription_end: str
    listing_date: str
    status: str
    source_url: str
    price_min: float | None
    price_max: float | None
    lot_size: int | None
    account_eligibility_note: str
    risk_note: str


@dataclass(frozen=True)
class IPOGuide:
    output_path: Path
    apply_command: str
    suggested_sources: list[str]


@dataclass(frozen=True)
class IPOImportResult:
    count: int
    output_path: Path
    audit_path: Path
    warnings: list[str]


def write_ipo_guide(
    *,
    output_dir: Path,
    market: str,
    name: str,
    symbol: str = "",
) -> IPOGuide:
    normalized_market = market.strip().upper()
    if normalized_market not in {"HK", "CN"}:
        raise ValueError("IPO 向导当前只支持 HK 或 CN 市场。")
    display_name = name.strip()
    if not display_name:
        raise ValueError("IPO 向导必须提供名称。")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", display_name).strip("-") or "ipo"
    output_path = output_dir / "data" / f"ipo-guide-{safe_name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "template": {
            "symbol": symbol.strip().upper(),
            "name": display_name,
            "market": normalized_market,
            "exchange": "HKEX" if normalized_market == "HK" else "SZSE/ SSE",
            "announcement_date": "YYYY-MM-DD",
            "subscription_start": "YYYY-MM-DD",
            "subscription_end": "YYYY-MM-DD",
            "listing_date": "YYYY-MM-DD",
            "status": "announced",
            "source_url": "https://",
            "price_min": None,
            "price_max": None,
            "lot_size": None,
            "account_eligibility_note": "",
            "risk_note": "",
        },
        "instructions": (
            "只填写交易所、发行人公告或监管披露中已经核验的 IPO/新股资料；"
            "不要填写预期收益或申购建议。"
        ),
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return IPOGuide(
        output_path=output_path,
        apply_command=f"lychee data set ipo --from-file {output_path}",
        suggested_sources=(
            ["香港交易所披露易新上市资料", "发行人招股章程或公告"]
            if normalized_market == "HK"
            else ["中国证监会/交易所发行披露", "发行人招股说明书或公告"]
        ),
    )


def import_ipo_events(
    *,
    output_dir: Path,
    guide_path: Path,
    now: datetime | None = None,
) -> IPOImportResult:
    try:
        payload = json.loads(guide_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"IPO 资料文件不是有效 JSON: {guide_path}") from error
    if not isinstance(payload, dict):
        raise ValueError("IPO 资料文件必须是 JSON 对象。")
    raw_rows = payload.get("rows")
    if raw_rows is None:
        template = payload.get("template")
        raw_rows = [template] if isinstance(template, dict) else []
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("IPO 资料文件必须包含 template 或 rows。")
    events = [_parse_ipo_event(row, index) for index, row in enumerate(raw_rows, start=1)]
    retrieved_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "ipo-events.json"
    output_path.write_text(
        json.dumps(
            {
                "provider": "manual",
                "retrieved_at": retrieved_at,
                "rows": [asdict(event) for event in events],
                "disclaimer": (
                    "IPO 事件仅为已核验资料索引，不构成申购资格确认、收益预测或投资建议。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    timestamp = retrieved_at.replace("-", "").replace(":", "").replace("+00:00", "Z")
    audit_path = research_dir / f"ipo-import-{timestamp}.json"
    audit_path.write_text(
        json.dumps(
            {
                "input_path": str(guide_path),
                "output_path": str(output_path),
                "count": len(events),
                "markets": sorted({event.market for event in events}),
                "source_urls": [event.source_url for event in events],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return IPOImportResult(len(events), output_path, audit_path, [])


def _parse_ipo_event(row: object, index: int) -> IPOEvent:
    if not isinstance(row, dict):
        raise ValueError(f"IPO 第 {index} 条记录不是对象。")
    text_fields = [
        "symbol",
        "name",
        "market",
        "exchange",
        "announcement_date",
        "subscription_start",
        "subscription_end",
        "listing_date",
        "status",
        "source_url",
    ]
    values = {field: str(row.get(field) or "").strip() for field in text_fields}
    missing = [field for field, value in values.items() if not value]
    if missing:
        raise ValueError(f"IPO 第 {index} 条记录缺少字段: {', '.join(missing)}")
    if values["market"].upper() not in {"HK", "CN"}:
        raise ValueError(f"IPO 第 {index} 条记录 market 只能是 HK 或 CN。")
    if not values["source_url"].startswith(("https://", "http://")):
        raise ValueError(f"IPO 第 {index} 条记录 source_url 必须是 http(s)。")
    for field in [
        "announcement_date",
        "subscription_start",
        "subscription_end",
        "listing_date",
    ]:
        try:
            datetime.fromisoformat(values[field])
        except ValueError as error:
            raise ValueError(f"IPO 第 {index} 条记录 {field} 必须是 ISO 日期。") from error
    price_min = _optional_float(row.get("price_min"), index, "price_min")
    price_max = _optional_float(row.get("price_max"), index, "price_max")
    lot_size = _optional_int(row.get("lot_size"), index, "lot_size")
    if price_min is not None and price_max is not None and price_min > price_max:
        raise ValueError(f"IPO 第 {index} 条记录价格区间无效。")
    if lot_size is not None and lot_size <= 0:
        raise ValueError(f"IPO 第 {index} 条记录 lot_size 必须大于 0。")
    return IPOEvent(
        symbol=values["symbol"].upper(),
        name=values["name"],
        market=values["market"].upper(),
        exchange=values["exchange"],
        announcement_date=values["announcement_date"],
        subscription_start=values["subscription_start"],
        subscription_end=values["subscription_end"],
        listing_date=values["listing_date"],
        status=values["status"].lower(),
        source_url=values["source_url"],
        price_min=price_min,
        price_max=price_max,
        lot_size=lot_size,
        account_eligibility_note=str(row.get("account_eligibility_note") or "").strip(),
        risk_note=str(row.get("risk_note") or "").strip(),
    )


def _optional_float(value: object, index: int, field: str) -> float | None:
    if value in (None, ""):
        return None
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"IPO 第 {index} 条记录 {field} 无法解析。")
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"IPO 第 {index} 条记录 {field} 无法解析。") from error


def _optional_int(value: object, index: int, field: str) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, (int, str)):
        raise ValueError(f"IPO 第 {index} 条记录 {field} 无法解析。")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"IPO 第 {index} 条记录 {field} 无法解析。") from error
