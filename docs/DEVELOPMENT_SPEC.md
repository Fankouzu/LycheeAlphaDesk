# Lychee AlphaDesk Development Spec

Version: v0.1 draft

[English](DEVELOPMENT_SPEC.md) | [简体中文](DEVELOPMENT_SPEC.zh-CN.md)

## 1. Product Direction

Lychee AlphaDesk is a terminal-native AI investment research workbench.

The main product is not a web dashboard. It is a fast local CLI/TUI application that runs without deployment, broker credentials, or paid API keys in demo mode.

The first implementation must prove the workflow:

```text
policy -> providers -> data quality -> daily report -> TUI review -> audit log
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
- Markdown daily report.
- Local audit trail.
- Minimal Textual TUI shell.

v0.1 should not deliver:

- Web frontend.
- FastAPI service.
- Live trading.
- Broker account requirements.
- Paid data source requirements.
- Real LLM requirement.
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
| Storage | SQLite for metadata, Parquet for time series |
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
lad data health --demo
lad data snapshot --demo
lad report --demo
lad policy check examples/demo/policy.yaml
lad audit list
lad
```

Command behavior:

- `lad` opens the TUI.
- `lad demo` verifies that demo files and local output directories exist.
- `lad setup` opens the unified interactive configuration center for data providers and LLM providers.
- `lad setup set` stores one provider key or token in the local config file for automation and agent use.
- `lad setup llm set` stores one OpenAI-compatible LLM `base_url`, API key, and optional model name non-interactively for automation and agent use.
- The interactive configuration center uses arrow-key provider selection in TTY environments and text fallback in non-TTY environments. Provider menus show display names and masked setup status only; provider details show registration links and user-facing setup guidance after selection. Hidden key entry confirms whether input was received with `✅` or `❌`.
- The LLM section stores a custom OpenAI-compatible `base_url`, API key, and model name. It first reads `{base_url}/models`; if the endpoint is unavailable or returns no usable model IDs, it prompts for a manual model name.
- `lychee` is the recommended console command; `lad` remains a short alias.
- `lad data health --demo` prints provider-level quality checks.
- `lad data snapshot --demo` writes a unified JSON snapshot with market, news, filing, and forecast data.
- `lad report --demo` generates a Markdown daily report from bundled demo providers.
- `lad policy check` validates the policy file and prints violations or warnings.
- `lad audit list` lists generated reports and decision records.

## 6. TUI Scope

The TUI should be useful but small in v0.1.

Screens:

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

## 7. Provider Interfaces

The core must depend on interfaces, not vendor SDKs.

Provider types:

- `MarketDataProvider`
- `NewsProvider`
- `FilingProvider`
- `MacroProvider`
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

- SQLite for metadata.
- Local files for Markdown report outputs.

## 11. Safety Defaults

v0.1 defaults:

- Demo mode on first run.
- Live trading disabled.
- Broker provider optional.
- LLM provider optional.
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
