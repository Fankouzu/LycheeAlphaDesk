# Broker Import and Acceptance Guide

Lychee AlphaDesk treats broker integration as **read-only import**. It does not log in to a broker, submit orders, or turn a statement into an investment conclusion.

## IBKR export path

In IBKR Client Portal, open **Performance & Reports -> Statements**, create a custom Activity Statement, select CSV output, and keep details visible. Prefer these sections:

- Positions: account, symbol, description, quantity, period-end price, and market value.
- Trades: account, symbol, trade date, quantity, price, commission, and tax/fee.
- Client Fees: account fee details.
- Corporate Actions: action date, description, quantity, value, code, and related result.
- Cash Report: cash balances by currency.

Official references: [Trades fields](https://www.ibkrguides.com/reportingreference/reportguide/et_trades.htm), [Positions fields](https://www.ibkrguides.com/reportingreference/reportguide/et_positions.htm), [Corporate Actions fields](https://www.ibkrguides.com/reportingreference/reportguide/corporateactions_default.htm), and [Custom Statement creation](https://www.ibkrguides.com/adminportal/performanceandstatements/createcustom.htm).

## Normalized files

IBKR column names and statement sections can vary with the report template. Copy verified fields into the normalized CSV schema instead of passing a raw Activity Statement directly.

### Positions

```csv
symbol,name,quantity,avg_cost,currency,asset_type,as_of,fees_paid,taxes_paid,corporate_action_note,account_id
QQQ,Invesco QQQ Trust,2,450,USD,etf,2026-07-16,0.8,0,Split and dividend records checked,U1234567
```

Required: `symbol,name,quantity,avg_cost,currency,asset_type,as_of`

Recommended: `account_id,fees_paid,taxes_paid,corporate_action_note`

### Transactions and corporate actions

```csv
transaction_id,symbol,trade_date,side,quantity,price,currency,fees,taxes,corporate_action,account_id
exec-001,QQQ,2026-07-16,buy,2,450,USD,0.8,0,,U1234567
div-001,QQQ,2026-07-17,dividend,2,1.5,USD,0,0,Dividend notice checked,U1234567
```

Required: `transaction_id,symbol,trade_date,side,quantity,price,currency`

Optional: `account_id,fees,taxes,corporate_action`

`transaction_id` must come from a verifiable execution, trade, or Flex Query identifier. Do not fabricate a stable ID from the CSV row number. If the identifier cannot be verified, keep an audit gap instead of guessing.

Supported sides: `buy`, `sell`, `dividend`, `interest`, `fee`, `tax`, `split`, `merger`, and `spinoff`.

## Import and acceptance

```bash
lychee portfolio import --file ibkr-positions.csv --source ibkr_csv
lychee portfolio import-transactions --file ibkr-transactions.csv --source ibkr_csv
lychee portfolio check --file portfolio.csv --policy policy.yaml \
  --positions .alphadesk/data/portfolio-positions.json
lychee research check
lychee research next
```

Acceptance requires checking record counts, account identities, and audit gaps against the original statement; confirming that the workbench shows both position and transaction audit context; and confirming that the next-action queue explains any remaining gaps. The system does not infer tax, realized P&L, or trading actions.

## Multi-account rules

- Every row should carry `account_id`; missing identity becomes an audit gap.
- The same symbol across accounts is merged by quantity for read-only valuation and retains a manual cross-account reconciliation gap.
- Different currencies, corporate actions, or cost conventions are not treated as automatically reconciled.
- Keep the original IBKR statement in a secure local location; normalized CSV is a research input only.

## Explicit non-goals

- No automatic IBKR login or Flex API session.
- No tax cost, tax filing amount, or realized-P&L calculation.
- No automatic tax-lot matching, FIFO/LIFO, wash-sale, withholding, or post-action tax-basis logic.
- No orders, rebalance instructions, or return forecasts from imported data.
