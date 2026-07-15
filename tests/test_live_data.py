import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import list_cache_entries, record_cache_entry
from lychee_alphadesk.core.config import default_config, save_config
from lychee_alphadesk.core.live_data import (
    _parse_sec_financial_snapshot,
    build_cached_data_snapshot,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    pull_sec_financials,
    read_research_metric_cache,
    run_cached_data_health,
    write_fund_metadata_cache,
    write_research_metric_cache,
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


def test_pull_sec_financials_writes_auditable_latest_xbrl_snapshot(
    tmp_path: Path,
) -> None:
    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert headers is not None
        assert headers["User-Agent"]
        if url.endswith("company_tickers.json"):
            return {
                "0": {
                    "cik_str": 320193,
                    "ticker": "AAPL",
                    "title": "Apple Inc.",
                }
            }
        assert url.endswith("CIK0000320193.json")
        return {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2026-01-01",
                                    "end": "2026-03-28",
                                    "val": 95359000000,
                                    "fy": 2026,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2026-05-01",
                                },
                                {
                                    "start": "2025-01-02",
                                    "end": "2025-03-29",
                                    "val": 86700000000,
                                    "fy": 2025,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2025-05-02",
                                }
                            ]
                        }
                    },
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2026-01-01",
                                    "end": "2026-03-28",
                                    "val": 24780000000,
                                    "fy": 2026,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2026-05-01",
                                },
                                {
                                    "start": "2025-01-02",
                                    "end": "2025-03-29",
                                    "val": 23636000000,
                                    "fy": 2025,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2025-05-02",
                                }
                            ]
                        }
                    },
                    "NetCashProvidedByUsedInOperatingActivities": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2025-10-01",
                                    "end": "2026-03-28",
                                    "val": 23951000000,
                                    "fy": 2026,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2026-05-01",
                                },
                                {
                                    "start": "2024-10-02",
                                    "end": "2025-03-29",
                                    "val": 22100000000,
                                    "fy": 2025,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2025-05-02",
                                }
                            ]
                        }
                    },
                }
            }
        }

    result = pull_sec_financials(
        symbols=["AAPL"],
        output_dir=tmp_path,
        fetch_json=fetch_json,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.domain == "financials"
    assert result.provider == "sec_edgar"
    assert result.count == 1
    assert result.output_path == tmp_path / "data" / "financials.json"
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"] == [
        {
            "symbol": "AAPL",
            "company": "Apple Inc.",
            "cik": 320193,
            "form": "10-Q",
            "fiscal_year": 2026,
            "fiscal_period": "Q2",
            "period_end": "2026-03-28",
            "filing_date": "2026-05-01",
            "currency": "USD",
            "revenue": 95359000000,
            "revenue_period_start": "2026-01-01",
            "revenue_period_end": "2026-03-28",
            "revenue_prior": 86700000000,
            "revenue_prior_period_start": "2025-01-02",
            "revenue_prior_period_end": "2025-03-29",
            "net_income": 24780000000,
            "net_income_period_start": "2026-01-01",
            "net_income_period_end": "2026-03-28",
            "net_income_prior": 23636000000,
            "net_income_prior_period_start": "2025-01-02",
            "net_income_prior_period_end": "2025-03-29",
            "operating_cash_flow": 23951000000,
            "operating_cash_flow_period_start": "2025-10-01",
            "operating_cash_flow_period_end": "2026-03-28",
            "operating_cash_flow_prior": 22100000000,
            "operating_cash_flow_prior_period_start": "2024-10-02",
            "operating_cash_flow_prior_period_end": "2025-03-29",
            "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        }
    ]

    cached = pull_sec_financials(
        symbols=["AAPL"],
        output_dir=tmp_path,
        fetch_json=lambda _url, _headers=None: (_ for _ in ()).throw(
            AssertionError("fresh financial cache should not call SEC")
        ),
        now=datetime(2026, 7, 5, 12, 0, tzinfo=UTC),
    )

    assert cached.refreshed is False
    assert cached.count == 1
    assert "保质期内" in cached.warnings[0]


def test_sec_financial_snapshot_skips_prior_value_with_mismatched_duration() -> None:
    snapshot = _parse_sec_financial_snapshot(
        symbol="TEST",
        company="Example Inc.",
        cik=1,
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
        payload={
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2026-01-01",
                                    "end": "2026-03-28",
                                    "val": 100,
                                    "fy": 2026,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2026-05-01",
                                },
                                {
                                    "start": "2024-10-02",
                                    "end": "2025-03-29",
                                    "val": 200,
                                    "fy": 2025,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2025-05-02",
                                },
                            ]
                        }
                    }
                }
            }
        },
    )

    assert snapshot is not None
    assert snapshot.revenue == 100
    assert snapshot.revenue_prior is None


def test_write_fund_metadata_cache_preserves_source_backed_proxy_details(
    tmp_path: Path,
) -> None:
    result = write_fund_metadata_cache(
        output_dir=tmp_path,
        symbol="2800.HK",
        display_name="盈富基金",
        market="HK",
        tracking_index="Hang Seng Index",
        expense_ratio="0.10%",
        holdings_summary="跟踪恒生指数成分股",
        source_url="https://example.com/2800",
        as_of="2026-07-05",
        provider="manual",
    )

    assert result.count == 1
    assert result.output_path == tmp_path / "data" / "fund-metadata.json"
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["provider"] == "manual"
    assert cache["rows"] == [
        {
            "symbol": "2800.HK",
            "display_name": "盈富基金",
            "market": "HK",
            "tracking_index": "Hang Seng Index",
            "expense_ratio": "0.10%",
            "holdings_summary": "跟踪恒生指数成分股",
            "source_url": "https://example.com/2800",
            "as_of": "2026-07-05",
            "provider": "manual",
        }
    ]


def test_write_research_metric_cache_preserves_source_backed_metric(
    tmp_path: Path,
) -> None:
    result = write_research_metric_cache(
        output_dir=tmp_path,
        symbol="QQQ",
        domain="market_breadth",
        name="纳斯达克100上涨家数",
        value="63/100",
        as_of="2026-07-07",
        source_url="https://example.com/nasdaq100-breadth",
        note="上涨家数高于下跌家数，需结合等权指数核验。",
    )

    assert result.domain == "research_metric"
    assert result.provider == "manual"
    assert result.count == 1
    assert result.output_path == tmp_path / "data" / "research-metrics.json"

    rows = read_research_metric_cache(tmp_path)

    assert len(rows) == 1
    metric = rows[0]
    assert metric.symbol == "QQQ"
    assert metric.domain == "market_breadth"
    assert metric.name == "纳斯达克100上涨家数"
    assert metric.value == "63/100"
    assert metric.source_url == "https://example.com/nasdaq100-breadth"
    assert metric.note == "上涨家数高于下跌家数，需结合等权指数核验。"


def test_pull_market_prices_auto_uses_eastmoney_for_hk_symbols(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    seen_urls: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        seen_urls.append(url)
        if "alphavantage.co" in url:
            return {
                "Time Series (Daily)": {
                    "2026-07-02": {"4. close": "90.50", "5. volume": "3000000"},
                },
            }
        assert "push2his.eastmoney.com" in url
        assert "116.03456" in url
        return {
            "data": {
                "code": "03456",
                "market": 116,
                "name": "易方达港交所科技",
                "decimal": 3,
                "klines": [
                    "2026-07-01,7.700,7.750,7.800,7.650,600000,4650000",
                    "2026-07-02,7.750,7.900,7.955,7.750,647200,5128971",
                ],
            }
        }

    result = pull_market_prices(
        symbols=["BABA", "3456.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
    )

    assert result.count == 2
    assert any("alphavantage.co" in url for url in seen_urls)
    assert any("push2his.eastmoney.com" in url for url in seen_urls)
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["provider"] == "auto"
    assert cache["rows"] == [
        {
            "symbol": "BABA",
            "date": "2026-07-02",
            "close": 90.5,
            "volume": 3000000,
            "currency": "USD",
        },
        {
            "symbol": "3456.HK",
            "date": "2026-07-02",
            "close": 7.9,
            "volume": 647200,
            "currency": "HKD",
        },
    ]


def test_pull_market_prices_auto_keeps_alpha_rows_when_eastmoney_fails(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        if "alphavantage.co" in url:
            return {
                "Time Series (Daily)": {
                    "2026-07-02": {"4. close": "90.50", "5. volume": "3000000"},
                },
            }
        raise RuntimeError("Eastmoney unavailable")

    result = pull_market_prices(
        symbols=["BABA", "3456.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
    )

    assert result.count == 1
    assert "Eastmoney unavailable" in result.warnings[0]
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"][0]["symbol"] == "BABA"


def test_pull_market_prices_preserves_existing_rows_when_refresh_returns_no_rows(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "market-prices.json"
    cache_path.write_text(
        json.dumps(
            {
                "provider": "auto",
                "rows": [
                    {
                        "symbol": "BABA",
                        "date": "2026-07-02",
                        "close": 90.5,
                        "volume": 3000000,
                        "currency": "USD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        raise RuntimeError("Eastmoney unavailable")

    result = pull_market_prices(
        symbols=["3456.HK"],
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
        force=True,
    )

    assert result.count == 0
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"] == [
        {
            "symbol": "BABA",
            "date": "2026-07-02",
            "close": 90.5,
            "volume": 3000000,
            "currency": "USD",
        }
    ]


def test_pull_market_prices_auto_falls_back_to_yahoo_chart(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        if "alphavantage.co" in url:
            return {}
        if "push2his.eastmoney.com" in url:
            raise RuntimeError("Eastmoney unavailable")
        assert "query1.finance.yahoo.com" in url
        assert headers is not None
        assert "Mozilla" in headers["User-Agent"]
        symbol = "BABA" if "BABA" in url else "3456.HK"
        close = 96.14 if symbol == "BABA" else 7.9
        currency = "USD" if symbol == "BABA" else "HKD"
        return {
            "chart": {
                "result": [
                    {
                        "meta": {"symbol": symbol, "currency": currency},
                        "timestamp": [1782950400],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [close],
                                    "volume": [3000000],
                                }
                            ]
                        },
                    }
                ]
            }
        }

    result = pull_market_prices(
        symbols=["BABA", "3456.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
    )

    assert result.count == 2
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert [row["symbol"] for row in cache["rows"]] == ["BABA", "3456.HK"]
    assert cache["rows"][0]["close"] == 96.14
    assert cache["rows"][1]["currency"] == "HKD"


def test_pull_market_prices_yahoo_fallback_uses_ss_suffix_for_shanghai_symbols(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    seen_urls: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        seen_urls.append(url)
        if "push2his.eastmoney.com" in url:
            raise RuntimeError("Eastmoney unavailable")
        assert "512480.SS" in url
        assert "512480.SH" not in url
        return {
            "chart": {
                "result": [
                    {
                        "meta": {"symbol": "512480.SS", "currency": "CNY"},
                        "timestamp": [1782950400],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [1.331],
                                    "volume": [15949373],
                                }
                            ]
                        },
                    }
                ]
            }
        }

    result = pull_market_prices(
        symbols=["512480.SH"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
    )

    assert result.count == 1
    assert any("512480.SS" in url for url in seen_urls)
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"][0]["symbol"] == "512480.SH"
    assert cache["rows"][0]["currency"] == "CNY"


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


def test_pull_sec_filings_uses_common_baba_cik_when_ticker_fetch_fails(
    tmp_path: Path,
) -> None:
    seen_urls: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        seen_urls.append(url)
        if url.endswith("company_tickers.json"):
            raise RuntimeError("SEC unavailable")
        assert "CIK0001577552.json" in url
        return {
            "filings": {
                "recent": {
                    "form": ["20-F"],
                    "filingDate": ["2026-06-30"],
                    "accessionNumber": ["0001104659-26-000001"],
                    "primaryDocument": ["baba-20260630x20f.htm"],
                }
            }
        }

    result = pull_sec_filings(
        symbols=["BABA"],
        output_dir=tmp_path,
        fetch_json=fetch_json,
    )

    assert result.count == 1
    assert "SEC 代码映射拉取失败" in result.warnings[0]
    assert any("CIK0001577552.json" in url for url in seen_urls)
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"][0]["company"] == "Alibaba Group Holding Limited"
    assert cache["rows"][0]["form"] == "20-F"


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


def test_pull_market_prices_refreshes_zero_row_final_cache(tmp_path: Path) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "market-prices.json"
    cache_path.write_text(
        json.dumps({"provider": "auto", "rows": []}),
        encoding="utf-8",
    )
    now = datetime(2026, 7, 6, 22, 0, tzinfo=UTC)
    record_cache_entry(
        output_dir=tmp_path,
        layer="market",
        cache_key="market:auto:BABA",
        provider="auto",
        artifact_path=cache_path,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
        ttl_seconds=900,
        row_count=0,
        market="US",
        session_state="closed",
        is_final_for_session=True,
    )
    calls = 0

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        nonlocal calls
        calls += 1
        return {
            "Time Series (Daily)": {
                "2026-07-02": {"4. close": "90.50", "5. volume": "3000000"},
            },
        }

    result = pull_market_prices(
        symbols=["BABA"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
        now=now,
    )

    assert calls == 1
    assert result.count == 1


def test_pull_market_prices_refreshes_when_cache_file_lacks_requested_symbol(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["alpha_vantage"].value = "demo-alpha-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "market-prices.json"
    cache_path.write_text(
        json.dumps({"provider": "auto", "rows": []}),
        encoding="utf-8",
    )
    now = datetime(2026, 7, 6, 22, 0, tzinfo=UTC)
    record_cache_entry(
        output_dir=tmp_path,
        layer="market",
        cache_key="market:auto:BABA",
        provider="auto",
        artifact_path=cache_path,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
        ttl_seconds=900,
        row_count=1,
        market="US",
        session_state="closed",
        is_final_for_session=True,
    )
    calls = 0

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        nonlocal calls
        calls += 1
        return {
            "Time Series (Daily)": {
                "2026-07-02": {"4. close": "90.50", "5. volume": "3000000"},
            },
        }

    result = pull_market_prices(
        symbols=["BABA"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
        now=now,
    )

    assert calls == 1
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
    entries = list_cache_entries(tmp_path, layer="news")
    assert len(entries) == 1
    assert entries[0].provider == "finnhub"
    assert entries[0].row_count == 1


def test_pull_news_events_without_symbols_writes_market_news_cache(tmp_path: Path) -> None:
    config = default_config()
    config.providers["newsapi"].value = "demo-newsapi-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert "newsapi.org/v2/everything" in url
        assert "apiKey=demo-newsapi-key" in url
        assert "stock" in url.lower()
        return {
            "articles": [
                {
                    "publishedAt": "2026-07-06T10:00:00Z",
                    "title": "Global markets watch AI infrastructure",
                    "description": "Investors watch chips, cloud and data centers.",
                    "url": "https://example.com/market-news",
                }
            ]
        }

    result = pull_news_events(
        symbols=[],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="newsapi",
        start_date="2026-07-01",
        end_date="2026-07-03",
        fetch_json=fetch_json,
    )

    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["provider"] == "newsapi"
    assert cache["rows"][0]["headline"] == "Global markets watch AI infrastructure"
    assert cache["rows"][0]["symbols"] == ["MARKET"]
    entries = list_cache_entries(tmp_path, layer="news")
    assert len(entries) == 1
    assert entries[0].cache_key == "news:newsapi:MARKET:2026-07-01:2026-07-03"


def test_pull_news_events_uses_topic_query_for_newsapi(tmp_path: Path) -> None:
    config = default_config()
    config.providers["newsapi"].value = "demo-newsapi-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    seen_urls: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        seen_urls.append(url)
        assert "newsapi.org/v2/everything" in url
        assert "AI+storage+demand" in url
        return {
            "articles": [
                {
                    "publishedAt": "2026-07-06T10:00:00Z",
                    "title": "AI storage demand improves",
                    "description": "Cloud buyers increased storage orders.",
                    "url": "https://example.com/topic-news",
                }
            ]
        }

    result = pull_news_events(
        symbols=["STX"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="newsapi",
        start_date="2026-07-01",
        end_date="2026-07-03",
        fetch_json=fetch_json,
        query="AI storage demand",
    )

    assert result.count == 1
    assert seen_urls
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"][0]["headline"] == "AI storage demand improves"
    assert cache["rows"][0]["symbols"] == ["STX"]
    entries = list_cache_entries(tmp_path, layer="news")
    assert len(entries) == 1
    assert entries[0].cache_key == (
        "news:newsapi:STX:2026-07-01:2026-07-03:AI storage demand"
    )


def test_pull_news_events_preserves_existing_news_rows(tmp_path: Path) -> None:
    config = default_config()
    config.providers["newsapi"].value = "demo-newsapi-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "news-events.json"
    cache_path.write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Original discovery evidence",
                        "summary": "Keep this row stable.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/original",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        return {
            "articles": [
                {
                    "publishedAt": "2026-07-06T10:00:00Z",
                    "title": "AI storage demand improves",
                    "description": "Cloud buyers increased storage orders.",
                    "url": "https://example.com/topic-news",
                }
            ]
        }

    result = pull_news_events(
        symbols=["STX"],
        query="AI storage demand",
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="newsapi",
        fetch_json=fetch_json,
        force=True,
    )

    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert [row["headline"] for row in cache["rows"]] == [
        "Original discovery evidence",
        "AI storage demand improves",
    ]


def test_pull_news_events_skips_fresh_cache(tmp_path: Path) -> None:
    config = default_config()
    config.providers["finnhub"].value = "demo-finnhub-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "news-events.json"
    cache_path.write_text(
        json.dumps(
            {
                "provider": "finnhub",
                "created_at": "2026-07-06T10:00:00+00:00",
                "warnings": [],
                "rows": [
                    {
                        "timestamp": "2026-07-06T09:30:00+00:00",
                        "headline": "Cached Apple news",
                        "summary": "Cached summary.",
                        "symbols": ["AAPL"],
                        "source_url": "https://example.com/cached-aapl-news",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    record_cache_entry(
        output_dir=tmp_path,
        layer="news",
        cache_key="news:finnhub:AAPL:2026-07-01:2026-07-03",
        provider="finnhub",
        artifact_path=cache_path,
        created_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        expires_at=datetime(2026, 7, 6, 11, 0, tzinfo=UTC),
        ttl_seconds=3600,
        row_count=1,
        market="mixed",
        session_state="ttl",
        is_final_for_session=False,
    )

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        raise AssertionError("fresh news cache should skip provider fetch")

    result = pull_news_events(
        symbols=["AAPL"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="finnhub",
        start_date="2026-07-01",
        end_date="2026-07-03",
        fetch_json=fetch_json,
        now=datetime(2026, 7, 6, 10, 30, tzinfo=UTC),
    )

    assert result.refreshed is False
    assert result.provider == "finnhub"
    assert result.count == 1
    assert "新闻缓存仍在保质期内" in result.warnings[0]


def test_pull_news_events_reports_zero_row_fresh_cache_without_global_count(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["newsapi"].value = "demo-newsapi-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "news-events.json"
    cache_path.write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "created_at": "2026-07-06T10:00:00+00:00",
                "warnings": [],
                "rows": [
                    {
                        "timestamp": "2026-07-06T09:30:00+00:00",
                        "headline": "Broad market news",
                        "summary": "This cache row does not cover Alibaba.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/market-news",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    record_cache_entry(
        output_dir=tmp_path,
        layer="news",
        cache_key=(
            "news:auto:BABA:2026-07-01:2026-07-03:"
            "AI cloud revenue Alibaba data center"
        ),
        provider="newsapi",
        artifact_path=cache_path,
        created_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        expires_at=datetime(2026, 7, 6, 11, 0, tzinfo=UTC),
        ttl_seconds=3600,
        row_count=0,
        market="US",
        session_state="ttl",
        is_final_for_session=False,
    )
    fetched: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        fetched.append(url)
        return {
            "articles": [
                {
                    "publishedAt": "2026-07-06T10:05:00Z",
                    "title": "Alibaba AI cloud revenue improves",
                    "description": "Data center demand supports Alibaba cloud growth.",
                    "url": "https://example.com/baba-ai-cloud",
                }
            ]
        }

    result = pull_news_events(
        symbols=["BABA"],
        query="AI cloud revenue Alibaba data center",
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        start_date="2026-07-01",
        end_date="2026-07-03",
        fetch_json=fetch_json,
        now=datetime(2026, 7, 6, 10, 30, tzinfo=UTC),
    )

    assert fetched == []
    assert result.refreshed is False
    assert result.count == 0
    assert "新闻缓存记录为空" in result.warnings[0]


def test_pull_news_events_refreshes_when_fresh_cache_misses_symbol(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["newsapi"].value = "demo-newsapi-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = data_dir / "news-events.json"
    cache_path.write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "created_at": "2026-07-06T10:00:00+00:00",
                "warnings": [],
                "rows": [
                    {
                        "timestamp": "2026-07-06T09:30:00+00:00",
                        "headline": "Cached Apple news",
                        "summary": "This row covers Apple, not Alibaba.",
                        "symbols": ["AAPL"],
                        "source_url": "https://example.com/aapl-news",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    record_cache_entry(
        output_dir=tmp_path,
        layer="news",
        cache_key="news:auto:BABA:2026-07-01:2026-07-03:AI cloud",
        provider="newsapi",
        artifact_path=cache_path,
        created_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        expires_at=datetime(2026, 7, 6, 11, 0, tzinfo=UTC),
        ttl_seconds=3600,
        row_count=1,
        market="US",
        session_state="ttl",
        is_final_for_session=False,
    )
    fetched: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        fetched.append(url)
        return {
            "articles": [
                {
                    "publishedAt": "2026-07-06T10:05:00Z",
                    "title": "Alibaba AI cloud update",
                    "description": "Alibaba cloud demand remains in focus.",
                    "url": "https://example.com/baba-cloud",
                }
            ]
        }

    result = pull_news_events(
        symbols=["BABA"],
        query="AI cloud",
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        start_date="2026-07-01",
        end_date="2026-07-03",
        fetch_json=fetch_json,
        now=datetime(2026, 7, 6, 10, 30, tzinfo=UTC),
    )

    assert fetched
    assert result.refreshed is True
    assert result.count == 1


def test_auto_news_provider_falls_back_when_first_configured_provider_fails(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["marketaux"].value = "demo-marketaux-key"
    config.providers["finnhub"].value = "demo-finnhub-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        if "marketaux" in url:
            raise RuntimeError(
                "Could not fetch JSON from https://api.marketaux.com?api_token=***: "
                "HTTP Error 403: Forbidden"
            )
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
    assert result.warnings == [
        "Marketaux 被拒绝访问（HTTP 403）；请检查 API Key、套餐权限或地区限制，"
        "正在尝试下一个已配置新闻数据源"
    ]


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
        assert "@" in headers["User-Agent"]
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
