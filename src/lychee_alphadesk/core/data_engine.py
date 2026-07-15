import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from lychee_alphadesk.core.paths import DEMO_ROOT
from lychee_alphadesk.providers.demo import (
    DemoFilingProvider,
    DemoForecastProvider,
    DemoMarketDataProvider,
    DemoNewsProvider,
    FilingSummary,
    ForecastInterval,
    NewsEvent,
    PriceRow,
)

QualityStatus = Literal["pass", "warning", "error"]


@dataclass(frozen=True)
class DataQualityCheck:
    name: str
    status: QualityStatus
    message: str
    provider: str


@dataclass(frozen=True)
class DataSnapshot:
    mode: Literal["demo", "live"]
    created_at: str
    provider_names: list[str]
    prices: list[PriceRow]
    news_events: list[NewsEvent]
    filings: list[FilingSummary]
    financials: list[dict[str, object]]
    forecasts: dict[str, ForecastInterval]
    quality_checks: list[DataQualityCheck]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "prices": len(self.prices),
            "news_events": len(self.news_events),
            "filings": len(self.filings),
            "financials": len(self.financials),
            "forecasts": len(self.forecasts),
        }


def build_demo_data_snapshot(demo_root: Path = DEMO_ROOT) -> DataSnapshot:
    market_provider = DemoMarketDataProvider(demo_root / "market_data.csv")
    news_provider = DemoNewsProvider(demo_root / "news.jsonl")
    filing_provider = DemoFilingProvider(demo_root / "filings.jsonl")
    forecast_provider = DemoForecastProvider()

    prices = market_provider.latest_prices()
    news_events = news_provider.events()
    filings = filing_provider.filings()
    forecasts = forecast_provider.forecast_intervals([price.symbol for price in prices])

    provider_names = [
        market_provider.name,
        news_provider.name,
        filing_provider.name,
        forecast_provider.name,
    ]

    return DataSnapshot(
        mode="demo",
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        provider_names=provider_names,
        prices=prices,
        news_events=news_events,
        filings=filings,
        financials=[],
        forecasts=forecasts,
        quality_checks=run_quality_checks(
            prices=prices,
            news_events=news_events,
            filings=filings,
            forecasts=forecasts,
        ),
    )


def run_quality_checks(
    *,
    prices: list[PriceRow],
    news_events: list[NewsEvent],
    filings: list[FilingSummary],
    forecasts: dict[str, ForecastInterval],
) -> list[DataQualityCheck]:
    checks = [
        _present_check(
            name="market-data-present",
            provider=DemoMarketDataProvider.name,
            count=len(prices),
            noun="行情数据",
        ),
        _present_check(
            name="news-events-present",
            provider=DemoNewsProvider.name,
            count=len(news_events),
            noun="新闻事件",
        ),
        _present_check(
            name="filings-present",
            provider=DemoFilingProvider.name,
            count=len(filings),
            noun="公告摘要",
        ),
    ]

    price_symbols = {price.symbol for price in prices}
    forecast_symbols = set(forecasts)
    missing_forecasts = sorted(price_symbols.difference(forecast_symbols))
    if missing_forecasts:
        checks.append(
            DataQualityCheck(
                name="forecast-coverage",
                status="warning",
                message="缺少以下代码的预测: " + ", ".join(missing_forecasts),
                provider=DemoForecastProvider.name,
            )
        )
    else:
        checks.append(
            DataQualityCheck(
                name="forecast-coverage",
                status="pass",
                message=f"预测已覆盖 {len(forecasts)} 个代码",
                provider=DemoForecastProvider.name,
            )
        )

    return checks


def write_snapshot_json(snapshot: DataSnapshot, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"data-snapshot-{snapshot.mode}.json"
    output_path.write_text(
        json.dumps(snapshot_to_dict(snapshot), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def snapshot_to_dict(snapshot: DataSnapshot) -> dict[str, object]:
    return {
        "mode": snapshot.mode,
        "created_at": snapshot.created_at,
        "provider_names": snapshot.provider_names,
        "counts": snapshot.counts,
        "prices": [asdict(price) for price in snapshot.prices],
        "news_events": [asdict(event) for event in snapshot.news_events],
        "filings": [asdict(filing) for filing in snapshot.filings],
        "financials": snapshot.financials,
        "forecasts": {
            symbol: forecast_to_dict(forecast)
            for symbol, forecast in snapshot.forecasts.items()
        },
        "quality_checks": [asdict(check) for check in snapshot.quality_checks],
    }


def forecast_to_dict(forecast: ForecastInterval) -> dict[str, object]:
    return {
        "symbol": forecast.symbol,
        "horizon_days": forecast.horizon_days,
        "lower": round(forecast.lower, 4),
        "midpoint": round(forecast.midpoint, 4),
        "upper": round(forecast.upper, 4),
        "method": forecast.method,
    }


def _present_check(*, name: str, provider: str, count: int, noun: str) -> DataQualityCheck:
    if count == 0:
        return DataQualityCheck(
            name=name,
            status="error",
            message=f"未加载{noun}",
            provider=provider,
        )
    return DataQualityCheck(
        name=name,
        status="pass",
        message=f"已加载 {count} 条{noun}",
        provider=provider,
    )
