from pathlib import Path

from lychee_alphadesk.providers.demo import (
    DemoFilingProvider,
    DemoForecastProvider,
    DemoMarketDataProvider,
    DemoNewsProvider,
)

DEMO_ROOT = Path("examples/demo")


def test_demo_market_provider_loads_prices() -> None:
    rows = DemoMarketDataProvider(DEMO_ROOT / "market_data.csv").latest_prices()

    assert rows[0].symbol == "SPY"
    assert rows[0].close > 0


def test_demo_news_provider_loads_events() -> None:
    events = DemoNewsProvider(DEMO_ROOT / "news.jsonl").events()

    assert events[0].headline
    assert "SPY" in events[0].symbols


def test_demo_filing_provider_loads_filing_summaries() -> None:
    filings = DemoFilingProvider(DEMO_ROOT / "filings.jsonl").filings()

    assert filings[0].company
    assert filings[0].source_url.startswith("https://")


def test_demo_forecast_provider_returns_intervals() -> None:
    forecasts = DemoForecastProvider().forecast_intervals(["SPY", "QQQ"])

    assert forecasts["SPY"].horizon_days == 20
    assert forecasts["SPY"].lower < forecasts["SPY"].upper
