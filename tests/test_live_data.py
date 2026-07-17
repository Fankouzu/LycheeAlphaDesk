import json
import sqlite3
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import list_cache_entries, record_cache_entry
from lychee_alphadesk.core.config import (
    NewsProviderPluginConfig,
    default_config,
    save_config,
)
from lychee_alphadesk.core.live_data import (
    _parse_sec_financial_snapshot,
    build_cached_data_snapshot,
    pull_market_breadth_metrics,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    pull_sec_financials,
    pull_volatility_metrics,
    read_research_metric_cache,
    run_cached_data_health,
    write_fund_metadata_cache,
    write_manual_filing_summary,
    write_manual_news_event,
    write_research_metric_cache,
)
from lychee_alphadesk.core.research_db import init_research_db
from lychee_alphadesk.providers.demo import NewsEvent
from lychee_alphadesk.providers.news_plugins import (
    NewsProviderMetadata,
    NewsProviderRegistry,
    NewsProviderRequest,
    NewsProviderSetting,
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


def test_write_manual_news_event_adds_auditable_source_to_news_cache(tmp_path: Path) -> None:
    result = write_manual_news_event(
        output_dir=tmp_path,
        symbol="0700.HK",
        headline="Tencent cloud revenue grows in Hong Kong market",
        summary=(
            "Tencent disclosed cloud growth in Hong Kong, which is relevant to the "
            "platform-company research question."
        ),
        source_url="https://example.com/tencent-cloud-source",
        published_at="2026-07-16T08:00:00+00:00",
    )

    assert result.domain == "news"
    assert result.provider == "manual"
    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["provider"] == "manual"
    assert cache["rows"] == [
        {
            "timestamp": "2026-07-16T08:00:00+00:00",
            "headline": "Tencent cloud revenue grows in Hong Kong market",
            "summary": (
                "Tencent disclosed cloud growth in Hong Kong, which is relevant to the "
                "platform-company research question."
            ),
            "symbols": ["0700.HK"],
            "source_url": "https://example.com/tencent-cloud-source",
            "is_symbol_scoped": True,
        }
    ]


def test_manual_filing_summary_is_auditable_and_survives_sec_refresh(
    tmp_path: Path,
) -> None:
    manual = write_manual_filing_summary(
        output_dir=tmp_path,
        symbol="NVDA",
        company="NVIDIA Corp.",
        form="4",
        date="2026-07-06",
        summary="已核验：该 Form 4 为内部人交易披露，未包含经营业绩更新。",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/manual-form4.html",
    )

    def fetch_json(url: str, _headers: dict[str, str] | None = None) -> object:
        if url.endswith("company_tickers.json"):
            return {"0": {"ticker": "NVDA", "cik_str": 1045810, "title": "NVIDIA Corp."}}
        return {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": ["2026-07-15"],
                    "accessionNumber": ["0001045810-26-000001"],
                    "primaryDocument": ["nvda8k.htm"],
                }
            }
        }

    refreshed = pull_sec_filings(
        symbols=["NVDA"],
        output_dir=tmp_path,
        fetch_json=fetch_json,
    )

    assert manual.provider == "manual"
    assert refreshed.provider == "sec_edgar"
    cache = json.loads((tmp_path / "data" / "filings.json").read_text(encoding="utf-8"))
    assert {row["form"] for row in cache["rows"]} == {"4", "8-K"}
    assert any(
        row["source_url"].endswith("manual-form4.html") for row in cache["rows"]
    )


def test_pull_market_prices_cools_down_empty_provider_result_until_forced(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["tushare"].value = "demo-tushare-token"
    config_path = save_config(config, tmp_path / "config.yaml")
    post_calls: list[str] = []

    def post_json(
        _url: str,
        _headers: dict[str, str] | None,
        payload: dict[str, object],
    ) -> object:
        post_calls.append(str(payload["api_name"]))
        return {
            "code": 40203,
            "msg": "抱歉，您没有接口(hk_daily)访问权限",
        }

    def fetch_json(_url: str, _headers: dict[str, str] | None = None) -> object:
        raise RuntimeError("fallback unavailable")

    first = pull_market_prices(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        post_json=post_json,
        fetch_json=fetch_json,
        now=datetime(2026, 7, 15, 10, 0, tzinfo=UTC),
    )
    second = pull_market_prices(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        post_json=post_json,
        fetch_json=fetch_json,
        now=datetime(2026, 7, 15, 10, 5, tzinfo=UTC),
    )
    forced = pull_market_prices(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        post_json=post_json,
        fetch_json=fetch_json,
        force=True,
        now=datetime(2026, 7, 15, 10, 10, tzinfo=UTC),
    )

    entries = list_cache_entries(tmp_path, layer="market")

    assert first.count == 0
    assert entries[0].status == "no_data"
    assert second.refreshed is False
    assert "保质期内跳过重试" in second.warnings[0]
    assert forced.refreshed is True
    assert post_calls == ["hk_daily", "hk_daily"]


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


def test_pull_market_breadth_metrics_uses_nasdaq_equal_weight_proxy(
    tmp_path: Path,
) -> None:
    def post_form_json(
        url: str,
        headers: dict[str, str] | None,
        payload: dict[str, str],
    ) -> object:
        assert url.endswith("/Index/HistoryData")
        index = payload["id"]
        start_value = {"NDX": 29502.60, "NDXE": 10043.11}[index]
        end_value = {"NDX": 29025.77, "NDXE": 10011.43}[index]
        values = [
            (f"/Date({1784088000000 + position * 86400000})/", start_value)
            for position in range(20)
        ] + [("/Date(1784174400000)/", end_value)]
        return {
            "iTotalRecords": len(values),
            "aaData": [
                {
                    "TradeDate": trade_date,
                    "Value": value,
                    "NetChange": 0,
                    "PctChange": None,
                }
                for trade_date, value in values
            ],
        }

    result = pull_market_breadth_metrics(
        symbols=["QQQ"],
        output_dir=tmp_path,
        post_form_json=post_form_json,
        force=True,
    )

    assert result.provider == "nasdaq_public"
    assert result.count == 3
    metrics = read_research_metric_cache(tmp_path)
    assert [metric.name for metric in metrics] == [
        "Nasdaq-100 市值加权 20 交易日变化",
        "Nasdaq-100 等权 20 交易日变化",
        "Nasdaq-100 等权相对市值加权差异",
    ]
    assert metrics[0].domain == "market_breadth"
    assert metrics[0].source_url == "https://indexes.nasdaq.com/Index/History/NDX"
    assert "市场扩散代理" in metrics[2].note


def test_pull_market_breadth_metrics_requires_supported_symbol(tmp_path: Path) -> None:
    try:
        pull_market_breadth_metrics(symbols=["AAPL"], output_dir=tmp_path)
    except ValueError as error:
        assert "QQQ" in str(error)
    else:
        raise AssertionError("unsupported symbol should fail clearly")


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


def test_pull_market_prices_auto_uses_configured_tushare_for_china_instruments(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["tushare"].value = "demo-tushare-token"
    config_path = save_config(config, tmp_path / "config.yaml")
    requests: list[dict[str, object]] = []

    def post_json(
        url: str,
        headers: dict[str, str] | None,
        payload: dict[str, object],
    ) -> object:
        assert url == "https://api.tushare.pro"
        assert headers == {"Content-Type": "application/json"}
        requests.append(payload)
        ts_code = str(dict(payload["params"])["ts_code"])
        return {
            "code": 0,
            "msg": "",
            "data": {
                "fields": [
                    "ts_code",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "vol",
                ],
                "items": [[ts_code, "20260715", 100.0, 121.0, 99.0, 120.0, 4567]],
            },
        }

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        raise AssertionError(f"unexpected fallback request: {url}")

    result = pull_market_prices(
        symbols=["600519.SH", "510300.SH", "0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
        post_json=post_json,
    )

    assert result.count == 3
    assert [request["api_name"] for request in requests] == [
        "daily",
        "fund_daily",
        "hk_daily",
    ]
    assert [dict(request["params"])["ts_code"] for request in requests] == [
        "600519.SH",
        "510300.SH",
        "00700.HK",
    ]
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert [row["symbol"] for row in cache["rows"]] == [
        "600519.SH",
        "510300.SH",
        "0700.HK",
    ]
    assert cache["rows"][0]["volume"] == 456700
    assert cache["rows"][2]["volume"] == 4567


def test_pull_market_prices_auto_falls_back_after_tushare_permission_denial(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["tushare"].value = "demo-tushare-token"
    config_path = save_config(config, tmp_path / "config.yaml")

    def post_json(
        url: str,
        headers: dict[str, str] | None,
        payload: dict[str, object],
    ) -> object:
        return {
            "code": 40203,
            "msg": "抱歉，您没有接口(daily)访问权限",
            "data": {"fields": [], "items": []},
        }

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert "push2his.eastmoney.com" in url
        assert headers is not None
        assert headers["User-Agent"].startswith("Mozilla/5.0")
        assert headers["Referer"] == "https://quote.eastmoney.com/"
        return {
            "data": {
                "klines": [
                    "2026-07-15,100.0,120.0,121.0,99.0,456700,0,0,0,0,0"
                ]
            }
        }

    result = pull_market_prices(
        symbols=["600519.SH"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
        post_json=post_json,
    )

    assert result.count == 1
    assert result.warnings == [
        "600519.SH Tushare 行情拉取失败: Tushare daily 接口权限不足（40203）: "
        "抱歉，您没有接口(daily)访问权限；已改用 Eastmoney"
    ]


def test_pull_market_prices_retries_eastmoney_without_headers_after_connection_failure(
    tmp_path: Path,
) -> None:
    eastmoney_headers: list[dict[str, str] | None] = []

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        if "push2his.eastmoney.com" in url:
            eastmoney_headers.append(headers)
            if headers is not None:
                raise RuntimeError("browser profile connection closed")
            return {
                "data": {
                    "klines": [
                        "2026-07-16,478.0,482.6,494.8,477.4,34417022,0,0,0,0,0"
                    ]
                }
            }
        raise RuntimeError("Yahoo unavailable")

    result = pull_market_prices(
        symbols=["0700.HK"],
        output_dir=tmp_path,
        provider_id="eastmoney",
        fetch_json=fetch_json,
    )

    assert result.count == 1
    assert result.warnings == []
    assert eastmoney_headers[0] is not None
    assert eastmoney_headers[1] is None


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


def test_pull_volatility_metrics_writes_cboe_vxn_evidence_for_qqq(
    tmp_path: Path,
) -> None:
    def fetch_text(url: str, _headers: dict[str, str] | None = None) -> str:
        assert url == "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv"
        end = datetime(2026, 7, 16, tzinfo=UTC)
        rows = ["DATE,OPEN,HIGH,LOW,CLOSE"]
        for index in range(260):
            current = end - timedelta(days=259 - index)
            close = 20 + index / 10
            rows.append(
                f"{current.strftime('%m/%d/%Y')},{close},{close},{close},{close}"
            )
        return "\n".join(rows)

    result = pull_volatility_metrics(
        symbols=["QQQ"],
        output_dir=tmp_path,
        fetch_text=fetch_text,
    )

    assert result.domain == "research_metric"
    assert result.provider == "cboe"
    assert result.count == 3
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    rows = cache["rows"]
    assert {row["name"] for row in rows} == {
        "Cboe VXN 收盘",
        "Cboe VXN 20 交易日变化",
        "Cboe VXN 近一年历史分位",
    }
    assert {row["as_of"] for row in rows} == {"2026-07-16"}
    assert {row["source_url"] for row in rows} == {
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv"
    }


def test_pull_volatility_metrics_reuses_fresh_cboe_cache_until_forced(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fetch_text(url: str, _headers: dict[str, str] | None = None) -> str:
        calls.append(url)
        end = datetime(2026, 7, 16, tzinfo=UTC)
        rows = ["DATE,OPEN,HIGH,LOW,CLOSE"]
        for index in range(260):
            current = end - timedelta(days=259 - index)
            close = 20 + index / 10
            rows.append(
                f"{current.strftime('%m/%d/%Y')},{close},{close},{close},{close}"
            )
        return "\n".join(rows)

    first = pull_volatility_metrics(
        symbols=["QQQ"],
        output_dir=tmp_path,
        fetch_text=fetch_text,
        now=datetime(2026, 7, 16, 10, 0, tzinfo=UTC),
    )
    second = pull_volatility_metrics(
        symbols=["QQQ"],
        output_dir=tmp_path,
        fetch_text=fetch_text,
        now=datetime(2026, 7, 16, 10, 5, tzinfo=UTC),
    )
    forced = pull_volatility_metrics(
        symbols=["QQQ"],
        output_dir=tmp_path,
        fetch_text=fetch_text,
        now=datetime(2026, 7, 16, 10, 10, tzinfo=UTC),
        force=True,
    )

    entries = list_cache_entries(tmp_path, layer="research_metrics")

    assert first.refreshed is True
    assert second.refreshed is False
    assert "研究指标缓存仍在保质期内" in second.warnings[0]
    assert forced.refreshed is True
    assert calls == [
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv",
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv",
    ]
    assert entries[0].ttl_seconds == 24 * 60 * 60


def test_auto_news_pull_prefers_configured_entity_news_plugin(tmp_path: Path) -> None:
    class AuditedEntityNewsPlugin:
        metadata = NewsProviderMetadata(
            provider_id="audited_entity",
            display_name="审计实体新闻",
            description="按公司实体提供可核验新闻。",
            capabilities=frozenset({"entity_news"}),
            settings=(
                NewsProviderSetting(
                    key="api_key",
                    label="API Key",
                    description="新闻源访问凭据。",
                ),
            ),
        )

        def pull_news(self, request: NewsProviderRequest) -> list[NewsEvent]:
            assert request.symbols == ("0700.HK",)
            assert request.settings == {"api_key": "plugin-key"}
            return [
                NewsEvent(
                    timestamp="2026-07-16T09:00:00+00:00",
                    headline="Tencent cloud disclosure",
                    summary="Issuer-level cloud disclosure for research verification.",
                    symbols=["0700.HK"],
                    source_url="https://issuer.example.com/tencent-cloud",
                    is_symbol_scoped=True,
                )
            ]

    config = default_config()
    config.provider_plugins["audited_entity"] = NewsProviderPluginConfig(
        settings={"api_key": "plugin-key"}
    )
    config_path = save_config(config, tmp_path / "config.yaml")
    registry = NewsProviderRegistry(
        providers={"audited_entity": AuditedEntityNewsPlugin()},
        diagnostics=(),
    )

    result = pull_news_events(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        news_provider_registry=registry,
    )

    assert result.provider == "audited_entity"
    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["provider"] == "audited_entity"
    assert cache["rows"][0]["source_url"] == "https://issuer.example.com/tencent-cloud"


def test_tencent_official_news_provider_preserves_source_and_entity_link(
    tmp_path: Path,
) -> None:
    html = """
    <html><body>
      <article>
        <h2>Tencent Cloud expands AI services</h2>
        <a rel="bookmark" href="https://www.tencent.com/tencent-cloud-ai/">
          Tencent Cloud expands AI services
        </a>
        <div class="tc-blogpost-date">July 16, 2026</div>
      </article>
      <article>
        <h2>Old Tencent story</h2>
        <a rel="bookmark" href="https://www.tencent.com/old-story/">Old Tencent story</a>
        <div class="tc-blogpost-date">June 1, 2026</div>
      </article>
    </body></html>
    """

    def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
        assert url == "https://www.tencent.com/newsroom/"
        return html

    result = pull_news_events(
        symbols=["0700.HK"],
        output_dir=tmp_path,
        provider_id="tencent_official",
        start_date="2026-07-10",
        end_date="2026-07-16",
        fetch_text=fetch_text,
    )

    assert result.provider == "tencent_official"
    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"] == [
        {
            "timestamp": "2026-07-16T00:00:00+08:00",
            "headline": "Tencent Cloud expands AI services",
            "summary": "Tencent 官方 Newsroom 公司新闻。",
            "symbols": ["0700.HK"],
            "source_url": "https://www.tencent.com/tencent-cloud-ai/",
            "is_symbol_scoped": True,
        }
    ]


def test_auto_news_pull_falls_back_after_unexpected_entity_plugin_error(
    tmp_path: Path,
) -> None:
    class FailingEntityNewsPlugin:
        metadata = NewsProviderMetadata(
            provider_id="failing_entity",
            display_name="故障实体新闻",
            description="用于验证插件故障隔离。",
            capabilities=frozenset({"entity_news"}),
            settings=(
                NewsProviderSetting(
                    key="token",
                    label="访问令牌",
                    description="新闻源访问凭据。",
                ),
            ),
        )

        def pull_news(self, request: NewsProviderRequest) -> list[NewsEvent]:
            raise OSError(f"network error for {request.settings['token']}")

    config = default_config()
    config.provider_plugins["failing_entity"] = NewsProviderPluginConfig(
        settings={"token": "plugin-secret"}
    )
    config_path = save_config(config, tmp_path / "config.yaml")
    registry = NewsProviderRegistry(
        providers={"failing_entity": FailingEntityNewsPlugin()},
        diagnostics=(),
    )

    def fetch_json(url: str, _headers: dict[str, str] | None = None) -> object:
        assert "api.gdeltproject.org" in url
        return {
            "articles": [
                {
                    "url": "https://example.com/tencent-news",
                    "title": "Tencent issuer news fallback",
                    "seendate": "20260716T084500Z",
                }
            ]
        }

    result = pull_news_events(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
        news_provider_registry=registry,
    )

    assert result.provider == "gdelt"
    assert "故障实体新闻 请求失败" in result.warnings[0]
    assert "plugin-secret" not in result.warnings[0]


def test_pull_news_events_writes_gdelt_entity_mapped_cache(tmp_path: Path) -> None:
    config_path = save_config(default_config(), tmp_path / "config.yaml")
    seen_urls: list[str] = []

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        seen_urls.append(url)
        assert "api.gdeltproject.org/api/v2/doc/doc" in url
        assert "Tencent" in url
        return {
            "articles": [
                {
                    "url": "https://example.com/tencent-ai",
                    "title": "Tencent expands AI cloud services",
                    "seendate": "20260716T084500Z",
                    "domain": "example.com",
                    "language": "English",
                    "sourcecountry": "Hong Kong",
                }
            ]
        }

    result = pull_news_events(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="gdelt",
        start_date="2026-07-10",
        end_date="2026-07-16",
        fetch_json=fetch_json,
    )

    assert result.provider == "gdelt"
    assert result.count == 1
    assert seen_urls
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"][0]["symbols"] == ["0700.HK"]
    assert cache["rows"][0]["headline"] == "Tencent expands AI cloud services"
    assert cache["rows"][0]["timestamp"] == "2026-07-16T08:45:00+00:00"


def test_pull_news_events_uses_company_query_for_unknown_cn_symbol(
    tmp_path: Path,
) -> None:
    config_path = save_config(default_config(), tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        decoded_url = urllib.parse.unquote_plus(url)
        assert "api.gdeltproject.org/api/v2/doc/doc" in url
        assert "平安银行" in decoded_url
        assert "000001.SZ" not in decoded_url
        return {
            "articles": [
                {
                    "url": "https://example.com/pingan-bank",
                    "title": "平安银行发布公司公告",
                    "seendate": "20260716T084500Z",
                }
            ]
        }

    result = pull_news_events(
        symbols=["000001.SZ"],
        query="平安银行",
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="gdelt",
        fetch_json=fetch_json,
    )

    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"][0]["symbols"] == ["000001.SZ"]


def test_auto_news_provider_uses_gdelt_after_configured_provider_denials(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["marketaux"].value = "demo-marketaux-key"
    config.providers["finnhub"].value = "demo-finnhub-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        if "marketaux" in url or "finnhub" in url:
            raise RuntimeError(f"Could not fetch JSON from {url}: HTTP Error 403: Forbidden")
        assert "api.gdeltproject.org" in url
        return {
            "articles": [
                {
                    "url": "https://example.com/tencent-ai",
                    "title": "Tencent AI update",
                    "seendate": "20260716T084500Z",
                }
            ]
        }

    result = pull_news_events(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
    )

    assert result.provider == "gdelt"
    assert result.count == 1
    assert len(result.warnings) == 2
    assert all("被拒绝访问" in warning for warning in result.warnings)


def test_auto_news_provider_uses_gdelt_when_configured_source_has_no_rows(
    tmp_path: Path,
) -> None:
    config = default_config()
    config.providers["newsapi"].value = "demo-newsapi-key"
    config_path = save_config(config, tmp_path / "config.yaml")

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        if "newsapi.org" in url:
            return {"articles": []}
        assert "api.gdeltproject.org" in url
        return {
            "articles": [
                {
                    "url": "https://example.com/tencent-ai",
                    "title": "Tencent AI update",
                    "seendate": "20260716T084500Z",
                }
            ]
        }

    result = pull_news_events(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="auto",
        fetch_json=fetch_json,
    )

    assert result.provider == "gdelt"
    assert result.count == 1
    assert any("NewsAPI 没有返回匹配新闻" in warning for warning in result.warnings)


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


def test_pull_news_events_upgrades_existing_row_with_symbol_scope(tmp_path: Path) -> None:
    config = default_config()
    config.providers["newsapi"].value = "demo-newsapi-key"
    config_path = save_config(config, tmp_path / "config.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "legacy-newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-06T10:00:00Z",
                        "headline": "Tencent expands AI cloud services",
                        "summary": "Tencent cloud expansion.",
                        "symbols": ["0700.HK"],
                        "source_url": "https://example.com/tencent-ai",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert "newsapi.org" in url
        return {
            "articles": [
                {
                    "publishedAt": "2026-07-06T10:00:00Z",
                    "title": "Tencent expands AI cloud services",
                    "description": "Tencent cloud expansion.",
                    "url": "https://example.com/tencent-ai",
                }
            ]
        }

    result = pull_news_events(
        symbols=["0700.HK"],
        config_path=config_path,
        output_dir=tmp_path,
        provider_id="newsapi",
        start_date="2026-07-01",
        end_date="2026-07-07",
        fetch_json=fetch_json,
        force=True,
    )

    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert result.count == 1
    assert cache["rows"] == [
        {
            "timestamp": "2026-07-06T10:00:00Z",
            "headline": "Tencent expands AI cloud services",
            "summary": "Tencent cloud expansion.",
            "symbols": ["0700.HK"],
            "source_url": "https://example.com/tencent-ai",
            "is_symbol_scoped": True,
        }
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


def test_pull_sec_filings_writes_hkex_announcements_for_hk_symbols(tmp_path: Path) -> None:
    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert url.endswith("activestock_sehk_e.json")
        return [{"i": 7609, "c": "00700", "n": "TENCENT", "s": 15487}]

    def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
        assert "titlesearch.xhtml" in url
        assert "stockId=7609" in url
        return """
        <table><tbody>
          <tr>
            <td class="release-time">Release Time: 09/07/2026 17:58</td>
            <td class="stock-short-name">Stock Short Name: TENCENT<br/>TENCENT-R</td>
            <td>
              <div class="headline">Next Day Disclosure Returns - [Share Buyback]<br/></div>
              <div class="doc-link">
                <a href="/listedco/listconews/sehk/2026/0709/2026070900827.pdf">
                  Next Day Disclosure Return
                </a>
              </div>
            </td>
          </tr>
          <tr>
            <td class="release-time">Release Time: 13/05/2026 16:31</td>
            <td class="stock-short-name">Stock Short Name: TENCENT</td>
            <td>
              <div class="headline">
                ANNOUNCEMENT OF THE RESULTS FOR THE THREE MONTHS ENDED 31 MARCH 2026
              </div>
              <div class="doc-link">
                <a href="/listedco/listconews/sehk/2026/0513/2026051300999.pdf">
                  Quarterly Results
                </a>
              </div>
            </td>
          </tr>
        </tbody></table>
        """

    result = pull_sec_filings(
        symbols=["0700.HK"],
        output_dir=tmp_path,
        fetch_json=fetch_json,
        fetch_text=fetch_text,
        limit_per_symbol=1,
    )

    assert result.provider == "hkexnews"
    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"] == [
        {
            "date": "2026-07-09",
            "company": "TENCENT",
            "form": "HKEX 公告",
            "summary": "HKEXnews 公告: Next Day Disclosure Returns - [Share Buyback]",
            "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0709/2026070900827.pdf",
            "symbol": "0700.HK",
        }
    ]


def test_pull_sec_filings_writes_cninfo_announcements_for_cn_symbols(tmp_path: Path) -> None:
    def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
        assert headers and "LycheeAlphaDesk" in headers["User-Agent"]
        assert url.endswith("szse_stock.json")
        return {
            "stockList": [
                {
                    "code": "000001",
                    "orgId": "gssz0000001",
                    "zwjc": "平安银行",
                }
            ]
        }

    def post_form_json(
        url: str,
        headers: dict[str, str] | None,
        payload: dict[str, str],
    ) -> object:
        assert url.endswith("hisAnnouncement/query")
        assert headers and headers["X-Requested-With"] == "XMLHttpRequest"
        assert payload["column"] == "szse"
        assert payload["stock"] == "000001,gssz0000001"
        return {
            "announcements": [
                {
                    "announcementTitle": "董事会决议公告",
                    "announcementTime": 1783008000000,
                    "adjunctUrl": "finalpage/2026-07-03/1225406051.PDF",
                    "secCode": "000001",
                    "secName": "平安银行",
                }
            ]
        }

    result = pull_sec_filings(
        symbols=["000001.SZ"],
        output_dir=tmp_path,
        fetch_json=fetch_json,
        post_form_json=post_form_json,
        limit_per_symbol=1,
    )

    assert result.provider == "cninfo"
    assert result.count == 1
    cache = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert cache["rows"] == [
        {
            "date": "2026-07-03",
            "company": "平安银行",
            "form": "巨潮公告",
            "summary": "巨潮资讯公告: 董事会决议公告",
            "source_url": "https://static.cninfo.com.cn/finalpage/2026-07-03/1225406051.PDF",
            "symbol": "000001.SZ",
        }
    ]


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


def test_cached_data_health_surfaces_provider_warnings_with_cached_rows(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-16T08:45:00+00:00",
                        "headline": "Unrelated provider output",
                        "summary": "Retained only for audit.",
                        "symbols": ["000001.SZ"],
                        "source_url": "https://example.com/unrelated",
                    }
                ],
                "warnings": ["Marketaux 被拒绝访问（HTTP 403）"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    checks = {check.name: check for check in run_cached_data_health(tmp_path)}

    assert checks["news-cache-present"].status == "warning"
    assert checks["news-cache-present"].provider == "newsapi"
    assert "已加载 1 条新闻事件" in checks["news-cache-present"].message
    assert "Marketaux 被拒绝访问" in checks["news-cache-present"].message


def test_cached_data_health_reports_market_coverage_and_tushare_entitlement_gap(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "auto",
                "created_at": "2026-07-15T19:26:28+00:00",
                "warnings": [
                    "0700.HK Tushare 行情拉取失败: Tushare hk_daily "
                    "接口权限不足（40203）: 没有接口(hk_daily)访问权限"
                ],
                "rows": [
                    {
                        "symbol": "AAPL",
                        "date": "2026-07-15",
                        "close": 210.0,
                        "volume": 100,
                        "currency": "USD",
                    },
                    {
                        "symbol": "510300.SH",
                        "date": "2026-07-14",
                        "close": 4.837,
                        "volume": 1808440038,
                        "currency": "CNY",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    checks = {check.name: check for check in run_cached_data_health(tmp_path)}

    assert checks["market-us-coverage"].status == "pass"
    assert checks["market-cn-coverage"].status == "pass"
    assert checks["market-hk-coverage"].status == "warning"
    assert checks["market-hk-coverage"].provider == "Tushare Pro"
    assert "hk_daily" in checks["market-hk-coverage"].message


def test_cached_data_health_does_not_apply_hk_entitlement_to_us_coverage(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "auto",
                "warnings": [
                    "0700.HK Tushare 行情拉取失败: Tushare hk_daily "
                    "接口权限不足（40203）: 没有接口(hk_daily)访问权限"
                ],
                "rows": [],
            }
        ),
        encoding="utf-8",
    )

    checks = {check.name: check for check in run_cached_data_health(tmp_path)}

    assert checks["market-us-coverage"].provider == "auto"
    assert "hk_daily" not in checks["market-us-coverage"].message
