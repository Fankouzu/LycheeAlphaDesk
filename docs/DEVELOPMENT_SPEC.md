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
lad data set fund --symbol 2800.HK --name "Tracker Fund of Hong Kong" --source-url https://example.com/2800 --tracking-index "Hang Seng Index" --expense-ratio "0.10%"
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

- `lad` opens the TUI with `今日市场发现` as the first action, `研究工作台` as the second action, and `机会雷达` as the third action, followed by `下一步行动队列`, `待判定证据队列`, research review history, row-level evidence review history, research memo history, research data requests, provider backlog, manual symbol drilldown, data health, provider setup guidance, snapshots, and quit.
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
- `lad discover radar` must read local market and news caches without calling the LLM or requiring the user to enter a symbol, then combine symbol-level news heat, theme keyword hits, and volume ranking into opportunity-radar research signals. Each signal must include market, symbol, theme, score, price snapshot, why it matters, evidence headlines, next verification commands, and drilldown targets mapped from locally cached instruments. Drilldown targets must show name, market, category, mapping reason, evidence gap, and data/research commands; instruments that are not in the local cache must not be presented as current radar results. It may only answer "what should be researched next" and must not provide buy, sell, hold, allocation, target-price, or expected-return language.
- `lad data pull market` writes daily prices into the local live cache. `auto` uses Alpha Vantage for US symbols; when a Tushare token is configured, it uses Tushare `daily`, `fund_daily`, or `hk_daily` for China stocks, China ETF-style codes, and HK symbols before retaining Eastmoney and Yahoo chart as fallbacks. Without a configured Tushare token, HK/China symbols start with Eastmoney. It uses market-cache freshness and trading-session checks by default; `--force` bypasses them. Tushare `40203` is an interface-entitlement gap, not a missing-key error: the CLI must tell the user to enable the required interface or plan and must not suggest re-entering the key as the remedy.
- `lad data pull news` writes Marketaux, Finnhub, or NewsAPI events into the local live cache. Without `--symbols` it pulls market-level news; with `--symbols` it pulls symbol-level news. `--query` passes topic keywords for evidence strengthening around a research theme; topic queries should use Marketaux or NewsAPI because Finnhub does not support keyword topic search. It uses news-cache freshness by default; `--force` refreshes explicitly. The news cache must preserve existing rows and append deduplicated new rows so refreshes do not change the meaning of `news_001`-style evidence IDs. Finnhub currently supports symbol-level news only; market-level news should use Marketaux or NewsAPI. Automatic fallback warnings must retain a sanitized, actionable class for authentication, access-denied, rate-limit, or timeout failures rather than collapsing them into a generic provider failure; direct pulls and workbench diagnostics must show `lychee setup` as the recovery entrypoint for authentication or access-denied failures.
- `lad data pull filings` writes recent SEC EDGAR filings into the local live cache.
- `lad data set fund` writes manually verified, source-backed fund/ETF metadata into `fund-metadata.json` for proxy ETF/index checks, including tracking index, fee/expense ratio, holdings summary, source URL, and as-of date. The command must require a source URL and must not hardcode drifting fund fees or constituents as production truth.
- `lad data pull financials` writes SEC EDGAR XBRL `companyfacts` snapshots to `financials.json`. The first provider covers US issuers only and must retain form type, revenue, net income, operating cash flow, and the official source URL without inventing unavailable values. Each metric must retain its own start/end dates so quarterly revenue is never presented as the same period as year-to-date operating cash flow. It may retain a prior-year comparison only when the metric concept, form, fiscal period, and period length match and the previous end date is within the prior-year comparison window; otherwise prior fields must remain empty. Research packets, task detail, verification checks, and evidence boards must surface a cached snapshot as auditable fact; HK/CN financial coverage must remain explicitly unavailable until a provider exists.
- `lad data freshness` only reads local `cache_entries` and displays cache layer, status, provider, cache key, market, session state, expiration time, and row count without triggering provider requests.
- `lad data health` checks live cache presence and row counts, then reports separate US, HK, and China A-share market-coverage rows from cached price symbols and the latest market warnings. A Tushare `40203` entitlement warning must apply only to the affected market and must not imply an outage for another market.
- `lad data snapshot` writes a unified JSON snapshot from the live cache.
- The TUI home action menu must expose the discovery-first workflow, opportunity radar, research workbench, unified next-action queue, pending evidence review queue, research review history, row-level evidence review history, research memo history, research data requests, and provider backlog before manual symbol workflows. The `研究工作台` action must run the workbench readiness loop and display a selectable research task list with each task's entrypoint, priority, evidence status, and ranking reason; the `下一步行动队列` action must aggregate pending evidence reviews, provider/data-source gaps, executable research data requests, and regular research-task follow-up commands into one beginner-facing queue with action area, reason, source artifact, and copyable command, plus a selectable menu for whitelisted automatic actions. When an executable research data request covers the same symbol or task name as a workbench candidate, the queue must hide that candidate's generic research follow-up command and keep the more concrete data request action. Execution results must show `completed`, `cached`, `no-data`, or `failed`, and `no-data` / `failed` must not show a follow-up verification command; the `待判定证据队列` action must read the latest drilldown verification artifact for each task, filter out already-reviewed evidence rows, display pending news evidence as selectable rows, open a detail page with the research question, review command, and source verification artifact, and record the row as support, reverse/risk, or irrelevant without leaving the queue workflow. After a pending evidence row is recorded from this home-level queue, the TUI must offer a direct `重新下钻核验` action for the same task so the user can immediately inspect the updated evidence board; the `研究复核历史` action must read SQLite review records and display review verdicts, notes, evidence counts, and artifact paths; the `证据复核历史` action must read SQLite row-level evidence review records and display each evidence fragment, direction label, note, and artifact path; the `研究备忘录历史` action must read SQLite memo records and display summaries, confidence, support/skeptic/missing/next-step counts, and artifact paths; the `研究数据请求` action must read latest memo requests first and fall back to latest verification hypothesis-panel requests for tasks without memos, display each next data request, suggested data-collection commands, source memo or source verification artifact, and linked drilldown verification artifact, and expose selectable actions to execute the supported portion of one request; the `数据源缺口队列` action must turn unsupported manual-source requests into auditable provider/plugin backlog items with data domain, plugin type, current coverage gap, candidate source shape, source memo or verification artifact, and linked verification artifact. Users move with ↑/↓ and press Enter to open a `研究任务面板` with entrypoint, priority, ranking reason, the research question to resolve, startup steps, evidence status, signal reading, evidence matrix, collected evidence, price data, related news, filings/financial clues, data gaps, next action, and a selectable action menu. The detail action menu should at least refresh task-level prices, refresh task-level news, refresh topic news for weak-evidence tasks, refresh applicable US filings/financial clues, run drilldown verification with the evidence board, generate a research memo, and return to the research task list. After refresh actions complete, the TUI must return to the same research task by symbol, proxy symbol, or name even if the workbench is reordered, and it must put rerun drilldown verification first in the next-action menu before memo generation and returning to the task list. The drilldown verification result page must expose selectable review-record actions for continue research, needs more evidence, pause watch, or blocked workflow verdicts; the first option must record the research decision board's suggested verdict while the remaining verdicts stay available as manual overrides. Pending news rows must also expose selectable row-level evidence review actions to mark the evidence as support, reverse, or irrelevant, then rerun drilldown verification so the result page shows the updated evidence board. After a review is recorded, the TUI must not stop at a static confirmation page: `needs_more_evidence` must offer topic-news refresh and rerun drilldown verification actions, and `continue_research` must offer research memo generation and rerun drilldown verification actions. Manual symbol entry remains available only as a drilldown path for users who already know which asset they want to inspect. The Textual built-in command palette is not a business-command surface and should stay disabled on the home screen to avoid terminal glyph-width issues.
- `lad report --demo` generates a Markdown daily report from bundled demo providers.
- `lad policy check` validates the policy file and prints violations or warnings.
- `lad research queue` lists watch candidates from the SQLite research database with status, market, symbol, theme, evidence count, and next-action count. The default output must be the deduplicated current active queue: candidates with symbols keep the latest row per market + symbol; candidates without symbols keep the latest row per market + normalized name, with conservative grouping for very clear variants such as China AI data-center supply chain / industrial chain / chain wording. Historical discovery runs may remain in SQLite, but they must not be dumped into the default workbench task list.
- `lad research deepen` creates second-stage research packets from the research queue, writing them to the local SQLite `research_packets` table and `.alphadesk/research/research-packets-*.json` with candidate identity, evidence IDs, expanded evidence, cached data, data gaps, proxy mappings, and next verification actions. The deepen layer must build packets from a candidate pool first, then prioritize ready packets without data gaps so the default workbench is not filled by blocked items. Related news selection must sort by research-theme relevance before timestamp so fresh but off-topic symbol news does not hide theme evidence. ETF, fund, and index tasks must additionally match financial-market context such as stocks, indexes, ETFs, exchanges, turnover, or liquidity; same-city or broad technology keywords alone must not admit a news item as related. User-facing output must be a workbench task card, not lesson-style prose, with at least: research question, entrypoint, priority, ranking reason, evidence status, key checks, and next-action queue. Evidence status must include support, reverse, direction-pending, and off-topic evidence counts; candidates with only reverse, direction-pending, or off-topic evidence must be downgraded to reviewing evidence direction first.
- Home-level workbench and next-action rows must never concatenate raw `data_gaps` into a task title or action label. They must translate known gaps into one compact user action such as establishing an observation entrypoint or collecting price, news, and filing evidence, then show a clear current status and research question. Raw gap strings, discovery IDs, and audit detail remain available in the research task detail and JSON artifacts.
- `lad research fill-gaps` automatically fills deterministic data gaps exposed by the queue and research packets. The first version supports missing market prices, missing ticker-linked news, missing SEC filings for US stock candidates, and proxy-price filling for auditable mappings on symbol-less themes. Current ticker-linked news that passes the existing topic and market-context filter may replace a missing historical discovery evidence ID as research evidence; the original missing IDs remain in the packet for audit, but must not permanently block an otherwise auditable current research path. A provider response with rows that do not pass this filter is only a partial fill: it must retain the rows for audit, expose `unresolved_news_symbols`, and not report the news evidence as complete. Price filling uses `auto` by default: US symbols use Alpha Vantage; HK/China symbols use configured Tushare routes when available, otherwise Eastmoney daily bars, with Yahoo chart as a final fallback. Candidates without symbols must not be silently rewritten to a guessed ticker; the system may only generate proxy candidates with reasons, confidence, and evidence IDs, then require user review before drilldown.
- `lad research check --strict` is the agent/CI workbench readiness entrypoint. It must run gap filling, regenerate research packets, produce the `AlphaDesk 研究工作台`, and write machine-readable `workbench-check-*.json`. Workbench output must not be just symbols, proxy tickers, generic conclusions, or lesson-style explanation; it must show executable tasks, blocked tasks, ranking reasons, evidence status, and the next-action queue. Every task card and next-action queue row must include a copyable `lychee research ...` command; blocked tasks must include the command that restarts the data-refresh chain. When a candidate is visible only because the workbench scan limit was expanded, subsequent `research run`, `research verify`, `research review`, `research memo`, and `research evidence-review` commands must preserve that scan range so copying a command does not fall back to the default first five candidates and miss the task. The machine-readable `workbench-check-*.json` must store the same `next_command` per candidate for agents and TUI consumers, plus structured `auto_fill` actions with requested symbols, status, row count, output path, and provider warnings. When an automatic fill is partial or fails, the CLI must show a compact data-source diagnostic and the artifact must preserve the full warning so agents can recover without repeating a blind pull. The workbench must reflect evidence-direction checks back into priority and next actions: candidates with only reverse, direction-pending, or off-topic news evidence must not be shown as direct-drilldown tasks, and their main command must be `lad research run --force` so topic news is refreshed and verification reruns instead of repeating read-only drilldown. In strict mode it must exit non-zero whenever the evidence, research-entrypoint, proxy-price, or data-gap gates fail.
- `lad research detail` is the non-interactive detail entrypoint for one research task. It must reuse the same core rendering logic as the TUI research detail and print `研究任务面板`, research status, ranking reason, the research question to resolve, startup steps, signal reading, evidence matrix, price data, related news, filings/financial clues, data gaps, and executable refresh commands. The task panel must not pretend to be an investment conclusion page; it must tell the user which drilldown command to run first, which evidence-board columns to inspect, and how to record a workflow verdict with `lad research review`. Research status may only express blocked, evidence-review, proxy review, evidence-building, or ready-for-drilldown states; it must not express buy/sell, allocation, or target-price advice. CLI and TUI must not maintain divergent research-detail wording.
- `lad research run` is the data-refresh execution chain for one research task. It must select a task, refresh related prices/news/applicable US filings, rerun the workbench check, print the updated research detail, and write a `research-run-*.json` audit artifact. If the task evidence quality is missing, needs_review, or mixed, the execution chain must build a topic-news query from the research theme and pull one extra round of topic news. The artifact must include structured `assessment` with stage, consistency-review state, evidence reading, and next decision. It must not produce buy/sell advice; it only advances evidence collection and research state.
- `lad research verify` is the drilldown verification entrypoint for one research task. It must check price, volume, news, filings/financial clues, and proxy-instrument state, then write a `research-verification-*.json` artifact. Direct-symbol tasks use local price cache; symbol-less tasks with proxy ETF/index mappings must use each mapping's `latest_price` for price and volume checks and add those proxy prices to support evidence instead of reporting missing local prices when proxy prices are covered. When source-backed `fund-metadata.json` exists for a proxy ETF/fund, verification must add its tracking index, fee, holdings summary, and source to support evidence; when metadata is missing or partial, only the specific missing fields should enter missing evidence instead of continuing to show a generic proxy-data gap. Check results may only express pass, pending review, blocked, or not applicable; it must also produce a four-column evidence board for support evidence, risks/reverse checks, off-topic/filtered evidence, and missing evidence, an evidence-change summary, evidence-change details, an analyst readout, a research hypothesis panel, plus a research decision board. The analyst readout must translate the evidence board into current signal, reverse pressure, evidence gap, evidence change, and the next research action so beginners can understand the state without reading raw evidence rows first; the research hypothesis panel must then show the core question, working hypothesis, support chain, counter-evidence chain, gap priorities, and next data requests so the user knows what is being tested. Both must be written into the verification artifact and must not provide investment advice. When pending news exists, the output must include a `待判定证据处理` section with the task-filtered `lad research pending-evidence` queue command, concrete `lad research evidence-review` command templates, and the rerun `lad research verify` command. When a previous `research-verification-*.json` exists for the same task, the system must compare both counts and row-level text for support, risk/reverse, off-topic/filtered, and missing evidence, explicitly show whether the evidence strengthened, weakened, mixed, or stayed unchanged, and list added, removed, and resolved evidence rows. The research decision board translates the evidence state into workflow verdicts such as continue research, needs more evidence, pause watch, or blocked, and must include copyable next commands such as `lad research run --force`, `lad research review --verdict ...`, or `lad research memo` as appropriate. News and discovery evidence must not enter support evidence by count alone; headlines and summaries must pass topic-relevance and evidence-direction checks first. Unmatched rows should move to off-topic/filtered evidence, topic-matched rows with negative direction words such as falls/cuts/weak/slowdown/pressure should become reverse evidence, and topic-matched but direction-unclear rows should be marked as pending news. The consistency conclusion must stay pending human review until a dedicated consistency-analysis engine exists. The research decision board, analyst readout, and research hypothesis panel may only provide research workflow state and next research actions; they must not provide buy, sell, hold, allocation, target-price, or expected-return advice.
- `lad research pending-evidence` is the pending row-level evidence review queue. It must read the latest `research-verification-*.json` artifact for each research task, collect only `新闻待判定` rows, skip rows already covered by `research_evidence_reviews`, and display the task, research question, evidence text, source artifact, a system-suggested evidence direction, the suggestion reason, and a prefilled `lad research evidence-review` command. The queue must not make beginners choose from raw placeholders such as `<support|reverse|irrelevant>` without guidance. It must support `--symbol` and `--name` filters so the command printed by `lad research verify` can open the relevant task queue directly. It is a research workflow queue and must not present pending evidence as buy/sell candidates.
- `lad research evidence-review` is the row-level evidence-direction review entrypoint. It must record a news headline or evidence text fragment as `support`, `reverse`, or `irrelevant`, writing both the `research_evidence_reviews` SQLite row and `research-evidence-review-*.json`. Later `lad research verify` runs must read these reviews and reclassify matching evidence into support, risk/reverse, or irrelevant/excluded paths so "direction pending" news can be resolved through an auditable workflow. After recording a row-level evidence review, the CLI must print next workbench commands for rerunning `lad research verify`, continuing the filtered `lad research pending-evidence` queue, and viewing `lad research evidence-reviews`. This command may only record evidence direction and notes; it must not provide buy, sell, hold, allocation, target-price, or expected-return advice.
- `lad research evidence-reviews` is the row-level evidence review history entrypoint. It must read records from the SQLite `research_evidence_reviews` table and show each reviewed evidence fragment, direction label, note, and review artifact. It is for auditing evidence classification and must not present historical evidence reviews as a buy/sell list.
- `lad research memo` is the LLM second-stage research memo entrypoint for one research task. It must run the same drilldown verification first, then send the evidence board, verification checks, evidence-change summary, and research decision board to the configured LLM and write a collision-safe `research-memo-*.json` artifact. The memo is an analyst work order, not a static essay; it may only contain a summary, working hypothesis, evidence reading, support points, skeptic review, falsification checks, missing evidence, next data requests, and next research steps. It must not contain buy, sell, hold, allocation, target-price, expected-return, position-sizing, or trading-instruction language. After a memo is generated, the CLI must keep the workbench moving by following the research decision board's suggested verdict when printing next commands: weak evidence should route to `lad research run --force` and `lad research review --verdict needs_more_evidence`, while stronger evidence can route to human consistency review. It must also offer inspecting `lad research data-requests`, rerunning `lad research verify`, and inspecting `lad research memos` history instead of ending at a static report. Missing LLM configuration, request failure, malformed JSON, missing required fields, or investment-advice language must fail the command.
- The TUI research detail view must expose the same `生成研究备忘录` action, show an LLM loading state, and reuse the same failure boundaries and non-advice boundary as `lad research memo`. After a memo is generated, the TUI must not stop at static output or a single return action; it must keep selectable actions for recording a research review, rerunning drilldown verification, viewing research data requests, viewing memo history, and returning to the research task list.
- `lad research review` is the review-record entrypoint for one research task. It must run the same drilldown verification first, then write `research-review-*.json` and a SQLite `research_reviews` row. After recording a verdict, the CLI must print verdict-specific next workbench commands, such as `lad research memo` and rerun verification for `continue_research`, or `lad research run --force` and rerun verification for `needs_more_evidence`. Review verdicts may only express research workflow state: continue research, needs more evidence, pause watch, or blocked; they must not express buy, sell, hold, allocation, target-price, or expected-return advice.
- `lad research reviews` is the research review history entrypoint. It must read records from the SQLite `research_reviews` table and show the review verdict, note, evidence counts, review artifact, and linked drilldown verification artifact. It is for reviewing the research process and must not present historical reviews as a buy/sell list.
- `lad research memos` is the research memo history entrypoint. It must read records from the SQLite `research_memos` table and show the summary, confidence, support/skeptic/missing/next-step counts, memo artifact, and linked drilldown verification artifact. It is for reviewing the research process and must not present historical memos as a buy/sell list.
- `lad research data-requests` is the research data-request queue. It must read `next_data_requests` from the latest memo per task first; when a task has no memo yet, it must fall back to the latest drilldown verification artifact's `hypothesis_panel.next_data_requests` so verification can create actionable data requests before the LLM memo stage. It must show each request, suggested data-collection commands, source memo or source verification artifact, and linked drilldown verification artifact. Fulfilled request IDs with completed, cached, or manual-required records must be skipped by the pending queue so completed work does not keep resurfacing. Fund/ETF metadata requests must point to `lad data guide fund` and `lad data set fund --from-file`; market requests must point to `lad data pull market`; news requests must point to `lad data pull news`; applicable US filing requests must point to `lad data pull filings`; explicit US-company requests for revenue, net income, operating cash flow, XBRL, or a financial snapshot must also point to `lad data pull financials`; every request must end with a `lad research verify` rerun command. `lad research run-data-request --request N` must execute the supported actions for one request, write a fulfillment artifact/SQLite record, and support generating fund metadata templates, refreshing market/news/filing/financial-snapshot data, and rerunning drilldown verification only after local data changed. Provider failures must preserve the raw auditable message and also print a beginner-readable `数据源诊断` line for network permission, timeout, authentication, or access-denied failures. Template-filling or manual-source requests must be marked as manual instead of pretending they are complete. Data requests without a supported automatic provider must explicitly say they need a manual source or future plugin support instead of pretending they are covered. It is only an evidence-collection queue and must not present data requests as buy/sell candidates.
- `lad research data-request-diagnose --request N` must only read the latest failed fulfillment for the selected pending request. It must show failed actions, a safe user-facing diagnosis, recovery steps, the failure artifact path, and the exact `run-data-request` retry command. It must not send provider requests, print credentials, or retry automatically. A failed data-request row in `lad research next` must point here first; selecting it through `run-next` must return `manual_required` and stop a batch before any retry.
- `lad research provider-backlog` must extract unsupported manual-source requests from the same research data-request queue and classify them into provider/plugin backlog items. Each item must show the research task, market, request text, data domain, plugin type, current coverage gap, candidate source shape, suggested `lad data set metric` command, source memo or source verification artifact, linked verification artifact, and next data-integration step. This command is for data-capability planning only; it must not present provider gaps as investment opportunities or trading advice.
- `lad research next` must aggregate pending evidence reviews, provider backlog items, executable research data requests, opportunity-radar drilldown targets, and research-task next commands into one prioritized queue. Its `--limit` must drive both the displayed queue size and workbench scan depth so candidates already inserted by radar or research-chain actions are not hidden by a smaller default workbench limit; research commands produced from that expanded scan must also carry the required `--limit` so copying them reaches the same task. Direct-symbol candidates must take precedence over proxy-theme matches for the same symbol, so selecting `512480.SH` does not jump into the broader semiconductor theme unless no direct candidate exists. When an executable research data request already covers a workbench candidate by symbol or task name, `research next` must suppress that candidate's generic research follow-up and keep the concrete data-request action instead. If the latest fulfillment for that data request is `failed`, `research next` must turn the queue item into a `数据源诊断` action with a repair-and-retry title, a beginner-readable failure diagnosis, the same retry command, and the failed fulfillment artifact as its source. Workbench candidates whose radar-triggered research chain ran within the last 24 hours must be promoted as `雷达跟进` actions before older data requests and ordinary research tasks, so the next evidence-board verification remains directly reachable. Opportunity-radar actions must turn each drilldown target's evidence gap into a topic-news refresh or continue-research command so users do not have to manually choose commands after reading the radar output. Each item must show the action area, user-facing action title, why it matters, source artifact, and a copyable command. This is the default beginner-facing "what should I do next" entrypoint and must not contain buy/sell/hold, allocation, target-price, or position-sizing language. `lad research run-next --action N` must execute only one whitelisted automatic action; `lad research run-next --count N` may repeatedly execute the current first whitelist action, but it must rebuild the queue after each step. When an opportunity-radar topic-news action returns `no-data`, it must write an auditable SQLite cooldown record, hide that exact action while the cooldown is fresh, and continue the batch if the rebuilt queue has advanced. When topic news returns evidence or a valid cache hit, the same radar target must become a `lad research run` follow-up instead of repeating the same news pull; after that follow-up runs, it must leave the queue. The batch must stop on `failed`, manual handoff, or an unchanged first command. The first supported actions are pending-evidence review, executable research data requests, opportunity-radar topic-news refresh, and radar-triggered research-task refresh. It must not execute arbitrary queue command strings through a shell. Pending-evidence review may only record evidence direction and print a rerun-verification command; it must not generate investment judgment. Research data requests may only call the existing `run-data-request` fulfillment boundary, must preserve manual-required steps, write a fulfillment record, leave the pending queue after completion/manual handoff, and must not rerun verification after zero-row pulls. After execution it must print the evidence-refresh result; it may print the next verification command only after evidence was collected, an evidence review was recorded, or a valid cache was hit, while `no-data` and `failed` must not advance research conclusions. If a radar-triggered research task is not yet in the research queue, it must be inserted as a local research candidate before the research chain runs.
- `lad data set metric` must write source-backed local research indicators into `research-metrics.json` for gaps such as market breadth, volatility metrics, fund flows, and sector performance. It must require a source URL, preserve symbol/domain/name/value/as-of/note/provider fields, and feed those metrics into research packets, verification checks, evidence boards, and task detail panels as supplemental evidence. It must not invent values or present metrics as investment advice.
- `lad data set news` must write a manually vetted, source-backed news record into `news-events.json`. It must require symbol, headline, summary, and a valid `http(s)` source URL, merge the record without deleting earlier cache rows, and never fetch a provider or infer facts. When automatic topic-news refresh returned rows but no topic evidence, the request and next-action queues must offer this explicit manual handoff rather than repeating the same refresh. The TUI must expose the same handoff as a three-field form and write only after an explicit save action; then it may offer a rerun verification action. The original missing discovery ID remains auditable; a manual record may satisfy the current evidence gate only when it passes the existing task-topic, market, and asset-context checks. It must not present the record as investment advice.
- `lad data set filing` must write a manually checked filing or document summary into `filings.json`. It must require the research symbol, company, form, filing date, checked summary, and a valid `http(s)` source URL. Filing-body, Form 4, and insider-trading-document requests must route to this manual-file-evidence handoff rather than a generic metric/provider backlog. Its rows must merge with prior manual and SEC rows, survive subsequent SEC refreshes, and match a direct research task by symbol before any name fallback. The TUI must expose company, form, date, summary, and URL inputs with explicit save, then offer rerun verification. It must not present the record as investment advice.
- After a manual news or filing record uniquely matches an open manual handoff, the system must write a local `manual_required` fulfillment record and remove that request from the pending data-request and next-action queues. This acknowledgement only records that a human supplied an auditable source; it must not assert that the source supports the hypothesis, and rerun verification remains required.
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
- A zero-row market result is `no_data` for one hour. While fresh it returns the prior sanitized diagnostic and skips network retries; `--force` bypasses the cooldown.
- A blocked research task that matches a fresh market `no_data` entry must route its next command to `lad data health`, not a default forced refresh. The raw diagnostic remains in the artifact, and `--force` stays available only as an explicit retry.
- `--force` must bypass freshness and session checks.

News cache uses a basic TTL policy:

- The default freshness window is 1 hour.
- While fresh, local `news-events.json` is reused so discovery and manual drilldowns do not repeatedly consume provider quota.
- A news request with `--symbols` is reusable only when the cache entry has more than 0 rows and `news-events.json` covers the requested symbols; global market-news rows must not be reported as the cache result for a specific symbol.
- If a symbol + query cache entry has 0 rows and is still fresh, the default path returns 0 rows and an explicit no-data state to avoid repeatedly consuming provider quota; only `--force` retries. A 0-row result must not advance to a research-verification command.
- `--force` must bypass news freshness.

SEC XBRL financial snapshots use a 24-hour TTL. A matching unexpired `financials.json` entry must be reused; `--force` must bypass the TTL. The cache must be recorded as the `financials` layer in `cache_entries` for CLI/TUI auditability.

Cache state is stored in the `cache_entries` table inside `.alphadesk/research.sqlite3`, including layer, cache_key, provider, artifact_path, created_at, expires_at, ttl_seconds, market, session_state, row_count, and is_final_for_session.

## No-Spin Progress Gates

Every future development round must pass these gates before it counts as progress:

- Capability gate: the round must reduce one user step, improve evidence reliability, or make the workbench easier to understand; copy tweaks, menu shuffling, or repeated output alone do not count.
- Evidence gate: data and research actions must distinguish `completed`, `cached`, `no-data`, and `failed`; `no-data` and `failed` must not print follow-up verification commands.
- Command-continuity gate: next commands printed by the workbench must copy-run back into the same research task; expanded scan limits, direct-symbol precedence, and proxy-theme matching must have regression coverage.
- Verification gate: each round must include automated tests and at least one real local command validation that exercises the discovery / next-action / run / verify chain, not just unit tests.
- Delivery gate: each stage must be committed and pushed, with commit trailers describing constraints, rejected alternatives, tests, and remaining risks.
- Stop condition: if real command output does not move the workbench closer to `discover signal -> fill evidence -> verify evidence -> produce next research action`, fix that blocker before adding another feature layer.

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
