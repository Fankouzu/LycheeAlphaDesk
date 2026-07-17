import json
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import record_research_metrics_cache
from lychee_alphadesk.core.live_data import PullResult, read_research_metric_cache
from lychee_alphadesk.core.sector import pull_sector_performance


def test_pull_sector_performance_calculates_proxy_changes_and_preserves_boundary(
    tmp_path: Path,
) -> None:
    rows = []
    symbols = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC"]
    for symbol in symbols:
        for index in range(20):
            rows.append(
                {
                    "symbol": symbol,
                    "date": f"2026-07-{18 - index:02d}",
                    "close": (
                        100 + (20 - index if index < 19 else 0)
                        if symbol == "XLK"
                        else 100
                    ),
                    "volume": 1000,
                    "currency": "USD",
                }
            )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-history.json").write_text(
        json.dumps({"provider": "fixture", "rows": rows}),
        encoding="utf-8",
    )

    def fake_history(**kwargs: object) -> PullResult:
        return PullResult(
            "market_history",
            "fixture",
            len(rows),
            data_dir / "market-history.json",
            [],
        )

    result = pull_sector_performance(
        markets=["US"],
        output_dir=tmp_path,
        pull_history=fake_history,
        force=True,
    )

    assert result.count == 6
    metrics = read_research_metric_cache(tmp_path)
    tech = next(row for row in metrics if row.symbol == "XLK")
    assert tech.domain == "sector_performance"
    assert tech.value == "+20.00%"
    assert "行业代理" in tech.note
    assert "完整行业指数" in tech.note


def test_pull_sector_performance_rejects_unknown_market(tmp_path: Path) -> None:
    try:
        pull_sector_performance(markets=["JP"], output_dir=tmp_path)
    except ValueError as error:
        assert "US、HK、CN" in str(error)
    else:
        raise AssertionError("unknown market should fail clearly")


def test_pull_sector_performance_reuses_fresh_cache(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = []
    for symbol in ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC"]:
        rows.extend(
            {
                "symbol": symbol,
                "domain": "sector_performance",
                "name": f"{symbol} 20交易日变化",
                "value": "+1.00%",
                "as_of": "2026-07-18",
                "source_url": "https://finance.yahoo.com/quote/XLK",
                "note": "proxy",
                "provider": "sector_proxy_yahoo",
            }
            for _ in [0]
        )
    (data_dir / "research-metrics.json").write_text(
        json.dumps({"provider": "sector_proxy_yahoo", "rows": rows}),
        encoding="utf-8",
    )
    record_research_metrics_cache(
        output_dir=tmp_path,
        provider="sector_proxy_yahoo",
        symbols=[f"SECTOR:{symbol}" for symbol in ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC"]],
        artifact_path=data_dir / "research-metrics.json",
        row_count=6,
    )

    def should_not_pull(**kwargs: object) -> PullResult:
        raise AssertionError("fresh sector cache should be reused")

    result = pull_sector_performance(
        markets=["US"],
        output_dir=tmp_path,
        pull_history=should_not_pull,
    )

    assert result.refreshed is False
    assert result.count == 6
