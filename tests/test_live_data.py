import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import record_cache_entry
from lychee_alphadesk.core.config import default_config, save_config
from lychee_alphadesk.core.live_data import (
    build_cached_data_snapshot,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    run_cached_data_health,
)
from lychee_alphadesk.core.research_db import init_research_db


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


def test_pull_market_prices_migrates_existing_research_db_without_cache_table(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    db_path = init_research_db(tmp_path)
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "research_candidates" in tables
    assert "cache_entries" not in tables

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        return {
            "Time Series (Daily)": {
                "2026-07-02": {"4. close": "214.33", "5. volume": "51230000"},
            },
        }

    result = pull_market_prices(
        symbols=["AAPL"],
        config_path=config_path,
        output_dir=tmp_path,
        fetch_json=fetch_json,
        now=datetime(2026, 7, 6, 14, 0, tzinfo=UTC),
    )

    assert result.count == 1
    with sqlite3.connect(db_path) as connection:
        migrated_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "cache_entries" in migrated_tables


def test_pull_market_prices_skips_fresh_open_market_cache(tmp_path: Path) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "market-prices.json"
    cache_path.write_text(
        json.dumps(
            {
                "provider": "alpha_vantage",
                "created_at": "2026-07-06T13:55:00+00:00",
                "warnings": [],
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
    now = datetime(2026, 7, 6, 14, 0, tzinfo=UTC)
    record_cache_entry(
        output_dir=tmp_path,
        layer="market",
        cache_key="market:alpha_vantage:AAPL",
        provider="alpha_vantage",
        artifact_path=cache_path,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=10),
        ttl_seconds=900,
        row_count=1,
        market="US",
        session_state="open",
        is_final_for_session=False,
    )

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        raise AssertionError("fresh market cache should skip provider fetch")

    result = pull_market_prices(
        symbols=["AAPL"],
        config_path=config_path,
        output_dir=tmp_path,
        fetch_json=fetch_json,
        now=now,
    )

    assert result.refreshed is False
    assert result.count == 1
    assert "行情缓存仍在保质期内" in result.warnings[0]


def test_pull_market_prices_force_refreshes_fresh_cache(tmp_path: Path) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "market-prices.json"
    cache_path.write_text(
        json.dumps({"provider": "alpha_vantage", "rows": []}),
        encoding="utf-8",
    )
    now = datetime(2026, 7, 6, 14, 0, tzinfo=UTC)
    record_cache_entry(
        output_dir=tmp_path,
        layer="market",
        cache_key="market:alpha_vantage:AAPL",
        provider="alpha_vantage",
        artifact_path=cache_path,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        ttl_seconds=900,
        row_count=0,
        market="US",
        session_state="open",
        is_final_for_session=False,
    )

    calls = 0

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        nonlocal calls
        calls += 1
        return {
            "Time Series (Daily)": {
                "2026-07-02": {"4. close": "214.33", "5. volume": "51230000"},
            },
        }

    result = pull_market_prices(
        symbols=["AAPL"],
        config_path=config_path,
        output_dir=tmp_path,
        fetch_json=fetch_json,
        now=now,
        force=True,
    )

    assert calls == 1
    assert result.refreshed is True
    assert result.count == 1


def test_pull_market_prices_skips_final_cache_after_close(tmp_path: Path) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "market-prices.json"
    cache_path.write_text(
        json.dumps(
            {
                "provider": "alpha_vantage",
                "rows": [
                    {
                        "symbol": "AAPL",
                        "date": "2026-07-06",
                        "close": 220.0,
                        "volume": 60000000,
                        "currency": "USD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 7, 6, 22, 0, tzinfo=UTC)
    record_cache_entry(
        output_dir=tmp_path,
        layer="market",
        cache_key="market:alpha_vantage:AAPL",
        provider="alpha_vantage",
        artifact_path=cache_path,
        created_at=now - timedelta(hours=1),
        expires_at=datetime(2026, 7, 7, 13, 30, tzinfo=UTC),
        ttl_seconds=900,
        row_count=1,
        market="US",
        session_state="closed",
        is_final_for_session=True,
    )

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        raise AssertionError("final close cache should skip after market close")

    result = pull_market_prices(
        symbols=["AAPL"],
        config_path=config_path,
        output_dir=tmp_path,
        fetch_json=fetch_json,
        now=now,
    )

    assert result.refreshed is False
    assert "收盘后跳过刷新" in result.warnings[0]


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
    assert result.warnings == ["marketaux 失败，正在尝试下一个已配置新闻数据源"]


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
