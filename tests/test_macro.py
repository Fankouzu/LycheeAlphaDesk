import json
from pathlib import Path

from lychee_alphadesk.core.config import default_config, save_config
from lychee_alphadesk.core.live_data import read_research_metric_cache
from lychee_alphadesk.core.macro import pull_macro_series


def test_pull_fred_macro_series_writes_latest_auditable_metric(tmp_path: Path) -> None:
    config = default_config()
    config.providers["fred"].value = "fred-secret"
    config_path = save_config(config, tmp_path / "config.yaml")
    calls: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None) -> object:
        calls.append(url)
        assert "api_key=fred-secret" in url
        return {
            "observations": [
                {"date": "2026-07-18", "value": "4.33"},
            ]
        }

    result = pull_macro_series(
        provider_id="fred",
        series=["DFF"],
        config_path=config_path,
        output_dir=tmp_path,
        fetch_json=fetch_json,
        force=True,
    )

    assert result.count == 1
    assert len(calls) == 1
    rows = read_research_metric_cache(tmp_path)
    assert rows[0].symbol == "MACRO:FRED:DFF"
    assert rows[0].domain == "macro"
    assert rows[0].value == "4.33"
    assert "api_key" not in rows[0].source_url


def test_pull_hkma_hibor_series_maps_numeric_fields(tmp_path: Path) -> None:
    def fetch_json(url: str, headers: dict[str, str] | None) -> object:
        assert "segment=hibor.fixing" in url
        return {
            "result": {
                "records": [
                    {
                        "end_of_day": "2026-07-18",
                        "ir_overnight": 2.1,
                        "ir_1m": 2.8,
                        "note": "ignored",
                    }
                ]
            }
        }

    result = pull_macro_series(
        provider_id="hkma",
        series=["hibor.fixing"],
        output_dir=tmp_path,
        fetch_json=fetch_json,
        force=True,
    )

    assert result.count == 2
    rows = read_research_metric_cache(tmp_path)
    assert {row.name for row in rows} == {
        "HKMA hibor.fixing ir_overnight",
        "HKMA hibor.fixing ir_1m",
    }
    cache = json.loads((tmp_path / "data" / "research-metrics.json").read_text())
    assert cache["provider"] == "hkma"


def test_macro_series_reuses_fresh_cache(tmp_path: Path) -> None:
    config = default_config()
    config.providers["fred"].value = "fred-secret"
    config_path = save_config(config, tmp_path / "config.yaml")
    calls: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None) -> object:
        calls.append(url)
        return {"observations": [{"date": "2026-07-18", "value": "4.33"}]}

    pull_macro_series(
        provider_id="fred",
        series=["DFF"],
        config_path=config_path,
        output_dir=tmp_path,
        fetch_json=fetch_json,
        force=True,
    )
    cached = pull_macro_series(
        provider_id="fred",
        series=["DFF"],
        config_path=config_path,
        output_dir=tmp_path,
        fetch_json=fetch_json,
    )

    assert cached.refreshed is False
    assert len(calls) == 1
