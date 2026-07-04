import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PriceRow:
    symbol: str
    date: str
    close: float
    volume: int
    currency: str


@dataclass(frozen=True)
class NewsEvent:
    timestamp: str
    headline: str
    summary: str
    symbols: list[str]
    source_url: str


@dataclass(frozen=True)
class FilingSummary:
    date: str
    company: str
    form: str
    summary: str
    source_url: str


@dataclass(frozen=True)
class ForecastInterval:
    symbol: str
    horizon_days: int
    lower: float
    midpoint: float
    upper: float
    method: str


class DemoMarketDataProvider:
    name = "demo-market-data"

    def __init__(self, path: Path) -> None:
        self.path = path

    def latest_prices(self) -> list[PriceRow]:
        with self.path.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            return [
                PriceRow(
                    symbol=row["symbol"],
                    date=row["date"],
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                    currency=row["currency"],
                )
                for row in reader
            ]


class DemoNewsProvider:
    name = "demo-news"

    def __init__(self, path: Path) -> None:
        self.path = path

    def events(self) -> list[NewsEvent]:
        return [
            NewsEvent(
                timestamp=item["timestamp"],
                headline=item["headline"],
                summary=item["summary"],
                symbols=item["symbols"],
                source_url=item["source_url"],
            )
            for item in _read_jsonl(self.path)
        ]


class DemoFilingProvider:
    name = "demo-filings"

    def __init__(self, path: Path) -> None:
        self.path = path

    def filings(self) -> list[FilingSummary]:
        return [
            FilingSummary(
                date=item["date"],
                company=item["company"],
                form=item["form"],
                summary=item["summary"],
                source_url=item["source_url"],
            )
            for item in _read_jsonl(self.path)
        ]


class DemoForecastProvider:
    name = "demo-forecast"

    def forecast_intervals(self, symbols: list[str]) -> dict[str, ForecastInterval]:
        intervals: dict[str, ForecastInterval] = {}
        for index, symbol in enumerate(symbols):
            midpoint = 100 + index * 8
            intervals[symbol] = ForecastInterval(
                symbol=symbol,
                horizon_days=20,
                lower=midpoint * 0.94,
                midpoint=midpoint,
                upper=midpoint * 1.06,
                method="mock-baseline",
            )
        return intervals


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows
