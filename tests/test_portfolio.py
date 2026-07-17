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


def test_load_portfolio_targets_requires_stable_columns(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.csv"
    path.write_text("symbol,name\nQQQ,QQQ\n", encoding="utf-8")

    try:
        load_portfolio_targets(path)
    except ValueError as error:
        assert "缺少字段" in str(error)
    else:
        raise AssertionError("缺少组合字段时必须失败")
