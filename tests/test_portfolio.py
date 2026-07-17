import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from lychee_alphadesk.core.portfolio import (
    check_portfolio,
    import_portfolio_positions,
    import_portfolio_transactions,
    load_imported_positions,
    load_portfolio_targets,
    write_portfolio_check_artifact,
)


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


def test_import_portfolio_positions_preserves_source_and_audit_gaps(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broker-export.csv"
    path.write_text(
        "symbol,name,quantity,avg_cost,currency,asset_type,as_of,fees_paid,taxes_paid,corporate_action_note,account_id\n"
        "2800.HK,Tracker Fund,100,22.5,HKD,etf,2026-07-16,,,,account-1\n",
        encoding="utf-8",
    )

    result = import_portfolio_positions(
        positions_path=path,
        output_dir=tmp_path,
        source="ibkr_csv",
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )

    assert result.source == "ibkr_csv"
    assert result.positions[0].avg_cost == 22.5
    assert result.positions[0].fees_paid is None
    assert len(result.audit_gaps) == 3
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["source"] == "ibkr_csv"
    assert payload["rows"][0]["symbol"] == "2800.HK"
    assert payload["rows"][0]["account_id"] == "account-1"
    assert result.audit_path.exists()


def test_load_imported_positions_merges_same_symbol_across_accounts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "positions.json"
    path.write_text(
        json.dumps(
            {
                "source": "ibkr_csv",
                "rows": [
                    {
                        "symbol": "QQQ",
                        "name": "Invesco QQQ Trust",
                        "quantity": 1,
                        "avg_cost": 400,
                        "currency": "USD",
                        "asset_type": "etf",
                        "as_of": "2026-07-16",
                        "fees_paid": 1,
                        "taxes_paid": 0,
                        "corporate_action_note": "已核对",
                        "account_id": "account-a",
                    },
                    {
                        "symbol": "QQQ",
                        "name": "Invesco QQQ Trust",
                        "quantity": 2,
                        "avg_cost": 430,
                        "currency": "USD",
                        "asset_type": "etf",
                        "as_of": "2026-07-17",
                        "fees_paid": 2,
                        "taxes_paid": 0,
                        "corporate_action_note": "已核对",
                        "account_id": "account-b",
                    },
                ],
                "audit_gaps": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    source, positions, audit_gaps = load_imported_positions(path)

    assert source == "ibkr_csv"
    assert positions["QQQ"].quantity == 3
    assert positions["QQQ"].avg_cost == 420
    assert positions["QQQ"].fees_paid == 3
    assert positions["QQQ"].account_id == "account-a;account-b"
    assert any("多个账户" in gap for gap in audit_gaps)


def test_import_portfolio_transactions_preserves_action_audit_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "transactions.csv"
    path.write_text(
        "transaction_id,symbol,trade_date,side,quantity,price,currency,fees,taxes,corporate_action,account_id\n"
        "t-1,QQQ,2026-07-16,buy,2,450,USD,1.2,0,,account-a\n"
        "t-2,QQQ,2026-07-17,dividend,2,1.5,USD,,,已核对股息公告,account-a\n",
        encoding="utf-8",
    )

    result = import_portfolio_transactions(
        transactions_path=path,
        output_dir=tmp_path,
        source="ibkr_csv",
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )

    assert result.source == "ibkr_csv"
    assert len(result.transactions) == 2
    assert result.transactions[1].side == "dividend"
    assert "交易流水没有完整提供费用。" in result.audit_gaps
    assert "交易流水没有完整提供税费。" in result.audit_gaps
    assert "股息或公司行动流水没有核对说明" not in result.audit_gaps
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["rows"][1]["corporate_action"] == "已核对股息公告"
    assert result.audit_path.exists()


def test_check_portfolio_uses_imported_quantity_for_read_only_valuation(
    tmp_path: Path,
) -> None:
    portfolio = tmp_path / "portfolio.csv"
    portfolio.write_text(
        "symbol,name,quantity,target_weight,asset_type,currency\n"
        "CASH,USD Cash,50,0.75,cash,USD\n"
        "AAPL,Apple,1,0.25,stock,USD\n",
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
                        "close": 100,
                        "volume": 1,
                        "currency": "USD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    positions_csv = tmp_path / "broker-export.csv"
    positions_csv.write_text(
        "symbol,name,quantity,avg_cost,currency,asset_type,as_of\n"
        "CASH,USD Cash,50,1,USD,cash,2026-07-16\n"
        "AAPL,Apple,3,90,USD,stock,2026-07-16\n",
        encoding="utf-8",
    )
    imported = import_portfolio_positions(
        positions_path=positions_csv,
        output_dir=tmp_path,
        source="ibkr_csv",
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )

    result = check_portfolio(
        portfolio_path=portfolio,
        policy_path=policy,
        output_dir=tmp_path,
        positions_path=imported.output_path,
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )

    assert result.status_label == "政策通过，已生成估值快照"
    aapl = next(item for item in result.valuations if item.symbol == "AAPL")
    assert aapl.value_base == 300
    assert aapl.actual_weight == 300 / 350
    assert aapl.avg_cost == 90
    assert aapl.cost_basis_base == 270
    assert aapl.unrealized_pnl_base == 30
    assert aapl.fees_paid is None
    assert aapl.taxes_paid is None
    assert result.position_source == "ibkr_csv"


def test_write_portfolio_check_artifact_persists_beginner_status(tmp_path: Path) -> None:
    portfolio = tmp_path / "portfolio.csv"
    portfolio.write_text(
        "symbol,name,quantity,target_weight,asset_type\n"
        "CASH,USD Cash,100,1.0,cash\n",
        encoding="utf-8",
    )
    policy = tmp_path / "policy.yaml"
    _write_policy(policy)

    result = check_portfolio(
        portfolio_path=portfolio,
        policy_path=policy,
        output_dir=tmp_path,
    )
    artifact = write_portfolio_check_artifact(result, tmp_path)
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    assert payload["status_label"] == result.status_label
