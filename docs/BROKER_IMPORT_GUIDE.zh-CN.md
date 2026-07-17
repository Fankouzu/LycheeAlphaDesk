# 券商导入与验收指南

Lychee AlphaDesk 的券商接入默认是**只读导入**。它不登录券商、不提交订单，也不把券商报表直接当成投资结论。

## IBKR 导出入口

建议在 IBKR Client Portal 的 **Performance & Reports -> Statements** 中创建自定义 Activity Statement，选择 CSV 输出，并保留明细。优先包含：

- Positions：账户、代码、描述、数量、期末价格和市值。
- Trades：账户、代码、交易日期、数量、价格、佣金、税费/费用。
- Client Fees：账户费用明细。
- Corporate Actions：公司行动日期、描述、数量、价值、代码和相关结果。
- Cash Report：各币种现金余额。

官方字段说明：

- [IBKR Trades 字段说明](https://www.ibkrguides.com/reportingreference/reportguide/et_trades.htm)
- [IBKR Positions 字段说明](https://www.ibkrguides.com/reportingreference/reportguide/et_positions.htm)
- [IBKR Corporate Actions 字段说明](https://www.ibkrguides.com/reportingreference/reportguide/corporateactions_default.htm)
- [IBKR 自定义 Statement 指南](https://www.ibkrguides.com/adminportal/performanceandstatements/createcustom.htm)

## 标准化文件

IBKR 原始 CSV 的列名和分节结构可能随报表模板变化。不要直接把原始 Activity Statement 当成标准输入，先复制需要的字段为以下两个 CSV。

### 持仓文件

文件名可自定，例如 `ibkr-positions.csv`：

```csv
symbol,name,quantity,avg_cost,currency,asset_type,as_of,fees_paid,taxes_paid,corporate_action_note,account_id
QQQ,Invesco QQQ Trust,2,450,USD,etf,2026-07-16,0.8,0,已核对分拆与分红记录,U1234567
```

必填：`symbol,name,quantity,avg_cost,currency,asset_type,as_of`

建议填写：`account_id,fees_paid,taxes_paid,corporate_action_note`

### 交易与公司行动文件

文件名可自定，例如 `ibkr-transactions.csv`：

```csv
transaction_id,symbol,trade_date,side,quantity,price,currency,fees,taxes,corporate_action,account_id
exec-001,QQQ,2026-07-16,buy,2,450,USD,0.8,0,,U1234567
div-001,QQQ,2026-07-17,dividend,2,1.5,USD,0,0,已核对股息公告,U1234567
```

必填：`transaction_id,symbol,trade_date,side,quantity,price,currency`

可选：`account_id,fees,taxes,corporate_action`

`transaction_id` 必须来自可核验的执行编号、交易编号或 Flex Query 字段。不要只用 CSV 行号伪造稳定 ID。无法确认编号时，保留人工缺口，不要让系统猜测。

支持的 `side`：`buy`、`sell`、`dividend`、`interest`、`fee`、`tax`、`split`、`merger`、`spinoff`。

## 导入与验收

```bash
lychee portfolio import \
  --file ibkr-positions.csv \
  --source ibkr_csv

lychee portfolio import-transactions \
  --file ibkr-transactions.csv \
  --source ibkr_csv

lychee portfolio check \
  --file portfolio.csv \
  --policy policy.yaml \
  --positions .alphadesk/data/portfolio-positions.json

lychee research check
lychee research next
```

验收顺序：

1. 导入输出中的账户数量、记录数量和审计缺口与原始报表核对。
2. `portfolio check` 必须明确显示行情、FX、持仓覆盖和费用/公司行动缺口。
3. `research check` 的“组合风险上下文”必须显示持仓审计和流水审计状态。
4. `research next` 中的“组合审计”或“流水审计”必须能解释下一步，不得出现未知状态。
5. 只有在人工核对报表总额后，才把数据当作研究上下文；系统不会把它变成税务、已实现盈亏或交易结论。

## 多账户规则

- 每一行都应带 `account_id`；没有账户标识会形成审计缺口。
- 同一代码来自多个账户时，系统会按数量合并用于只读估值，并记录跨账户人工核对缺口。
- 不同币种、不同公司行动或不同成本口径不能被视为已自动对账。
- 原始 IBKR 报表必须保留在本地安全位置，标准化 CSV 只用于研究导入。

## 当前明确不支持

- 自动读取 IBKR 登录会话或 Flex API。
- 自动计算税务成本、税务申报金额或已实现盈亏。
- 自动匹配税务批次、FIFO/LIFO、洗售规则、股息预扣税和公司行动后的税务成本。
- 根据导入结果生成订单、调仓建议或收益预测。
