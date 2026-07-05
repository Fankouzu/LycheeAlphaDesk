# Lychee AlphaDesk 开发规格

版本：v0.1 草案

[English](DEVELOPMENT_SPEC.md) | [简体中文](DEVELOPMENT_SPEC.zh-CN.md)

## 1. 产品方向

Lychee AlphaDesk 是一个终端原生的 AI 投资研究工作台。

主产品不是 Web dashboard，而是一个快速、本地运行的 CLI/TUI 应用。Demo 模式不需要部署服务，不需要券商账户，也不需要付费 API key。

第一期实现必须证明这个工作流成立：

```text
投资政策 -> provider -> 市场发现 -> 数据质量 -> 每日报告 -> TUI 查看 -> 审计日志
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
- 覆盖美股、港股和 A 股的发现优先工作流。
- Markdown 每日报告。
- 本地审计日志。
- 最小 Textual TUI 外壳。

v0.1 不交付：

- Web 前端。
- FastAPI 服务。
- 实盘交易。
- 券商账户要求。
- 付费数据源要求。
- Demo 模式的真实 LLM 强依赖。
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
| 存储 | JSON 存可读快照，SQLite 存审计记录和研究队列，后续 Parquet 存时间序列 |
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
lad setup set alpha_vantage YOUR_API_KEY
lad setup llm set https://api.example.com/v1 YOUR_API_KEY MODEL_NAME
lychee setup
lychee discover today
lad discover today --markets us,hk,cn
lad data health --demo
lad data snapshot --demo
lad data pull market --symbols AAPL,TSLA
lad data pull news --symbols AAPL --provider auto
lad data pull filings --symbols AAPL,TSLA --limit 3
lad data health
lad data snapshot
lad report --demo
lad policy check examples/demo/policy.yaml
lad research queue
lad audit list
lad
```

命令行为：

- `lad` 打开 TUI，主界面第一个动作必须是 `Today Discovery`，然后才是关注候选查看、数据健康检查、provider setup 指引、手动股票代码钻取、snapshot 和退出。
- `lad demo` 检查 demo 文件和本地输出目录。
- `lad setup` 打开统一交互式配置中心，数据 provider 和 LLM provider 都从这里配置。
- `lad setup set` 为自动化脚本和 agent 单项写入一个 provider key 或 token。
- `lad setup llm set` 为自动化脚本和 agent 单项写入一个 OpenAI-compatible LLM `base_url`、API key 和可选模型名。
- 交互式配置中心在 TTY 环境使用 Textual `OptionList` 控件和键盘导航。面向人的菜单必须使用 ↑/↓/←/→/Tab 移动选择，Enter 确认，Esc 返回或退出。菜单不得使用数字或字母作为选项选择操作，并且这条流程不得继续使用手写 raw-key parser。非 TTY 环境不得 fallback 到文本菜单，应使用非交互式 setup 命令。provider 菜单只显示展示名称和脱敏配置状态；进入 provider 后再显示注册链接和面向用户的配置说明。隐藏输入提交后会用 `✅` 或 `❌` 告诉用户是否收到内容。
- LLM 配置区会写入自定义 OpenAI-compatible `base_url`、API key 和模型名。它会先读取 `{base_url}/models`；如果接口不可用或没有返回可用模型 ID，就提示用户手动输入模型名。
- `lychee` 是推荐的 console command；`lad` 保留为短别名。
- `lad data health --demo` 打印 provider 级数据质量检查。
- `lad data snapshot --demo` 写入统一 JSON 快照，包含市场、新闻、公告和预测数据。
- `lychee discover today` 在不要求用户先输入股票代码的情况下，运行覆盖美股、港股和 A 股的发现优先流程。
- `lad discover today --markets us,hk,cn` 会以 `stream: true` 调用已配置的 OpenAI-compatible `/chat/completions` 接口，解析模型返回的 JSON，并写入本地 `llm-synthesized` discovery report cache，包含主题、关注候选、证据引用、warning 和下一步动作。如果没有配置 LLM provider、API 请求失败，或模型没有返回有效 JSON，命令必须失败；不允许静默生成 fallback 报告。成功后必须同步写入 `.alphadesk/research.sqlite3`，作为研究队列和证据追踪的本地数据库。默认 LLM 读超时为 180 秒。
- `lad data pull market` 将 Alpha Vantage 日线行情写入本地 live cache。默认使用行情 cache 的保质期和交易时段判断；`--force` 可强制刷新。
- `lad data pull news` 将 Marketaux、Finnhub 或 NewsAPI 新闻事件写入本地 live cache。
- `lad data pull filings` 将 SEC EDGAR 近期 filings 写入本地 live cache。
- `lad data health` 检查 live cache 是否存在以及行数状态。
- `lad data snapshot` 基于 live cache 写入统一 JSON 快照。
- TUI 主界面 Action 菜单必须先暴露发现优先流程，再暴露手动 symbol 流程。手动输入股票代码只作为已经知道关注对象后的钻取路径保留。Textual 内置 command palette 不是业务命令入口，并且应在主界面保持禁用，以避免终端 glyph 宽度显示问题。
- `lad report --demo` 使用内置 demo provider 生成 Markdown 日报。
- `lad policy check` 校验投资政策文件，并打印违反项或警告。
- `lad research queue` 列出 SQLite 研究库中的关注候选，包含状态、市场、代码、主题、证据数量和下一步动作数量。
- `lad audit list` 列出已生成的报告和决策记录。

## 数据新鲜度策略

本地 cache 必须有明确保质期。默认路径优先复用未过期数据，只有过期、缺失或用户显式传入 `--force` 时才刷新。

行情 cache 的保质期必须同时考虑数据类型和市场交易状态：

- 美股常规交易时段按 9:30-16:00 ET 处理。
- 港股常规交易时段按 9:30-12:00、13:00-16:00 HKT 处理。
- A 股常规交易时段按 9:30-11:30、13:00-15:00 CST 处理。
- 交易中默认 15 分钟保质期。
- 港股/A 股午休期间默认不刷新，等下午开盘后再判断。
- 收盘后允许做一次收盘确认刷新；确认后的行情 cache 冻结到下一个交易日开盘。
- 周末默认不刷新；第一版暂不内置完整节假日日历。
- `--force` 必须绕过保质期和交易时段判断。

cache 状态写入 `.alphadesk/research.sqlite3` 的 `cache_entries` 表，记录 layer、cache_key、provider、artifact_path、created_at、expires_at、ttl_seconds、market、session_state、row_count 和 is_final_for_session。

## 交互规范

这是整个项目的永久交互规则：

- 面向人的交互式界面必须 keyboard-navigation-first。
- 菜单和选项选择必须使用 ↑/↓/←/→/Tab 移动，Enter 确认，Esc 返回或退出。
- 菜单不得使用数字、字母或输入命令别名作为选项选择方式。
- 只有真实值才允许文本输入，例如 API key、URL、模型名、证券代码或文件路径。
- v0.1 面向人的 CLI/TUI 文案必须中文优先；内部 provider 名、命令参数、模型 ID、证券代码等机器标识可以保留原文。
- LLM、网络请求、数据拉取和报告生成等可能耗时的动作，必须在阻塞调用前显示 loading/等待状态；失败时必须给出可读错误，不得让用户误以为终端卡死。
- 面向自动化脚本和 coding agent 的非交互命令允许存在，但必须通过显式命令参数传值，而不是隐藏的菜单选择。
- 非 TTY 环境不得提供数字或字母式文本菜单 fallback。

## 6. TUI 范围

v0.1 的 TUI 应该小而有用。

页面：

- Today Discovery：美股/港股/A 股主题、有证据支撑的关注候选、风险提示和建议拉取的数据。
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
- 不要把输入股票代码作为新手进入产品的第一步。

## 6.1 今日市场发现引擎

产品入口必须是发现优先。

引擎先从广域证据出发，再逐步收敛到主题和候选标的：

```text
美股/港股/A 股市场概览 -> 广域新闻与事件 -> LLM 综合分析 -> 关注候选 -> 钻取详细数据
```

必须覆盖的市场：

| 市场 | 第一轮数据 | 预期输出 |
| --- | --- | --- |
| 美股 | 主要指数、ETF、财经新闻、SEC filings、大盘股观察池 | 主题、股票、ETF、行业候选 |
| 港股 | 恒生系列指数、港股市场新闻、HKEX 公告、港币/利率背景 | 主题、股票、中国相关行业、IPO/打新提示 |
| A 股 | 宽基指数、行业板块、公告、财报/业绩预告、IPO/打新提示 | 主题、A 股候选、政策相关行业 |

Discovery report 必须包含：

- 市场覆盖情况和缺失 provider 的 warning。
- 来源列表，包括 provider 名称和时间戳。
- 主题，包括摘要、证据、相关行业、风险提示和置信度。
- 关注候选，包括展示名称、可选股票代码、市场、资产类型、证据、风险提示和建议拉取的数据。
- 明确说明候选只是研究对象，不是买入/卖出建议。

LLM 可以负责摘要、聚类、提取、比较和建议下一步研究数据。LLM 不得输出直接买入/卖出结论、目标价、自动仓位配置或实盘交易指令。

如果没有配置 LLM provider、API 请求失败，或模型没有返回有效 JSON，命令必须失败并显示 setup/error 指引，而且不得写入 discovery cache。

## 7. Provider 接口

核心系统依赖接口，不依赖具体厂商 SDK。

Provider 类型：

- `MarketDataProvider`
- `NewsProvider`
- `FilingProvider`
- `MacroProvider`
- `DiscoveryProvider`
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
- LLM provider 对 demo/report 流程可选，但对 Today Discovery 必选。
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
