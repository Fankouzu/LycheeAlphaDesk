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
lad data pull news
lad data pull news --symbols AAPL --provider auto
lad data pull news --symbols AAPL --provider auto --force
lad data pull filings --symbols AAPL,TSLA --limit 3
lad data freshness
lad data health
lad data snapshot
lad report --demo
lad policy check examples/demo/policy.yaml
lad research queue
lad audit list
lad
```

Command behavior:

- `lad` opens the TUI with `今日市场发现` as the first action and `研究工作台` as the second action, followed by manual symbol drilldown, data health, provider setup guidance, snapshots, and quit.
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
- `lad discover today --markets us,hk,cn` first checks or pulls market-level news cache, then calls the configured OpenAI-compatible `/chat/completions` endpoint with `stream: true`, parses the model's JSON response, and writes a local `llm-synthesized` discovery report cache with themes, watch candidates, evidence references, warnings, and next actions. The command must fail if no suitable news provider is available, if no LLM provider is configured, if the API request fails, or if the model does not return valid JSON; silent fallback reports are not allowed. Successful runs must also write `.alphadesk/research.sqlite3` as the local database for research queue and evidence tracking. The default LLM read timeout is 180 seconds.
- `lad data pull market` writes Alpha Vantage daily prices into the local live cache. It uses market-cache freshness and trading-session checks by default; `--force` bypasses them.
- `lad data pull news` writes Marketaux, Finnhub, or NewsAPI events into the local live cache. Without `--symbols` it pulls market-level news; with `--symbols` it pulls symbol-level news. `--query` passes topic keywords for evidence strengthening around a research theme; topic queries should use Marketaux or NewsAPI because Finnhub does not support keyword topic search. It uses news-cache freshness by default; `--force` refreshes explicitly. The news cache must preserve existing rows and append deduplicated new rows so refreshes do not change the meaning of `news_001`-style evidence IDs. Finnhub currently supports symbol-level news only; market-level news should use Marketaux or NewsAPI.
- `lad data pull filings` writes recent SEC EDGAR filings into the local live cache.
- `lad data freshness` only reads local `cache_entries` and displays cache layer, status, provider, cache key, market, session state, expiration time, and row count without triggering provider requests.
- `lad data health` checks live cache presence and row counts.
- `lad data snapshot` writes a unified JSON snapshot from the live cache.
- The TUI home action menu must expose the discovery-first workflow, research workbench, research review history, and research memo history before manual symbol workflows. The `研究工作台` action must run the workbench readiness loop and display a selectable research task list with each task's entrypoint, priority, evidence status, and ranking reason; the `研究复核历史` action must read SQLite review records and display review verdicts, notes, evidence counts, and artifact paths; the `研究备忘录历史` action must read SQLite memo records and display summaries, confidence, support/skeptic/missing/next-step counts, and artifact paths. Users move with ↑/↓ and press Enter to open a `研究任务面板` with entrypoint, priority, ranking reason, the research question to resolve, startup steps, evidence status, signal reading, evidence matrix, collected evidence, price data, related news, filings/financial clues, data gaps, next action, and a selectable action menu. The detail action menu should at least refresh task-level prices, refresh task-level news, refresh topic news for weak-evidence tasks, refresh applicable US filings/financial clues, run drilldown verification with the evidence board, generate a research memo, and return to the research task list. After refresh actions complete, the TUI must return to the same research task by symbol, proxy symbol, or name even if the workbench is reordered, and it must put rerun drilldown verification first in the next-action menu before memo generation and returning to the task list. The drilldown verification result page must expose selectable review-record actions for continue research, needs more evidence, pause watch, or blocked workflow verdicts; the first option must record the research decision board's suggested verdict while the remaining verdicts stay available as manual overrides. After a review is recorded, the TUI must not stop at a static confirmation page: `needs_more_evidence` must offer topic-news refresh and rerun drilldown verification actions, and `continue_research` must offer research memo generation and rerun drilldown verification actions. Manual symbol entry remains available only as a drilldown path for users who already know which asset they want to inspect. The Textual built-in command palette is not a business-command surface and should stay disabled on the home screen to avoid terminal glyph-width issues.
- `lad report --demo` generates a Markdown daily report from bundled demo providers.
- `lad policy check` validates the policy file and prints violations or warnings.
- `lad research queue` lists watch candidates from the SQLite research database with status, market, symbol, theme, evidence count, and next-action count. The default output must be the deduplicated current active queue: candidates with symbols keep the latest row per market + symbol; candidates without symbols keep the latest row per market + normalized name, with conservative grouping for very clear variants such as China AI data-center supply chain / industrial chain / chain wording. Historical discovery runs may remain in SQLite, but they must not be dumped into the default workbench task list.
- `lad research deepen` creates second-stage research packets from the research queue, writing them to the local SQLite `research_packets` table and `.alphadesk/research/research-packets-*.json` with candidate identity, evidence IDs, expanded evidence, cached data, data gaps, proxy mappings, and next verification actions. The deepen layer must build packets from a candidate pool first, then prioritize ready packets without data gaps so the default workbench is not filled by blocked items. Related news selection must sort by research-theme relevance before timestamp so fresh but off-topic symbol news does not hide theme evidence. User-facing output must be a workbench task card, not lesson-style prose, with at least: research question, entrypoint, priority, ranking reason, evidence status, key checks, and next-action queue. Evidence status must include support, reverse, direction-pending, and off-topic evidence counts; candidates with only reverse, direction-pending, or off-topic evidence must be downgraded to reviewing evidence direction first.
- `lad research fill-gaps` automatically fills deterministic data gaps exposed by the queue and research packets. The first version supports missing market prices, missing SEC filings for US stock candidates, and proxy-price filling for auditable mappings on symbol-less themes. Price filling uses `auto` by default: US symbols use Alpha Vantage, HK/China symbols use Eastmoney daily bars, and Yahoo chart is used as a fallback when the primary source fails. Candidates without symbols must not be silently rewritten to a guessed ticker; the system may only generate proxy candidates with reasons, confidence, and evidence IDs, then require user review before drilldown.
- `lad research check --strict` is the agent/CI workbench readiness entrypoint. It must run gap filling, regenerate research packets, produce the `AlphaDesk 研究工作台`, and write machine-readable `workbench-check-*.json`. Workbench output must not be just symbols, proxy tickers, generic conclusions, or lesson-style explanation; it must show executable tasks, blocked tasks, ranking reasons, evidence status, and the next-action queue. The workbench must reflect evidence-direction checks back into priority and next actions: candidates with only reverse, direction-pending, or off-topic news evidence must not be shown as direct-drilldown tasks. In strict mode it must exit non-zero whenever the evidence, research-entrypoint, proxy-price, or data-gap gates fail.
- `lad research detail` is the non-interactive detail entrypoint for one research task. It must reuse the same core rendering logic as the TUI research detail and print `研究任务面板`, research status, ranking reason, the research question to resolve, startup steps, signal reading, evidence matrix, price data, related news, filings/financial clues, data gaps, and executable refresh commands. The task panel must not pretend to be an investment conclusion page; it must tell the user which drilldown command to run first, which three evidence-board columns to inspect, and how to record a workflow verdict with `lad research review`. Research status may only express blocked, evidence-review, proxy review, evidence-building, or ready-for-drilldown states; it must not express buy/sell, allocation, or target-price advice. CLI and TUI must not maintain divergent research-detail wording.
- `lad research run` is the data-refresh execution chain for one research task. It must select a task, refresh related prices/news/applicable US filings, rerun the workbench check, print the updated research detail, and write a `research-run-*.json` audit artifact. If the task evidence quality is missing, needs_review, or mixed, the execution chain must build a topic-news query from the research theme and pull one extra round of topic news. The artifact must include structured `assessment` with stage, consistency-review state, evidence reading, and next decision. It must not produce buy/sell advice; it only advances evidence collection and research state.
- `lad research verify` is the drilldown verification entrypoint for one research task. It must check price, volume, news, filings/financial clues, and proxy-instrument state, then write a `research-verification-*.json` artifact. Check results may only express pass, pending review, blocked, or not applicable; it must also produce a three-column evidence board for support evidence, risks/reverse checks, and missing evidence, plus a research decision board that translates the evidence state into workflow verdicts such as continue research, needs more evidence, pause watch, or blocked. News and discovery evidence must not enter support evidence by count alone; headlines and summaries must pass topic-relevance and evidence-direction checks first. Unmatched rows should move to risks/reverse checks, topic-matched rows with negative direction words such as falls/cuts/weak/slowdown/pressure should become reverse evidence, and topic-matched but direction-unclear rows should be marked as pending news. The consistency conclusion must stay pending human review until a dedicated consistency-analysis engine exists. The research decision board may only provide research workflow state and next research actions; it must not provide buy, sell, hold, allocation, target-price, or expected-return advice.
- `lad research memo` is the LLM second-stage research memo entrypoint for one research task. It must run the same drilldown verification first, then send the evidence board, verification checks, and next actions to the configured LLM and write `research-memo-*.json`. The memo may only contain a summary, evidence reading, support points, skeptic review, missing evidence, and next research steps; it must not contain buy, sell, hold, allocation, target-price, expected-return, position-sizing, or trading-instruction language. Missing LLM configuration, request failure, malformed JSON, missing required fields, or investment-advice language must fail the command.
- The TUI research detail view must expose the same `生成研究备忘录` action, show an LLM loading state, and reuse the same failure boundaries and non-advice boundary as `lad research memo`.
- `lad research review` is the review-record entrypoint for one research task. It must run the same drilldown verification first, then write `research-review-*.json` and a SQLite `research_reviews` row. Review verdicts may only express research workflow state: continue research, needs more evidence, pause watch, or blocked; they must not express buy, sell, hold, allocation, target-price, or expected-return advice.
- `lad research reviews` is the research review history entrypoint. It must read records from the SQLite `research_reviews` table and show the review verdict, note, evidence counts, review artifact, and linked drilldown verification artifact. It is for reviewing the research process and must not present historical reviews as a buy/sell list.
- `lad research memos` is the research memo history entrypoint. It must read records from the SQLite `research_memos` table and show the summary, confidence, support/skeptic/missing/next-step counts, memo artifact, and linked drilldown verification artifact. It is for reviewing the research process and must not present historical memos as a buy/sell list.
- `lad audit list` lists generated reports and decision records.

## Data Freshness Policy

Local caches must have explicit freshness windows. The default path reuses unexpired data and refreshes only when data is expired, missing, or explicitly forced with `--force`.

Market-price cache freshness must combine data TTL with market session state:

- US regular trading is treated as 9:30-16:00 ET.
- HK regular trading is treated as 9:30-12:00 and 13:00-16:00 HKT.
- China A-share regular trading is treated as 9:30-11:30 and 13:00-15:00 CST.
- During open sessions the default freshness window is 15 minutes.
- During HK/CN lunch breaks, the default path does not refresh until the afternoon session opens.
- After close, the system may perform one final-close refresh; once final, market cache is frozen until the next trading-day open.
- Weekends do not refresh by default; the first implementation does not include full exchange holiday calendars.
- `--force` must bypass freshness and session checks.

News cache uses a basic TTL policy:

- The default freshness window is 1 hour.
- While fresh, local `news-events.json` is reused so discovery and manual drilldowns do not repeatedly consume provider quota.
- `--force` must bypass news freshness.

Cache state is stored in the `cache_entries` table inside `.alphadesk/research.sqlite3`, including layer, cache_key, provider, artifact_path, created_at, expires_at, ttl_seconds, market, session_state, row_count, and is_final_for_session.

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
US/HK/CN market context -> broad news and events -> evidence pack -> LLM synthesis -> watch candidates -> drilldown data
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
- Themes with summaries, evidence IDs, related sectors, risk flags, and confidence levels.
- Watch candidates with display names, optional symbols, markets, asset types, evidence IDs, risk flags, and suggested next data pulls.
- A clear disclaimer that candidates are research targets, not buy/sell recommendations.

News and events must be converted into an evidence pack before entering the LLM. Evidence items use stable local IDs such as `news_001` and include headline, summary, source_url, timestamp, provider, symbols, and tags. The evidence pack should filter obvious direct-pick noise such as direct buy picks, target-price articles, and analyst-rating articles.

The LLM may summarize, cluster, extract, compare, and suggest next research steps. It must cite evidence IDs from the evidence pack instead of vague evidence descriptions. The system must validate evidence fields returned by the LLM; if evidence is not an existing local evidence-pack ID such as `news_001`, the command must fail and must not write a discovery cache or research queue. The LLM must not produce direct buy/sell calls, target prices, automatic allocations, or live trading instructions.

If no LLM provider is configured, if the API request fails, or if the model does not return valid JSON, the command must fail with setup/error guidance and must not write a discovery cache.

## 6.2 Research Deepen Engine

Research Deepen is the second-stage preparation layer after discovery. It reads the SQLite research queue and local live cache to generate auditable research packets instead of direct investment conclusions.

Each research packet must include:

- Candidate identity: candidate_id, display_name, symbol, market, asset_type, related_theme, why_watch, confidence, and status.
- Discovery evidence IDs plus expanded evidence details.
- Local cached data: price, related news, and filings.
- Data gaps: missing symbol, missing price cache, missing filing cache, or evidence IDs that cannot be resolved from the current local cache.
- Next verification actions.
- A clear non-advice disclaimer.

Research Deepen must not output direct buy/sell calls, target prices, automatic allocations, or live trading instructions.

The research-data workflow must be a closed loop:

1. Develop or adjust data/research capability.
2. Run real commands against real cached/provider data.
3. Regenerate research packets.
4. Check whether data_gaps decrease, evidence remains traceable, and output still avoids investment advice.
5. If data is still insufficient for the research task, continue development instead of leaving the user to guess the missing steps.

The first automatic gap filler only performs deterministic actions: market prices for candidates with symbols and SEC filings for US stock candidates. Market providers must tolerate per-symbol failures: one market/provider failure must not discard successfully fetched rows for other symbols. Symbol-less candidates enter a mapping queue and should later be handled by a symbol mapping provider or evidence-constrained LLM-assisted mapping.

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
