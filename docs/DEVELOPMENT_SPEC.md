# Lychee AlphaDesk Development Spec

Version: v0.1 draft

[English](DEVELOPMENT_SPEC.md) | [简体中文](DEVELOPMENT_SPEC.zh-CN.md)

## 1. Product Direction

Lychee AlphaDesk is a terminal-native AI investment research workbench.

The main product is not a web dashboard. It is a fast local CLI/TUI application that runs without deployment, broker credentials, or paid API keys in demo mode.

The first implementation must prove the workflow:

```text
policy -> providers -> discovery -> data quality -> daily report -> TUI review -> audit log
```

The project should feel useful within five minutes of cloning the repository.

## 2. First-Phase Goals

v0.1 should deliver:

- Installable Python package.
- `lad` command.
- Demo mode with bundled data.
- Policy file validation.
- Provider interfaces.
- Demo providers.
- Data quality checks.
- Unified data snapshot command.
- Provider health command.
- CLI setup command for local provider configuration.
- Discovery-first workflow for US, HK, and China A-share research.
- Markdown daily report.
- Local audit trail.
- Minimal Textual TUI shell.

v0.1 should not deliver:

- Web frontend.
- FastAPI service.
- Live trading.
- Broker account requirements.
- Paid data source requirements.
- Real LLM requirement for demo mode.
- Real TimesFM requirement.

## 3. Technical Stack

| Layer | Decision |
| --- | --- |
| Language | Python 3.11+ |
| Package manager | uv |
| CLI | Typer |
| Terminal UI | Textual + Rich |
| Models and config | Pydantic v2 + YAML |
| Tables and terminal rendering | Rich |
| Reports | Markdown + Jinja2 |
| Storage | JSON for readable snapshots, SQLite for audit records and research queue, later Parquet for time series |
| Testing | pytest |
| Formatting and linting | ruff |
| Type checking | mypy |
| Docs | Markdown first, MkDocs Material later |

## 4. Package Layout

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

## 5. CLI Commands

Required v0.1 commands:

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

Command behavior:

- `lad` opens the TUI with `Today Discovery` as the first action, followed by watch-candidate review, data health, provider setup guidance, manual symbol drilldown, snapshots, and quit.
- `lad demo` verifies that demo files and local output directories exist.
- `lad setup` opens the unified interactive configuration center for data providers and LLM providers.
- `lad setup set` stores one provider key or token in the local config file for automation and agent use.
- `lad setup llm set` stores one OpenAI-compatible LLM `base_url`, API key, and optional model name non-interactively for automation and agent use.
- The interactive configuration center uses Textual `OptionList` controls and keyboard navigation in TTY environments. Human-facing menus must use ↑/↓/←/→/Tab for movement, Enter for selection, and Esc for back/exit. Menus must never use numbers or letters as option-selection controls, and this flow must not use a hand-rolled raw-key parser. Non-TTY environments must not fall back to text menus; they should use the non-interactive setup commands. Provider menus show display names and masked setup status only; provider details show registration links and user-facing setup guidance after selection. Hidden key entry confirms whether input was received with `✅` or `❌`.
- The LLM section stores a custom OpenAI-compatible `base_url`, API key, and model name. It first reads `{base_url}/models`; if the endpoint is unavailable or returns no usable model IDs, it prompts for a manual model name.
- `lychee` is the recommended console command; `lad` remains a short alias.
- `lad data health --demo` prints provider-level quality checks.
- `lad data snapshot --demo` writes a unified JSON snapshot with market, news, filing, and forecast data.
- `lychee discover today` runs a discovery-first workflow across US, HK, and China A-share markets without requiring symbols up front.
- `lad discover today --markets us,hk,cn` calls the configured OpenAI-compatible `/chat/completions` endpoint with `stream: true`, parses the model's JSON response, and writes a local `llm-synthesized` discovery report cache with themes, watch candidates, evidence references, warnings, and next actions. The command must fail if no LLM provider is configured, if the API request fails, or if the model does not return valid JSON; silent fallback reports are not allowed. Successful runs must also write `.alphadesk/research.sqlite3` as the local database for research queue and evidence tracking. The default LLM read timeout is 180 seconds.
- `lad data pull market` writes Alpha Vantage daily prices into the local live cache.
- `lad data pull news` writes Marketaux, Finnhub, or NewsAPI events into the local live cache.
- `lad data pull filings` writes recent SEC EDGAR filings into the local live cache.
- `lad data health` checks live cache presence and row counts.
- `lad data snapshot` writes a unified JSON snapshot from the live cache.
- The TUI home action menu must expose the discovery-first workflow before manual symbol workflows. Manual symbol entry remains available only as a drilldown path for users who already know which asset they want to inspect. The Textual built-in command palette is not a business-command surface and should stay disabled on the home screen to avoid terminal glyph-width issues.
- `lad report --demo` generates a Markdown daily report from bundled demo providers.
- `lad policy check` validates the policy file and prints violations or warnings.
- `lad research queue` lists watch candidates from the SQLite research database with status, market, symbol, theme, evidence count, and next-action count.
- `lad audit list` lists generated reports and decision records.

## Interaction Standard

This is a permanent interaction rule for the whole project:

- Human-facing interactive screens must be keyboard-navigation-first.
- Menus and option selection must use ↑/↓/←/→/Tab to move, Enter to select, and Esc to go back or exit.
- Menus must not use numbers, letters, or typed command aliases as option selectors.
- Typed text is allowed only for actual values, such as API keys, URLs, model names, symbols, or file paths.
- v0.1 human-facing CLI/TUI copy is Chinese-first; machine identifiers such as provider names, command arguments, model IDs, and symbols may stay in their original form.
- Potentially slow actions such as LLM calls, network requests, data pulls, and report generation must show a loading/waiting state before blocking work starts; failures must show readable errors so the terminal never feels frozen.
- Non-interactive commands are allowed for automation and coding agents, but they must be explicit command arguments rather than hidden menu selections.
- Non-TTY environments must not receive numeric or letter-based text-menu fallbacks.

## 6. TUI Scope

The TUI should be useful but small in v0.1.

Screens:

- Today Discovery: US/HK/CN themes, source-backed watch candidates, risk flags, and suggested next data pulls.
- Today: daily conclusion, risk status, and no-action reasoning.
- Portfolio: mock positions, cash weight, target drift, and policy violations.
- News: demo event clusters and affected assets.
- Forecasts: mock forecast intervals and simple baseline comparison.
- Memos: generated research memo preview.
- Policy: loaded policy rules and validation results.
- Providers: demo provider health.
- Audit: generated report history.

TUI requirements:

- Keyboard-first navigation.
- Fast local startup.
- No network required in demo mode.
- Clear "demo data" labeling.
- No live order entry.
- Do not ask beginners for symbols as the first product step.

## 6.1 Today Discovery Engine

The product entry point is discovery-first.

The engine starts from broad evidence and then narrows into themes and candidates:

```text
US/HK/CN market context -> broad news and events -> LLM synthesis -> watch candidates -> drilldown data
```

Required market coverage:

| Market | First-pass data | Expected output |
| --- | --- | --- |
| US | Major indexes, ETFs, financial news, SEC filings, large-cap watch universe | Themes, stocks, ETFs, sector candidates |
| HK | Hang Seng family indexes, HK market news, HKEX announcements, HKD/rate context | Themes, stocks, China-linked sectors, IPO/new-share notes |
| CN A-shares | Broad indexes, sector boards, announcements, earnings/forecast notices, IPO/new-share notes | Themes, A-share candidates, policy-linked sectors |

The discovery report must include:

- Market coverage and missing-provider warnings.
- Source list with provider names and timestamps.
- Themes with summaries, evidence, related sectors, risk flags, and confidence levels.
- Watch candidates with display names, optional symbols, markets, asset types, evidence, risk flags, and suggested next data pulls.
- A clear disclaimer that candidates are research targets, not buy/sell recommendations.

The LLM may summarize, cluster, extract, compare, and suggest next research steps. It must not produce direct buy/sell calls, target prices, automatic allocations, or live trading instructions.

If no LLM provider is configured, if the API request fails, or if the model does not return valid JSON, the command must fail with setup/error guidance and must not write a discovery cache.

## 7. Provider Interfaces

The core must depend on interfaces, not vendor SDKs.

Provider types:

- `MarketDataProvider`
- `NewsProvider`
- `FilingProvider`
- `MacroProvider`
- `DiscoveryProvider`
- `ForecastProvider`
- `LLMProvider`
- `BrokerProvider`
- `StorageProvider`

v0.1 providers:

- `DemoMarketDataProvider`
- `DemoNewsProvider`
- `DemoFilingProvider`
- `DemoForecastProvider`
- `DemoLLMProvider`
- `ManualCsvBrokerProvider`

Optional real providers can be added after the interface is stable.

Plugin rules:

- Real providers must be optional extras, not default dependencies.
- Broker integrations must be read-only before any order-draft workflow is added.
- Provider failures must return structured warnings with source names and timestamps.
- Demo mode must never silently mix demo data with live provider data.

## 8. Policy File

Policy file format: YAML.

Minimum supported fields:

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

Validation output must include:

- Errors: invalid configuration that blocks reports.
- Warnings: risky but allowed configuration.
- Passes: rules that are explicitly satisfied.

## 9. Daily Report

The v0.1 report is Markdown.

Required sections:

- Daily conclusion.
- Portfolio snapshot.
- Policy check.
- Data quality status.
- News and events.
- Forecast summary.
- Skeptic review.
- No-action or action rationale.
- Audit metadata.

The report must clearly mark demo data.

## 10. Audit Trail

Every generated report should write an audit record.

Minimum fields:

- Report id.
- Generation timestamp.
- Policy file path.
- Provider names.
- Input snapshot ids.
- Output report path.
- Demo or live mode.
- Warnings and errors.

Storage:

- JSON files for readable report and snapshot outputs.
- SQLite for audit metadata, discovery runs, research candidates, evidence links, and research queue state.
- Local files for Markdown report outputs.

## 11. Safety Defaults

v0.1 defaults:

- Demo mode on first run.
- Live trading disabled.
- Broker provider optional.
- LLM provider optional for demo/report workflows, but required for Today Discovery.
- TimesFM provider optional.
- Provider keys stored in the user config directory, not project-level `.env` files.
- All real provider failures must degrade to explicit warnings.
- No silent fallback from real data to demo data.

## 12. Open-Source Defaults

The repository should be easy to try and easy to audit.

Defaults:

- No API keys required for the first successful run.
- No broker account required for the first successful run.
- No background services required for the first successful run.
- CLI output should explain where reports and audit records were written.
- Generated demo reports should include a clear non-advice disclaimer.
- Configuration examples should prefer conservative, long-term investing assumptions.

## 13. Acceptance Criteria

v0.1 is complete when a new user can:

1. Clone the repository.
2. Install the package with uv.
3. Run `lad demo`.
4. Run `lad report --demo`.
5. Open the generated Markdown report.
6. Run `lad` and navigate the TUI.
7. Understand that the system is research-first and not a trading bot.
8. Inspect policy validation and audit records.

## 14. Implementation Order

1. Python package and CLI skeleton.
2. Demo data files.
3. Pydantic config and policy models.
4. Provider interfaces.
5. Demo providers.
6. Data quality checks.
7. Markdown report templates.
8. Audit storage.
9. Textual TUI shell.
10. Tests and quickstart docs.
