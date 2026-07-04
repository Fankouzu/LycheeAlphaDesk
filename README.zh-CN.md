# Lychee AlphaDesk

[English](README.md) | [简体中文](README.zh-CN.md)

![GitHub Repo stars](https://img.shields.io/github/stars/Fankouzu/LycheeAlphaDesk?style=social)
![CI](https://github.com/Fankouzu/LycheeAlphaDesk/actions/workflows/ci.yml/badge.svg)
![Status](https://img.shields.io/badge/status-runnable%20demo-059669)
![Broker Agnostic](https://img.shields.io/badge/broker--agnostic-yes-2563eb)
![Policy First](https://img.shields.io/badge/policy--first-yes-059669)
![Not a Trading Bot](https://img.shields.io/badge/not%20a%20trading%20bot-human%20approved-f59e0b)
![License](https://img.shields.io/badge/license-TBD-lightgrey)

面向长期投资者的终端原生、政策优先 AI 投资研究工作台。

Lychee AlphaDesk 是一个开源终端投研工作台，目标是把市场数据、财报、新闻、宏观指标、时间序列预测和 LLM 分析整合到一个证据优先的投资研究流程里。

它以本地命令行和 TUI 应用运行，反应快，不需要复杂部署。它不是交易机器人，也不提供投资建议。它的目标是帮助投资者在任何人工操作之前，先完成研究、记录、审查和复盘。

> 终端原生。研究优先。政策优先。券商无关。人工确认。

## 🚀 快速开始

```bash
git clone https://github.com/Fankouzu/LycheeAlphaDesk.git
cd LycheeAlphaDesk
uv tool install .
lychee setup
lychee data health --demo
lychee data snapshot --demo
lychee policy check examples/demo/policy.yaml
lychee report --demo
lychee audit list
```

生成的数据快照会写入 `.alphadesk/data-snapshot-demo.json`。生成的 demo 报告会写入 `.alphadesk/daily-report-demo.md`。

如果已经安装过工具，`git pull` 更新仓库后请刷新本地 CLI 包：

```bash
uv tool install . --force --reinstall-package lychee-alphadesk
```

如果只是本地开发，不想全局安装：

```bash
uv sync --all-groups --no-editable
uv run --no-editable lychee data health --demo
```

`lad` 会继续作为短别名保留，但推荐使用 `lychee`。

## ✨ 为什么做这个项目

很多 AI 投资工具从预测或交易信号开始。Lychee AlphaDesk 从投资政策开始。

在系统给出研究、再平衡或订单草稿之前，必须先检查：

- 哪些资产允许投资？
- 可以承受多少风险？
- 数据是否新鲜并且可追溯？
- 支持结论的证据是什么？
- 最强反方观点是什么？
- 正确答案是否应该是“不操作”？

这个项目的目标是帮助长期投资者建立纪律，而不是鼓励过度交易。

## 🧭 核心理念

- **政策优先**：投资规则优先级高于模型输出。
- **证据优先**：每个结论都应引用数据、财报、新闻，或明确标记为推断。
- **券商无关**：IBKR、Futu、Longbridge、Tiger、CSV 导入、paper broker 都只是可选插件。
- **数据源无关**：市场数据、新闻、财报、宏观、LLM、预测模型都通过可插拔 provider 接入。
- **终端原生**：主产品是本地 CLI/TUI 工作台，而不是 Web dashboard。
- **人工确认**：MVP 阶段不做自动实盘执行。
- **欢迎不操作**：证据不足时，系统应明确输出“不操作”。

## ⚡ 目标终端体验

主界面是终端。下面是 v0.1 目标体验：

```bash
lychee demo
lychee report --demo
lychee
```

计划中的 TUI 页面：

- Today：每日结论、风险状态和不操作理由。
- Portfolio：现金、模拟持仓、配置偏离和投资政策违反项。
- News：事件聚类、受影响资产和来源时间戳。
- Forecasts：TimesFM 或 mock 预测区间，并与基准模型比较。
- Memos：投资研究备忘录和反方审查。
- Policy：投资政策规则和校验结果。
- Providers：数据源健康度和插件状态。
- Audit：历史报告、数据快照和决策日志。

## 📡 数据引擎

第一期数据引擎重点是先让数据可见、可审计，然后再接入真实 provider 插件。

```bash
uv run --no-editable lad data health --demo
uv run --no-editable lad data snapshot --demo
```

当前 demo snapshot 聚合：

- 市场价格和成交量。
- 新闻事件。
- 财报和公告摘要。
- Mock 预测区间。
- Provider 级数据质量检查。

## 🏗️ 计划中的引擎结构

```mermaid
flowchart LR
  Policy[投资政策] --> Risk[组合风控引擎]
  Market[市场数据] --> Data[数据治理]
  News[新闻与事件] --> Data
  Filings[财报与公告] --> Data
  Macro[宏观与利率] --> Data
  Data --> Forecast[预测层]
  Data --> Committee[LLM 投委会]
  Forecast --> Decision[决策引擎]
  Committee --> Decision
  Risk --> Decision
  Decision --> Memo[投资备忘录]
  Decision --> Report[每日驾驶舱报告]
```

## 🧩 计划模块

| 模块 | 作用 |
| --- | --- |
| 投资政策引擎 | 定义允许产品、风险上限、现金规则、禁止产品和人工审批要求。 |
| 数据治理 | 统一 ticker、币种、时区、分红、拆股、过期数据和来源时间戳。 |
| 市场数据 Provider | 获取日线/周线价格、成交量、分红、拆股和指数数据。 |
| 新闻与事件引擎 | 对新闻去重、聚类，并映射到公司、行业、宏观和地缘事件。 |
| 财报与公告 | 分析 SEC 文件、HKEX 公告、招股书和财务报表。 |
| 预测层 | 使用 TimesFM 和简单基准模型输出预测区间，不直接生成交易信号。 |
| LLM 投委会 | 运行分析员、宏观、风险官、反方审稿人、投资秘书等角色。 |
| 决策引擎 | 输出不操作、需要研究、风险警报、再平衡或人工订单草稿。 |
| 审计日志 | 保存来源链接、数据快照、prompt 版本、模型输出和决策记录。 |

## 🔌 Provider 架构

Lychee AlphaDesk 围绕 provider 接口设计。

| Provider 类型 | 示例 |
| --- | --- |
| MarketDataProvider | yfinance、AkShare、Tushare、本地 CSV |
| NewsProvider | GDELT、Finnhub、FMP、Alpha Vantage |
| FilingProvider | SEC EDGAR、HKEXnews、巨潮资讯 |
| MacroProvider | FRED、HKMA、US Treasury FiscalData |
| ForecastProvider | TimesFM、统计基准模型 |
| LLMProvider | OpenAI、Claude、Gemini、Qwen、DeepSeek、本地模型 |
| BrokerProvider | mock broker、paper broker、CSV/manual、IBKR、Futu、Longbridge、Tiger |
| StorageProvider | SQLite、DuckDB、Postgres、Parquet |

开源 MVP 必须在没有券商账户、没有付费 API key 的情况下运行。

## 🔑 CLI Setup 与 Provider Key

下一阶段应接入真实 provider，但任何 provider 都不应成为必选项。默认 demo 流程必须继续支持离线运行。

Lychee AlphaDesk 是命令行工具，所以 provider key 应通过 CLI 写入本机配置，而不是在项目目录里维护 `.env`。默认配置文件位置：

```text
~/.config/lychee-alphadesk/config.yaml
```

使用 `lad setup` 创建配置文件并查看 provider 注册地址：

```bash
lychee setup
lychee setup wizard
lychee setup providers
lychee setup set alpha_vantage "YOUR_API_KEY"
```

setup 命令只显示配置文件路径和下一步命令。使用 `lychee setup providers` 查看 provider 注册地址和需要回填的值。在真实终端里，wizard 使用上下箭头菜单：用 ↑/↓ 选择 provider，回车查看详情，按 `q` 结束。主菜单只显示展示名称和脱敏后的配置状态；注册链接只会在进入某个 provider 后显示。隐藏输入提交后会用 `✅` 或 `❌` 告诉用户是否收到内容。

建议优先接入：

| 优先级 | Provider ID | Provider | 数据范围 | 是否需要注册 | Setup 值 | 地址 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `yfinance` | yfinance | 美股/港股/全球日线行情 | 不需要正式注册 | 无 | [GitHub](https://github.com/ranaroussi/yfinance) | 适合开发和研究 demo；这是非官方 Yahoo Finance 接入，不应视为生产级或可再分发授权数据。 |
| 1 | `akshare` | AkShare | A 股、港股/美股、宏观等公开数据 | 通常不需要 API key | 无 | [GitHub](https://github.com/akfamily/akshare) | 中国市场覆盖的第一优先开源选择；接口稳定性受上游网站影响。 |
| 1 | `gdelt` | GDELT | 全球新闻和事件 | 不需要 API key | 无 | [GDELT data/API](https://www.gdeltproject.org/data.html) | 适合作为第一版新闻源，但需要后续做去重、ticker/entity 映射。 |
| 1 | `sec_edgar` | SEC EDGAR | 美国上市公司公告和 XBRL | 不需要 API key | 无 | [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | 美国财报/公告必接；本地 CLI 流程不需要用户配置。 |
| 1 | `hkma` | HKMA Open API | 香港宏观和金融统计 | 不需要注册 | 无 | [HKMA Open API](https://apidocs.hkma.gov.hk/) | 适合补充港币利率、银行、金融市场背景。 |
| 2 | `tushare` | Tushare Pro | A 股行情、财务、交易日历 | 需要账号和 token | token | [Tushare token 指南](https://tushare.pro/document/1?doc_id=39) | 比爬取更结构化，但部分数据可能需要积分/权限。 |
| 2 | `alpha_vantage` | Alpha Vantage | 全球行情、基本面、技术指标、宏观 | 免费 API key | API key | [申请 API key](https://www.alphavantage.co/support/#api-key) | 适合初学者；免费档有频率限制。 |
| 2 | `finnhub` | Finnhub | 行情、基本面、公告、新闻 | 免费 API key | API key | [注册](https://finnhub.io/register) / [文档](https://finnhub.io/docs/api) | 适合 ticker 级新闻和公司数据。 |
| 2 | `fmp` | FMP | 行情、基本面、财务报表、新闻稿 | 需要 API key | API key | [注册](https://site.financialmodelingprep.com/register) | 高级可选 provider；因为注册路径对新手不够清晰，默认 wizard 中隐藏。 |
| 2 | `fred` | FRED | 美国宏观数据 | 免费 API key | API key | [FRED API](https://fred.stlouisfed.org/docs/api/fred/) | 美国宏观数据第一优先。 |
| 2 | `marketaux` | Marketaux | 金融新闻和情绪 | 免费 API key | API key | [文档](https://www.marketaux.com/documentation) | 如果 GDELT 的 ticker 匹配太噪，可作为金融新闻增强源。 |
| 2 | `newsapi` | NewsAPI | 通用新闻 | 开发阶段免费 API key | API key | [文档](https://newsapi.org/docs) | 可补充通用新闻，但要检查套餐限制和商业用途限制。 |

官方或授权数据路线：

| Provider | 数据范围 | 注册/申请 | 地址 | 备注 |
| --- | --- | --- | --- | --- |
| HKEXnews | 港股上市公司公告 | 网站搜索无需账号 | [HKEXnews](https://www.hkexnews.hk/) | 适合作为第一版港股公告源；但它不是稳定开发者 API，抓取/搜索行为要谨慎。 |
| 巨潮资讯 / CNINFO | 中国上市公司公告 | 公开网站可查；数据服务 API 可能需要申请 | [巨潮资讯](https://www.cninfo.com.cn/) / [CNINFO Data Service](https://webapi.cninfo.com.cn/) | 可先做公开公告发现；企业级接口可能需要单独服务条款。 |
| HKEX Market Data Services | 港交所官方行情 | 需要付费/授权申请 | [HKEX 获取行情](https://www.hkex.com.hk/Global/Exchange/FAQ/Market-Data/Getting-Market-Data?sc_lang=en) | 只有当免费/开放 provider 不稳定，或涉及再分发/商业用途时才需要。 |

不要把 provider 密钥提交进仓库，也不要把真实 key 粘贴到示例、issue、日志或截图中。

实现顺序：

1. 先接无需 key 的 provider：yfinance、AkShare、GDELT、SEC EDGAR、HKMA。
2. 再把需要 key 的 provider 放到 optional extras 和 health checks 后面。
3. 付费/授权数据只作为可选插件加入。
4. 每个 provider 都必须输出同一套 `DataSnapshot` 结构，并记录来源时间戳、provider 名称和 warning。

## 🧱 技术栈

| 层 | 选择 |
| --- | --- |
| 语言 | Python 3.11+ |
| 包管理 | uv |
| CLI | Typer |
| 终端 UI | Textual + Rich |
| 配置 | YAML + Pydantic v2 |
| 本地存储 | SQLite + Parquet，后续可加 DuckDB |
| 报告 | Markdown + Jinja2 |
| 测试 | pytest |
| 代码质量 | ruff + mypy |
| 文档 | 后续 MkDocs Material |

MVP 不需要 Web server。

## 📜 投资政策示例

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

## 🎯 MVP 范围

第一个公开版本聚焦研究，不聚焦执行。它应该在没有券商账户、LLM key、TimesFM 权重、付费行情数据的情况下也有价值。

v0.1 核心范围：

- Demo 模式，包含模拟组合、模拟新闻和样例报告。
- 本地投资政策文件。
- 终端原生 TUI 外壳。
- 小型 ETF 和股票观察池。
- Markdown 每日驾驶舱报告。
- 本地审计留痕。

v0.1 之后的插件：

- 来自免费或开放 provider 的市场和宏观数据。
- 新闻和事件聚类。
- SEC 财报分析。
- TimesFM 预测区间，并与简单基准模型比较。
- 带反方审查的 LLM 投资研究备忘录。
- 只读券商连接器，用于组合导入和对账。

MVP 不做：

- 自动实盘交易。
- 高频数据或 tick 级工作流。
- 保证金、期权、期货和杠杆产品。
- 付费交易所行情订阅。
- 投资建议或收益承诺。

## 🛠️ 项目状态

Lychee AlphaDesk 当前处于可运行 demo 启动阶段。

第一个里程碑是一个 demo-first 的本地研究流程，不需要券商账户即可运行。当前代码库已经包含初始 `lad` CLI、内置 demo 数据、数据快照、provider 健康检查、投资政策校验、Markdown 报告生成、审计记录、测试和 CI。

## 🗺️ 路线图

| 版本 | 目标 |
| --- | --- |
| v0.1 | Demo 数据、投资政策文件、本地存储、Markdown 每日报告、最小 TUI 外壳。 |
| v0.2 | 市场、宏观、新闻、财报 provider 和 provider 健康状态页面。 |
| v0.3 | TimesFM 预测和 LLM 投委会。 |
| v0.4 | 组合导入、对账和只读 broker plugin。 |
| v1.0 | 稳定插件 API、文档、示例、测试和安全默认值。 |

## 📚 开发规格

第一期架构和实现范围见 [docs/DEVELOPMENT_SPEC.zh-CN.md](docs/DEVELOPMENT_SPEC.zh-CN.md)，英文版见 [docs/DEVELOPMENT_SPEC.md](docs/DEVELOPMENT_SPEC.md)。

## 🛡️ 安全与免责声明

Lychee AlphaDesk 仅用于研究、教育和个人工作流自动化。

它不是投资建议、法律建议、税务建议或会计建议。市场存在风险。AI 模型可能出错。数据可能过期、不完整或错误。任何真实投资决策都必须由人类审查和确认。

## 📄 License

License 会在第一个实现版本发布前确定。
