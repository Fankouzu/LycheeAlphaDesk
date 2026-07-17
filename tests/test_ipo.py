import json
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.ipo import import_ipo_events, write_ipo_guide


def test_ipo_guide_and_manual_import_are_auditable(tmp_path: Path) -> None:
    guide = write_ipo_guide(
        output_dir=tmp_path,
        market="HK",
        name="Demo Technology IPO",
        symbol="09999.HK",
    )
    payload = json.loads(guide.output_path.read_text(encoding="utf-8"))
    payload["rows"] = [
        {
            "symbol": "09999.HK",
            "name": "Demo Technology IPO",
            "market": "HK",
            "exchange": "HKEX",
            "announcement_date": "2026-07-01",
            "subscription_start": "2026-07-02",
            "subscription_end": "2026-07-05",
            "listing_date": "2026-07-10",
            "status": "announced",
            "source_url": "https://example.com/ipo",
            "price_min": 10,
            "price_max": 12,
            "lot_size": 100,
            "account_eligibility_note": "需人工核对账户资格",
            "risk_note": "以招股章程为准",
        }
    ]
    guide.output_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    result = import_ipo_events(
        output_dir=tmp_path,
        guide_path=guide.output_path,
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert result.count == 1
    assert result.warnings == []
    cached = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cached["rows"][0]["symbol"] == "09999.HK"
    assert "不构成申购资格确认" in cached["disclaimer"]
    assert result.audit_path.exists()
