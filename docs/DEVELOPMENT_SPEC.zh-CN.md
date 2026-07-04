# Lychee AlphaDesk 开发规格

版本：v0.1 草案

[English](DEVELOPMENT_SPEC.md) | [简体中文](DEVELOPMENT_SPEC.zh-CN.md)

## 1. 产品方向

Lychee AlphaDesk 是一个终端原生的 AI 投资研究工作台。

主产品不是 Web dashboard，而是一个快速、本地运行的 CLI/TUI 应用。Demo 模式不需要部署服务，不需要券商账户，也不需要付费 API key。

第一期实现必须证明这个工作流成立：

```text
投资政策 -> provider -> 数据质量 -> 每日报告 -> TUI 查看 -> 审计日志
```

用户 clone 仓库后，应该能在五分钟内看到项目价值。

## 2. 第一期目标

v0.1 应交付：

- 可安装 Python package。
- `lad` 命令。
- 内置 demo 模式。
- 投资政策文件校验。
- Provider 接口。
- Demo providers。
- 数据质量检查。
- 统一数据快照命令。
- Provider 健康检查命令。
- 用于本机 provider 配置的 CLI setup 命令。
- Markdown 每日报告。
- 本地审计日志。
- 最小 Textual TUI 外壳。

v0.1 不交付：

- Web 前端。
- FastAPI 服务。
- 实盘交易。
- 券商账户要求。
- 付费数据源要求。
- 真实 LLM 强依赖。
- 真实 TimesFM 强依赖。

## 3. 技术栈

| 层 | 决策 |
| --- | --- |
| 语言 | Python 3.11+ |
| 包管理 | uv |
| CLI | Typer |
| 终端 UI | Textual + Rich |
| 数据模型和配置 | Pydantic v2 + YAML |
| 表格和终端渲染 | Rich |
| 报告 | Markdown + Jinja2 |
| 存储 | SQLite 存元数据，Parquet 存时间序列 |
| 测试 | pytest |
| 格式化和 lint | ruff |
| 类型检查 | mypy |
| 文档 | Markdown 优先，后续 MkDocs Material |

## 4. 包结构

```text
src/lychee_alphadesk/
  __init__.py
  cli/
    app.py
  tui/
    app.py
    screens/
  core/
    policy/
    providers/
    data_quality/
    reports/
    audit/
    portfolio/
  providers/
    demo/
    csv/
  templates/
examples/
  demo/
    alphadesk.yaml
    policy.yaml
    portfolio.csv
    market_data.csv
    news.jsonl
    filings.jsonl
docs/
tests/
```

## 5. CLI 命令

v0.1 必须支持：

```bash
lad demo
lad setup
lad setup wizard
lad setup providers
lad setup set alpha_vantage YOUR_API_KEY
lychee setup
lychee setup wizard
lad data health --demo
lad data snapshot --demo
lad report --demo
lad policy check examples/demo/policy.yaml
lad audit list
lad
```

命令行为：

- `lad` 打开 TUI。
- `lad demo` 检查 demo 文件和本地输出目录。
- `lad setup` 创建 `~/.config/lychee-alphadesk/config.yaml`，并打印 provider 注册指引。
- `lad setup wizard` 运行交互式 provider key 配置流程；TTY 环境使用上下箭头选择 provider，非 TTY 环境使用文本 fallback。TTY 主菜单只显示展示名称和脱敏配置状态；进入 provider 后再显示注册链接和面向用户的配置说明。隐藏输入提交后会用 `✅` 或 `❌` 告诉用户是否收到内容。FMP 这类高级 provider 可以继续出现在 `setup providers`，但默认 wizard 中隐藏。
- `lad setup providers` 列出 provider 注册地址和需要配置的值。
- `lad setup set` 把 provider key 或 token 写入本机配置文件。
- `lychee` 是推荐的 console command；`lad` 保留为短别名。
- `lad data health --demo` 打印 provider 级数据质量检查。
- `lad data snapshot --demo` 写入统一 JSON 快照，包含市场、新闻、公告和预测数据。
- `lad report --demo` 使用内置 demo provider 生成 Markdown 日报。
- `lad policy check` 校验投资政策文件，并打印违反项或警告。
- `lad audit list` 列出已生成的报告和决策记录。

## 6. TUI 范围

v0.1 的 TUI 应该小而有用。

页面：

- Today：每日结论、风险状态和不操作理由。
- Portfolio：模拟持仓、现金比例、目标偏离和政策违反项。
- News：demo 事件聚类和受影响资产。
- Forecasts：mock 预测区间和简单基准对比。
- Memos：生成的研究备忘录预览。
- Policy：已加载投资政策和校验结果。
- Providers：demo provider 健康状态。
- Audit：历史报告记录。

TUI 要求：

- 键盘优先导航。
- 本地快速启动。
- Demo 模式不需要网络。
- 清楚标记 demo 数据。
- 不提供实盘下单入口。

## 7. Provider 接口

核心系统依赖接口，不依赖具体厂商 SDK。

Provider 类型：

- `MarketDataProvider`
- `NewsProvider`
- `FilingProvider`
- `MacroProvider`
- `ForecastProvider`
- `LLMProvider`
- `BrokerProvider`
- `StorageProvider`

v0.1 providers：

- `DemoMarketDataProvider`
- `DemoNewsProvider`
- `DemoFilingProvider`
- `DemoForecastProvider`
- `DemoLLMProvider`
- `ManualCsvBrokerProvider`

真实 provider 应在接口稳定后再逐步加入。

插件规则：

- 真实 provider 必须作为可选 extras，而不是默认依赖。
- 券商集成在加入任何订单草稿流程之前，必须先保持只读。
- Provider 失败时必须返回结构化警告，并包含来源名称和时间戳。
- Demo 模式绝不能把 demo 数据和 live provider 数据静默混用。

## 8. 投资政策文件

政策文件格式：YAML。

最小字段：

```yaml
base_currency: USD
live_trading: false

risk_limits:
  min_cash_weight: 0.30
  max_single_asset_weight: 0.25
  max_experimental_weight: 0.00

blocked_products:
  - margin
  - options
  - futures
  - leveraged_etf
  - inverse_etf
  - crypto

decision_requires:
  - data_quality_check
  - source_links
  - counterargument
  - human_approval
```

校验输出必须包括：

- Errors：会阻止报告生成的无效配置。
- Warnings：有风险但可接受的配置。
- Passes：明确通过的规则。

## 9. 每日报告

v0.1 报告格式为 Markdown。

必需章节：

- 每日结论。
- 组合快照。
- 投资政策检查。
- 数据质量状态。
- 新闻和事件。
- 预测摘要。
- 反方审查。
- 不操作或行动理由。
- 审计元数据。

报告必须明确标记 demo 数据。

## 10. 审计日志

每次生成报告都应写入审计记录。

最小字段：

- 报告 id。
- 生成时间。
- 投资政策文件路径。
- Provider 名称。
- 输入快照 id。
- 输出报告路径。
- Demo 或 live 模式。
- 警告和错误。

存储：

- SQLite 存元数据。
- 本地文件存 Markdown 报告。

## 11. 安全默认值

v0.1 默认：

- 首次运行使用 demo 模式。
- 禁用实盘交易。
- Broker provider 可选。
- LLM provider 可选。
- TimesFM provider 可选。
- Provider key 存在用户配置目录，而不是项目级 `.env` 文件。
- 所有真实 provider 失败都必须降级为明确警告。
- 不允许从真实数据静默 fallback 到 demo 数据。

## 12. 开源默认值

仓库应该容易试用，也容易审计。

默认要求：

- 第一次成功运行不需要 API key。
- 第一次成功运行不需要券商账户。
- 第一次成功运行不需要后台服务。
- CLI 输出应说明报告和审计记录写到了哪里。
- 生成的 demo 报告必须包含清晰的非投资建议免责声明。
- 配置示例应优先采用保守、长期投资的假设。

## 13. 验收标准

v0.1 完成时，新用户应该可以：

1. clone 仓库。
2. 用 uv 安装 package。
3. 运行 `lad demo`。
4. 运行 `lad report --demo`。
5. 打开生成的 Markdown 报告。
6. 运行 `lad` 并浏览 TUI。
7. 理解系统是研究优先，而不是交易机器人。
8. 查看投资政策校验和审计记录。

## 14. 实现顺序

1. Python package 和 CLI 骨架。
2. Demo 数据文件。
3. Pydantic 配置和投资政策模型。
4. Provider 接口。
5. Demo providers。
6. 数据质量检查。
7. Markdown 报告模板。
8. 审计存储。
9. Textual TUI 外壳。
10. 测试和 quickstart 文档。
