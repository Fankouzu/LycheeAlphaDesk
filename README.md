# Lychee AlphaDesk

[English](README.md) | [简体中文](README.zh-CN.md)

![GitHub Repo stars](https://img.shields.io/github/stars/Fankouzu/LycheeAlphaDesk?style=social)
![CI](https://github.com/Fankouzu/LycheeAlphaDesk/actions/workflows/ci.yml/badge.svg)
![Status](https://img.shields.io/badge/status-runnable%20demo-059669)
![Broker Agnostic](https://img.shields.io/badge/broker--agnostic-yes-2563eb)
![Policy First](https://img.shields.io/badge/policy--first-yes-059669)
![Not a Trading Bot](https://img.shields.io/badge/not%20a%20trading%20bot-human%20approved-f59e0b)
![License](https://img.shields.io/badge/license-TBD-lightgrey)

Terminal-native, policy-first AI investment research workbench for long-term investors.

Lychee AlphaDesk is an open-source terminal investment desk that combines market data, filings, news, macro signals, time-series forecasting, and LLM-based analysis into an evidence-first workflow.

It runs locally as a fast command-line and TUI application. It is not a trading bot. It does not provide financial advice. It is designed to help investors research, document, and review decisions before any manual action.

> Terminal-native. Research-first. Policy-first. Broker-agnostic. Human-approved.

## 🚀 Quickstart

```bash
git clone https://github.com/Fankouzu/LycheeAlphaDesk.git
cd LycheeAlphaDesk
uv sync --all-groups --no-editable
uv run --no-editable lad demo
uv run --no-editable lad data health --demo
uv run --no-editable lad data snapshot --demo
uv run --no-editable lad policy check examples/demo/policy.yaml
uv run --no-editable lad report --demo
uv run --no-editable lad audit list
```

The generated data snapshot is written to `.alphadesk/data-snapshot-demo.json`. The generated demo report is written to `.alphadesk/daily-report-demo.md`.

## ✨ Why This Exists

Most AI investing tools start with predictions or trading signals. Lychee AlphaDesk starts with investment policy.

Before the system can suggest research, rebalancing, or an order draft, it must check:

- What assets are allowed?
- How much risk is acceptable?
- Is the data fresh and traceable?
- What evidence supports the conclusion?
- What is the strongest counterargument?
- Should the correct answer be "do nothing"?

The goal is to help long-term investors build discipline, not to encourage overtrading.

## 🧭 Core Ideas

- **Policy-first**: investment rules override model output.
- **Evidence-first**: every conclusion should cite data, filings, news, or explicit inference.
- **Broker-agnostic**: IBKR, Futu, Longbridge, Tiger, CSV imports, and paper brokers are optional plugins.
- **Provider-agnostic**: market data, news, filings, macro data, LLMs, and forecasts use pluggable providers.
- **Terminal-native**: the main product is a local CLI/TUI workspace, not a web dashboard.
- **Human-approved**: live execution is out of scope for the MVP.
- **No-action friendly**: the system should say "no action" when evidence is weak.

## ⚡ Target Terminal Experience

The primary interface is the terminal. These commands describe the v0.1 target experience:

```bash
lad demo
lad report --demo
lad
```

Planned TUI sections:

- Today: daily conclusion, risk status, and no-action reasoning.
- Portfolio: cash, mock positions, allocation drift, and policy violations.
- News: clustered events with affected assets and source timestamps.
- Forecasts: TimesFM or mock forecast intervals compared with baselines.
- Memos: investment research memos and skeptic reviews.
- Policy: investment policy rules and validation results.
- Providers: data source health and plugin status.
- Audit: saved reports, data snapshots, and decision logs.

## 📡 Data Engine

The first data engine milestone focuses on making data visible and auditable before adding real provider plugins.

```bash
uv run --no-editable lad data health --demo
uv run --no-editable lad data snapshot --demo
```

The demo snapshot currently aggregates:

- Market prices and volume.
- News events.
- Filing and announcement summaries.
- Mock forecast intervals.
- Provider-level quality checks.

## 🏗️ Planned Engine

```mermaid
flowchart LR
  Policy[Investment Policy] --> Risk[Portfolio Risk Engine]
  Market[Market Data] --> Data[Data Governance]
  News[News and Events] --> Data
  Filings[Filings and Financials] --> Data
  Macro[Macro and Rates] --> Data
  Data --> Forecast[Forecast Layer]
  Data --> Committee[LLM Investment Committee]
  Forecast --> Decision[Decision Engine]
  Committee --> Decision
  Risk --> Decision
  Decision --> Memo[Investment Memo]
  Decision --> Report[Daily Cockpit Report]
```

## 🧩 Planned Modules

| Module | Purpose |
| --- | --- |
| Investment Policy Engine | Defines allowed products, risk limits, cash rules, blocked instruments, and manual approval requirements. |
| Data Governance | Normalizes tickers, currencies, time zones, dividends, splits, stale data, and source timestamps. |
| Market Data Providers | Fetches daily/weekly prices, volume, dividends, splits, and index data. |
| News and Event Engine | Deduplicates and clusters news into company, sector, macro, and geopolitical events. |
| Filings and Financials | Reads SEC filings, HKEX announcements, prospectuses, and financial statements. |
| Forecast Layer | Uses TimesFM and simple baselines for forecast intervals, not direct trade signals. |
| LLM Investment Committee | Runs analyst, macro, risk, skeptic, and secretary roles with source-backed outputs. |
| Decision Engine | Produces no-action, research-required, risk-alert, rebalance, or manual order-draft outputs. |
| Audit Log | Stores source links, data snapshots, prompt versions, model outputs, and decision records. |

## 🔌 Provider Architecture

Lychee AlphaDesk is designed around provider interfaces.

| Provider Type | Examples |
| --- | --- |
| MarketDataProvider | yfinance, AkShare, Tushare, local CSV |
| NewsProvider | GDELT, Finnhub, FMP, Alpha Vantage |
| FilingProvider | SEC EDGAR, HKEXnews, CNINFO |
| MacroProvider | FRED, HKMA, US Treasury FiscalData |
| ForecastProvider | TimesFM, statistical baselines |
| LLMProvider | OpenAI, Claude, Gemini, Qwen, DeepSeek, local models |
| BrokerProvider | mock broker, paper broker, CSV/manual, IBKR, Futu, Longbridge, Tiger |
| StorageProvider | SQLite, DuckDB, Postgres, Parquet |

The open-source MVP must run without a broker account or paid API key.

## 🔑 Provider Setup

The next development milestone should add real providers without making any of them mandatory. The default demo flow must keep working offline.

Recommended first integrations:

| Priority | Provider | Domain | Registration | Address | Suggested env var | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | yfinance | US/HK/global daily prices | No formal signup | [GitHub](https://github.com/ranaroussi/yfinance) | none | Good for development and research demos; unofficial Yahoo Finance access, so do not treat it as production-grade or licensed redistribution data. |
| 1 | AkShare | China A-shares, HK/US data, macro datasets | Usually no API key | [GitHub](https://github.com/akfamily/akshare) | none | Best first open-source option for China-market coverage; interfaces may change with upstream sites. |
| 1 | GDELT | Global news and events | No API key | [GDELT data/API](https://www.gdeltproject.org/data.html) | none | Good first news provider because it is open and global, but needs downstream deduplication and ticker/entity mapping. |
| 1 | SEC EDGAR | US filings and XBRL facts | No API key | [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | `LAD_SEC_USER_AGENT` | Required for US company filings; use a responsible User-Agent and follow SEC fair-access guidance. |
| 1 | HKMA Open API | HK macro and financial statistics | No registration | [HKMA Open API](https://apidocs.hkma.gov.hk/) | none | Useful for HK macro/rates context. |
| 2 | Tushare Pro | China A-share prices, fundamentals, calendars | Account + token | [Tushare token guide](https://tushare.pro/document/1?doc_id=39) | `LAD_TUSHARE_TOKEN` | Better structured China data than scraping, but some datasets may require points/permissions. |
| 2 | Alpha Vantage | Global prices, fundamentals, indicators, macro | Free API key | [Get API key](https://www.alphavantage.co/support/#api-key) | `LAD_ALPHA_VANTAGE_API_KEY` | Good beginner-friendly API; free tier is rate-limited. |
| 2 | Finnhub | Market data, fundamentals, filings, news | Free API key | [Register](https://finnhub.io/register) / [Docs](https://finnhub.io/docs/api) | `LAD_FINNHUB_API_KEY` | Useful for ticker-linked market news and company data. |
| 2 | FMP | Prices, fundamentals, statements, press releases | API key | [Docs](https://site.financialmodelingprep.com/developer/docs) | `LAD_FMP_API_KEY` | Strong candidate for financial statements, but usage/licensing should be checked before redistribution. |
| 2 | FRED | US macro data | Free API key | [FRED API](https://fred.stlouisfed.org/docs/api/fred/) | `LAD_FRED_API_KEY` | Best first US macro provider. |
| 2 | Marketaux | Financial news and sentiment | Free API key | [Docs](https://www.marketaux.com/documentation) | `LAD_MARKETAUX_API_KEY` | Useful for entity-tagged financial news if GDELT matching is too noisy. |
| 2 | NewsAPI | General news | Free development API key | [Docs](https://newsapi.org/docs) | `LAD_NEWSAPI_KEY` | Useful for general headlines; check plan limits and commercial-use restrictions. |

Official or licensed data routes:

| Provider | Domain | Registration / application | Address | Notes |
| --- | --- | --- | --- | --- |
| HKEXnews | HK listed company announcements | No account for website search | [HKEXnews](https://www.hkexnews.hk/) | Good first HK filing source, but treat scraping/search behavior carefully because it is not a stable developer API. |
| CNINFO | China listed company announcements | Public website; data-service API may require access | [CNINFO](https://www.cninfo.com.cn/) / [CNINFO Data Service](https://webapi.cninfo.com.cn/) | Start with public announcement discovery; enterprise-style API access may require separate service terms. |
| HKEX Market Data Services | HK official market data | Paid/licensed application | [HKEX getting market data](https://www.hkex.com.hk/Global/Exchange/FAQ/Market-Data/Getting-Market-Data?sc_lang=en) | Only needed when open/free providers are not reliable enough or redistribution/commercial use is required. |

Never commit provider secrets. Use shell environment variables or a local `.env` file ignored by git.

```bash
cp .env.example .env
export LAD_ALPHA_VANTAGE_API_KEY="..."
export LAD_FINNHUB_API_KEY="..."
export LAD_FMP_API_KEY="..."
export LAD_FRED_API_KEY="..."
export LAD_MARKETAUX_API_KEY="..."
export LAD_NEWSAPI_KEY="..."
export LAD_TUSHARE_TOKEN="..."
export LAD_SEC_USER_AGENT="LycheeAlphaDesk/0.1 contact@example.com"
```

Implementation order:

1. Add no-key providers first: yfinance, AkShare, GDELT, SEC EDGAR, HKMA.
2. Add key-based providers behind optional extras and health checks.
3. Add paid/licensed providers only as optional plugins.
4. Every provider must output the same `DataSnapshot` shape and must record source timestamps, provider name, and warnings.

## 🧱 Technical Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.11+ |
| Package manager | uv |
| CLI | Typer |
| Terminal UI | Textual + Rich |
| Configuration | YAML + Pydantic v2 |
| Local storage | SQLite + Parquet, DuckDB later |
| Reports | Markdown + Jinja2 |
| Testing | pytest |
| Quality | ruff + mypy |
| Documentation | MkDocs Material later |

No web server is required for the MVP.

## 📜 Example Policy

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

## 🎯 MVP Scope

The first public version should focus on research, not execution. It should be useful without a broker account, an LLM key, TimesFM weights, or paid market data.

v0.1 core:

- Demo mode with mock portfolio, mock news, and sample reports.
- Local investment policy file.
- Terminal-native TUI shell.
- Small watchlist of ETFs and example stocks.
- Daily Markdown cockpit report.
- Local audit trail.

Post-v0.1 plugins:

- Market and macro data from free or open providers.
- News/event clustering.
- SEC filing analysis.
- TimesFM forecast intervals compared with simple baselines.
- LLM-generated research memo with a skeptic review.
- Read-only broker connectors for portfolio import and reconciliation.

Out of scope for MVP:

- Automatic live trading.
- High-frequency data or tick-level workflows.
- Margin, options, futures, and leveraged products.
- Paid exchange data subscriptions.
- Financial advice or guaranteed return claims.

## 🛠️ Project Status

Lychee AlphaDesk is in the runnable demo bootstrap stage.

The first milestone is a demo-first research workflow that can run locally without brokerage credentials. The current codebase includes the initial `lad` CLI, bundled demo data, data snapshots, provider health checks, policy validation, Markdown report generation, audit records, tests, and CI.

## 🗺️ Roadmap

| Version | Goal |
| --- | --- |
| v0.1 | Demo data, policy file, local storage, Markdown daily report, minimal TUI shell. |
| v0.2 | Market, macro, news, filing providers and provider health screens. |
| v0.3 | TimesFM forecasts and LLM investment committee. |
| v0.4 | Portfolio import, reconciliation, and read-only broker plugins. |
| v1.0 | Stable plugin API, documentation, examples, tests, and safety defaults. |

## 📚 Development Spec

See [docs/DEVELOPMENT_SPEC.md](docs/DEVELOPMENT_SPEC.md) for the first-phase architecture and implementation scope. A Chinese version is available at [docs/DEVELOPMENT_SPEC.zh-CN.md](docs/DEVELOPMENT_SPEC.zh-CN.md).

## 🛡️ Safety And Disclaimer

Lychee AlphaDesk is for research, education, and personal workflow automation.

It is not investment advice, legal advice, tax advice, or accounting advice. Markets involve risk. AI models can be wrong. Data can be stale, incomplete, or incorrect. Any real investment decision must be reviewed and approved by a human.

## 📄 License

License to be decided before the first implementation release.
