import json
from pathlib import Path

from lychee_alphadesk.core.data_engine import build_demo_data_snapshot, write_snapshot_json


def test_demo_data_snapshot_aggregates_all_provider_domains() -> None:
    snapshot = build_demo_data_snapshot(Path("examples/demo"))

    assert snapshot.mode == "demo"
    assert snapshot.provider_names == [
        "demo-market-data",
        "demo-news",
        "demo-filings",
        "demo-forecast",
    ]
    assert [price.symbol for price in snapshot.prices] == ["SPY", "QQQ", "2800.HK"]
    assert len(snapshot.news_events) == 2
    assert len(snapshot.filings) == 2
    assert sorted(snapshot.forecasts) == ["2800.HK", "QQQ", "SPY"]


def test_demo_data_snapshot_reports_quality_status() -> None:
    snapshot = build_demo_data_snapshot(Path("examples/demo"))

    checks = {check.name: check for check in snapshot.quality_checks}

    assert checks["market-data-present"].status == "pass"
    assert checks["news-events-present"].status == "pass"
    assert checks["filings-present"].status == "pass"
    assert checks["forecast-coverage"].status == "pass"


def test_snapshot_json_is_written_for_auditable_data_visibility(tmp_path: Path) -> None:
    snapshot = build_demo_data_snapshot(Path("examples/demo"))

    output_path = write_snapshot_json(snapshot, tmp_path)

    assert output_path == tmp_path / "data-snapshot-demo.json"
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["mode"] == "demo"
    assert data["counts"] == {
        "prices": 3,
        "news_events": 2,
        "filings": 2,
        "forecasts": 3,
    }
    assert data["quality_checks"][0]["status"] == "pass"
