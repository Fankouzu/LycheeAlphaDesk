import json
from pathlib import Path

from lychee_alphadesk.core.config import default_config, save_config
from lychee_alphadesk.core.live_data import (
    build_cached_data_snapshot,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    run_cached_data_health,
)


def test_pull_market_prices_writes_alpha_vantage_cache(tmp_path: Path) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert "TIME_SERIES_DAILY" in url
        assert "apikey=demo-alpha-key" in url
        return {
            "Meta Data": {"2. Symbol": "AAPL"},
            "Time Series (Daily)": {
                "2026-07-02": {"4. close": "214.33", "5. volume": "51230000"},
                "2026-07-01": {"4. close": "211.00", "5. volume": "40000000"},
            },
        }

    result = pull_market_prices(
        symbols=["AAPL"],
        config_path=config_path,
        output_dir=tmp_path,
        fetch_json=fetch_json,
    )

    assert result.count == 1
    assert result.output_path == tmp_path / "data" / "market-prices.json"
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["provider"] == "alpha_vantage"
    assert cache["rows"][0] == {
        "symbol": "AAPL",
        "date": "2026-07-02",
        "close": 214.33,
        "volume": 51230000,
        "currency": "USD",
    }


def test_pull_news_events_writes_finnhub_cache(tmp_path: Path) -> None:
    config = default_config()
    config.providers["finnhub"].value = "demo-finnhub-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert "company-news" in url
        assert "token=demo-finnhub-key" in url
        return [
            {
                "datetime": 1783036800,
                "headline": "Apple reports services growth",
                "summary": "Services revenue increased.",
                "url": "https://example.com/aapl-news",
            }
        ]

    result = pull_news_events(
        symbols=["AAPL"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="finnhub",
        start_date="2026-07-01",
        end_date="2026-07-03",
        fetch_json=fetch_json,
    )

    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["provider"] == "finnhub"
    assert cache["rows"][0]["headline"] == "Apple reports services growth"
    assert cache["rows"][0]["symbols"] == ["AAPL"]
    assert cache["rows"][0]["source_url"] == "https://example.com/aapl-news"


def test_auto_news_provider_falls_back_when_first_configured_provider_fails(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["marketaux"].value = "demo-marketaux-key"
    config.providers["finnhub"].value = "demo-finnhub-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        if "marketaux" in url:
            raise RuntimeError("Could not fetch JSON from https://api.marketaux.com?api_token=***")
        assert "company-news" in url
        return [
            {
                "datetime": 1783036800,
                "headline": "Fallback news",
                "summary": "Finnhub fallback worked.",
                "url": "https://example.com/fallback",
            }
        ]

    result = pull_news_events(
        symbols=["AAPL"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
    )

    assert result.provider == "finnhub"
    assert result.count == 1
    assert result.warnings == ["marketaux failed; trying next configured news provider"]


def test_fetch_errors_mask_secret_query_values(tmp_path: Path) -> None:
    config = default_config()
    config.providers["finnhub"].value = "secret-finnhub-token"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        raise RuntimeError(f"Could not fetch JSON from {url}")

    try:
        pull_news_events(
            symbols=["AAPL"],
            config_path=config_path,
            output_dir=tmp_path,
            provider_id="finnhub",
            fetch_json=fetch_json,
        )
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected provider fetch failure")

    assert "secret-finnhub-token" not in message
    assert "token=***" in message


def test_pull_sec_filings_writes_filing_cache(tmp_path: Path) -> None:
    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert headers and "LycheeAlphaDesk" in headers["User-Agent"]
        if url.endswith("company_tickers.json"):
            return {
                "0": {
                    "ticker": "AAPL",
                    "cik_str": 320193,
                    "title": "Apple Inc.",
                }
            }
        return {
            "filings": {
                "recent": {
                    "form": ["10-K", "8-K"],
                    "filingDate": ["2025-10-31", "2025-08-01"],
                    "accessionNumber": ["0000320193-25-000079", "0000320193-25-000050"],
                    "primaryDocument": ["aapl-20250927.htm", "aapl-20250801.htm"],
                }
            }
        }

    result = pull_sec_filings(
        symbols=["AAPL"],
        output_dir=tmp_path,
        fetch_json=fetch_json,
        limit_per_symbol=1,
    )

    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["provider"] == "sec_edgar"
    assert cache["rows"][0]["company"] == "Apple Inc."
    assert cache["rows"][0]["form"] == "10-K"
    assert "000032019325000079" in cache["rows"][0]["source_url"]


def test_cached_snapshot_aggregates_live_cache_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "alpha_vantage",
                "rows": [
                    {
                        "symbol": "AAPL",
                        "date": "2026-07-02",
                        "close": 214.33,
                        "volume": 51230000,
                        "currency": "USD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "finnhub",
                "rows": [
                    {
                        "timestamp": "2026-07-02T00:00:00+00:00",
                        "headline": "Apple reports services growth",
                        "summary": "Services revenue increased.",
                        "symbols": ["AAPL"],
                        "source_url": "https://example.com/aapl-news",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2025-10-31",
                        "company": "Apple Inc.",
                        "form": "10-K",
                        "summary": "AAPL 10-K filed on 2025-10-31.",
                        "source_url": "https://www.sec.gov/Archives/edgar/data/320193/example.htm",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_cached_data_snapshot(tmp_path)

    assert snapshot.mode == "live"
    assert snapshot.provider_names == ["alpha_vantage", "finnhub", "sec_edgar"]
    assert snapshot.counts["prices"] == 1
    assert snapshot.counts["news_events"] == 1
    assert snapshot.counts["filings"] == 1


def test_cached_data_health_reports_missing_and_present_cache_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        json.dumps({"provider": "alpha_vantage", "rows": []}),
        encoding="utf-8",
    )

    checks = {check.name: check for check in run_cached_data_health(tmp_path)}

    assert checks["market-cache-present"].status == "warning"
    assert checks["news-cache-present"].status == "error"
