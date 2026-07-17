import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from lychee_alphadesk.core.portfolio import check_portfolio, load_portfolio_targets


def _write_policy(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "base_currency": "USD",
                "live_trading": False,
                "risk_limits": {
                    "min_cash_weight": 0.30,
                    "max_single_asset_weight": 0.25,
                    "max_experimental_weight": 0.00,
                },
                "blocked_products": [
                    "margin",
                    "options",
                    "futures",
                    "leveraged_etf",
                    "crypto",
                ],
                "decision_requires": [
                    "data_quality_check",
                    "source_links",
                    "counterargument",
                    "human_approval",
                ],
            }
        ),
        encoding="utf-8",
    )


def test_check_portfolio_reports_policy_and_price_coverage(tmp_path: Path) -> None:
    portfolio = tmp_path / "portfolio.csv"
    portfolio.write_text(
        "symbol,name,quantity,target_weight,asset_type\n"
        "CASH,USD Cash,3000,0.40,cash\n"
        "QQQ,Invesco QQQ Trust,2,0.20,etf\n"
        "SPY,SPDR S&P 500 ETF Trust,2,0.20,etf\n"
        "2800.HK,Tracker Fund of Hong Kong,100,0.20,etf\n",
        encoding="utf-8",
    )
    policy = tmp_path / "policy.yaml"
    _write_policy(policy)

    result = check_portfolio(
        portfolio_path=portfolio,
        policy_path=policy,
        output_dir=tmp_path,
    )

    assert result.ok
    assert result.status_label == "政策通过，等待行情"
    assert result.total_target_weight == 1
    assert result.cash_target_weight == 0.4
    assert result.missing_price_symbols == ["QQQ", "SPY", "2800.HK"]


def test_check_portfolio_rejects_weight_and_blocked_product(tmp_path: Path) -> None:
    portfolio = tmp_path / "portfolio.csv"
    portfolio.write_text(
        "symbol,name,quantity,target_weight,asset_type\n"
        "CASH,USD Cash,3000,0.10,cash\n"
        "QQQ,Invesco QQQ Trust,2,0.70,leveraged_etf\n",
        encoding="utf-8",
    )
    policy = tmp_path / "policy.yaml"
    _write_policy(policy)

    result = check_portfolio(portfolio_path=portfolio, policy_path=policy)

    assert not result.ok
    assert any("低于政策最低值" in error for error in result.errors)
    assert any("超过单项上限" in error for error in result.errors)
    assert any("禁止的产品类型" in error for error in result.errors)


def test_check_portfolio_surfaces_foreign_currency_without_fx_assumption(
    tmp_path: Path,
) -> None:
    portfolio = tmp_path / "portfolio.csv"
    portfolio.write_text(
        "symbol,name,quantity,target_weight,asset_type,currency\n"
        "CASH,USD Cash,3000,0.40,cash,USD\n"
        "AAPL,Apple,2,0.20,stock,USD\n"
        "2800.HK,Tracker Fund,100,0.20,etf,HKD\n"
        "510300.SH,CSI 300 ETF,100,0.20,etf,CNY\n",
        encoding="utf-8",
    )
    policy = tmp_path / "policy.yaml"
    _write_policy(policy)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "test",
                "rows": [
                    {
                        "symbol": "AAPL",
                        "date": "2026-07-16",
                        "close": 1,
                        "volume": 1,
                        "currency": "USD",
                    },
                    {
                        "symbol": "2800.HK",
                        "date": "2026-07-16",
                        "close": 1,
                        "volume": 1,
                        "currency": "HKD",
                    },
                    {
                        "symbol": "510300.SH",
                        "date": "2026-07-16",
                        "close": 1,
                        "volume": 1,
                        "currency": "CNY",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = check_portfolio(
        portfolio_path=portfolio,
        policy_path=policy,
        output_dir=tmp_path,
    )

    assert result.ok
    assert result.status_label == "政策通过，等待 FX"
    assert result.currencies == ["CNY", "HKD", "USD"]
    assert result.foreign_currency_symbols == ["2800.HK", "510300.SH"]
    assert any("缺少 FX provider" in warning for warning in result.warnings)

    (data_dir / "fx-rates.json").write_text(
        json.dumps(
            {
                "retrieved_at": "2026-07-17T10:00:00+00:00",
                "rows": [
                    {
                        "base_currency": "USD",
                        "quote_currency": "HKD",
                        "rate": 7.8,
                        "as_of": "2026-07-16",
                        "provider": "ecb",
                        "source_url": "https://example.com/fx",
                    },
                    {
                        "base_currency": "USD",
                        "quote_currency": "CNY",
                        "rate": 7.1,
                        "as_of": "2026-07-16",
                        "provider": "ecb",
                        "source_url": "https://example.com/fx",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    cached_result = check_portfolio(
        portfolio_path=portfolio,
        policy_path=policy,
        output_dir=tmp_path,
        now=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    assert cached_result.status_label == "政策通过，已生成估值快照"
    assert cached_result.missing_fx_currencies == []
    assert {item.symbol for item in cached_result.valuations} == {
        "CASH",
        "AAPL",
        "2800.HK",
        "510300.SH",
    }
    assert all(item.value_base > 0 for item in cached_result.valuations)


def test_load_portfolio_targets_requires_stable_columns(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.csv"
    path.write_text("symbol,name\nQQQ,QQQ\n", encoding="utf-8")

    try:
        load_portfolio_targets(path)
    except ValueError as error:
        assert "缺少字段" in str(error)
    else:
        raise AssertionError("缺少组合字段时必须失败")
