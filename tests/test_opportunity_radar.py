import json
from pathlib import Path

from lychee_alphadesk.core.opportunity_radar import build_opportunity_radar


def _write_cache(output_dir: Path, filename: str, rows: list[dict[str, object]]) -> None:
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / filename).write_text(
        json.dumps(
            {
                "created_at": "2026-07-08T00:00:00+00:00",
                "provider": "test",
                "rows": rows,
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_opportunity_radar_ranks_niche_theme_above_obvious_mega_cap(
    tmp_path: Path,
) -> None:
    _write_cache(
        tmp_path,
        "market-prices.json",
        [
            {
                "symbol": "NVDA",
                "date": "2026-07-07",
                "close": 180.0,
                "volume": 120_000_000,
                "currency": "USD",
            },
            {
                "symbol": "STX",
                "date": "2026-07-07",
                "close": 130.0,
                "volume": 18_000_000,
                "currency": "USD",
            },
        ],
    )
    _write_cache(
        tmp_path,
        "news-events.json",
        [
            {
                "timestamp": "2026-07-07T10:00:00+00:00",
                "headline": "AI storage demand lifts hard-drive suppliers",
                "summary": "Data center buyers are paying more for high-capacity storage.",
                "symbols": ["STX"],
                "source_url": "https://example.com/stx-1",
            },
            {
                "timestamp": "2026-07-07T11:00:00+00:00",
                "headline": "Cloud data center expansion increases storage backlog",
                "summary": "Server and storage suppliers see tighter supply.",
                "symbols": ["STX"],
                "source_url": "https://example.com/stx-2",
            },
            {
                "timestamp": "2026-07-07T12:00:00+00:00",
                "headline": "Nvidia remains a central AI chip benchmark",
                "summary": "Investors continue watching the AI semiconductor leader.",
                "symbols": ["NVDA"],
                "source_url": "https://example.com/nvda",
            },
        ],
    )

    report = build_opportunity_radar(output_dir=tmp_path, limit=3)

    assert report.signals[0].symbol == "STX"
    assert report.signals[0].theme == "AI 基础设施扩散"
    assert report.signals[0].news_count == 2
    assert "新闻热度" in report.signals[0].why_it_matters
    assert "lychee data pull news --symbols STX" in report.signals[0].next_steps[0]
    assert all("买入" not in signal.why_it_matters for signal in report.signals)


def test_opportunity_radar_maps_broad_theme_to_cached_drilldown_targets(
    tmp_path: Path,
) -> None:
    _write_cache(
        tmp_path,
        "market-prices.json",
        [
            {
                "symbol": "QQQ",
                "date": "2026-07-07",
                "close": 708.0,
                "volume": 50_000_000,
                "currency": "USD",
            },
            {
                "symbol": "NVDA",
                "date": "2026-07-07",
                "close": 190.0,
                "volume": 120_000_000,
                "currency": "USD",
            },
            {
                "symbol": "512480.SH",
                "date": "2026-07-07",
                "close": 1.33,
                "volume": 1_500_000_000,
                "currency": "CNY",
            },
        ],
    )
    _write_cache(
        tmp_path,
        "news-events.json",
        [
            {
                "timestamp": "2026-07-07T10:00:00+00:00",
                "headline": "AI data center semiconductor demand lifts QQQ",
                "summary": "Cloud and chip stocks drive market trading in technology ETFs.",
                "symbols": ["QQQ"],
                "source_url": "https://example.com/qqq-ai",
            }
        ],
    )

    report = build_opportunity_radar(output_dir=tmp_path, limit=5)

    assert report.signals[0].symbol == "QQQ"
    targets = report.signals[0].drilldown_targets
    assert [target.symbol for target in targets] == ["NVDA", "512480.SH"]
    assert targets[0].display_name == "NVIDIA"
    assert targets[0].evidence_gap == "缺少该标的的主题新闻缓存，需补新闻验证。"
    assert "lychee data pull news --symbols NVDA" in targets[0].next_steps[0]
    assert all("买入" not in target.reason for target in targets)


def test_opportunity_radar_requires_local_market_and_news_cache(tmp_path: Path) -> None:
    report = build_opportunity_radar(output_dir=tmp_path, limit=5)

    assert report.status == "blocked"
    assert not report.signals
    assert "缺少本地行情或新闻缓存" in report.warnings[0]


def test_opportunity_radar_filters_symbol_noise_without_market_context(
    tmp_path: Path,
) -> None:
    _write_cache(
        tmp_path,
        "market-prices.json",
        [
            {
                "symbol": "2800.HK",
                "date": "2026-07-07",
                "close": 23.9,
                "volume": 90_000_000,
                "currency": "HKD",
            },
            {
                "symbol": "3033.HK",
                "date": "2026-07-07",
                "close": 4.4,
                "volume": 120_000_000,
                "currency": "HKD",
            },
        ],
    )
    _write_cache(
        tmp_path,
        "news-events.json",
        [
            {
                "timestamp": "2026-07-07T10:00:00+00:00",
                "headline": "qwen-asr-pvt added to PyPI",
                "summary": "A software package update is unrelated to market trading.",
                "symbols": ["2800.HK"],
                "source_url": "https://example.com/pypi",
            },
            {
                "timestamp": "2026-07-07T11:00:00+00:00",
                "headline": "US stock market hits record capitalization",
                "summary": "US stocks account for a larger share of global market cap.",
                "symbols": ["2800.HK"],
                "source_url": "https://example.com/us-market",
            },
            {
                "timestamp": "2026-07-07T12:00:00+00:00",
                "headline": "Hong Kong ETF turnover rises as southbound flows improve",
                "summary": "Hang Seng China asset ETFs saw stronger market liquidity.",
                "symbols": ["3033.HK"],
                "source_url": "https://example.com/hk-etf",
            },
        ],
    )

    report = build_opportunity_radar(output_dir=tmp_path, limit=5)

    assert [signal.symbol for signal in report.signals] == ["3033.HK"]
    assert "Hong Kong ETF turnover rises" in report.signals[0].evidence[0]


def test_opportunity_radar_does_not_count_theme_terms_inside_other_words(
    tmp_path: Path,
) -> None:
    _write_cache(
        tmp_path,
        "market-prices.json",
        [
            {
                "symbol": "QQQ",
                "date": "2026-07-07",
                "close": 708.0,
                "volume": 50_000_000,
                "currency": "USD",
            }
        ],
    )
    _write_cache(
        tmp_path,
        "news-events.json",
        [
            {
                "timestamp": "2026-07-07T10:00:00+00:00",
                "headline": "The millionaire boom is real but market risk is rising",
                "summary": "Investors discuss stocks and portfolio habits, not automation.",
                "symbols": ["QQQ"],
                "source_url": "https://example.com/millionaire",
            }
        ],
    )

    report = build_opportunity_radar(output_dir=tmp_path, limit=5)

    assert not report.signals
