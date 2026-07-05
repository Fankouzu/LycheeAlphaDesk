# Today Discovery Engine Design

Date: 2026-07-05
Status: approved for documentation; ready for implementation planning

## User Problem

Lychee AlphaDesk should not assume that a beginner already knows which symbols to watch. A symbol-first workflow asks the user to enter `AAPL` or `TSLA` before the system has helped them understand what is happening in the market.

The primary product entry point must therefore become discovery-first:

```text
broad market context -> news and event scan -> LLM analysis -> watch candidates -> drilldown data
```

The system should help the user discover what is worth researching today across US stocks, Hong Kong stocks, and China A-shares. It must not present those candidates as buy or sell recommendations.

## Chosen Approach

Use a hybrid discovery workflow.

The first discovery pass combines:

- Market overview: indexes, ETFs, sector moves, volume, breadth, and unusual movement.
- News scan: global financial news, regional market news, company news, and industry themes.
- Company events: filings, announcements, earnings events, guidance, IPO/new-share opportunities, and material notices.
- LLM synthesis: theme extraction, candidate mapping, evidence summaries, risk flags, and suggested next data pulls.

This approach is better than news-only discovery because it reduces headline noise with market context. It is better than market-only discovery because it explains why a move may matter.

## Product Flow

The `lychee` TUI home screen should make `Today Discovery` the first and primary action.

Beginner flow:

1. User starts `lychee`.
2. User selects `Today Discovery`.
3. The engine fetches broad US, HK, and CN market context without asking for symbols.
4. The engine fetches market-wide and industry news.
5. The engine fetches available company events and filings.
6. The LLM summarizes market themes and proposes watch candidates.
7. The TUI shows themes and candidates with evidence, risk flags, and next actions.
8. User selects a theme or candidate to pull detailed prices, news, filings, financials, or peer comparisons.

Symbol entry remains useful, but only as a drilldown tool for users who already know what they want to inspect.

## Market Coverage

The discovery engine must cover all three markets from the beginning:

| Market | First-pass data | Candidate examples |
| --- | --- | --- |
| US | Major indexes, ETFs, financial news, SEC filings, large-cap watch universe | Stocks, ETFs, sectors, earnings/filing events |
| HK | Hang Seng family indexes, HK market news, HKEX announcements, HKD/rate context | Stocks, sectors, China-linked themes, IPO/new-share events |
| CN A-shares | Broad indexes, sector boards, CNINFO-style announcements, earnings/forecast notices, IPO/new-share events | A-share stocks, sector themes, policy-linked themes |

The first implementation can use partial provider coverage per market, but the report shape must always identify which market evidence is present, missing, or degraded.

## Output Model

The engine should produce a `DiscoveryReport` with structured fields:

- `created_at`
- `markets`
- `sources`
- `themes`
- `candidates`
- `warnings`
- `next_actions`

Each theme should contain:

- name
- market coverage
- summary
- evidence references
- related sectors
- risk flags
- confidence level

Each candidate should contain:

- display name
- optional symbol
- market
- asset type
- related theme
- why watch
- evidence references
- risk flags
- suggested next data pulls
- confidence level

Candidate language must use "watch", "research", "investigate", and "drill down". It must not use "buy", "sell", "target price", or "guaranteed return" as product-level recommendations.

## LLM Role

The LLM is an analyst assistant, not an investment adviser.

Allowed LLM tasks:

- summarize market context
- cluster news into themes
- extract companies, sectors, and ETFs mentioned in evidence
- map themes across US, HK, and CN markets
- list risk flags and missing evidence
- suggest which data should be pulled next
- write a research memo draft

Disallowed LLM tasks:

- direct buy/sell calls
- automatic portfolio allocation
- live trading instructions
- unsupported claims without source evidence
- hiding uncertainty or missing data

If no LLM provider is configured, the engine must fail with setup guidance. It must not silently generate fallback analysis because that would make a beginner think AI synthesis has happened when it has not.

When an LLM provider is configured, the engine must call the OpenAI-compatible `/chat/completions` endpoint and require a valid JSON object. API failures, malformed responses, missing required fields, or buy/sell-style recommendation language must fail the command instead of writing a discovery cache.

## TUI Requirements

Human-facing discovery screens must follow the project interaction standard:

- Use keyboard navigation with Up/Down/Left/Right/Tab, Enter, and Esc.
- Do not use number or letter keys as menu selectors.
- Show beginner-friendly display names, not internal variable names.
- Keep symbol input out of the first step.
- Let users select themes or candidates from a menu before drilling down.

The home screen should prioritize:

1. Today Discovery
2. Review Watch Candidates
3. Data Health
4. Provider Setup Guidance
5. Manual Symbol Drilldown
6. Snapshot / Audit

## Data Source Strategy

Provider integrations should remain optional and pluggable.

No-key or low-friction providers should be preferred first:

- yfinance for global price/index/ETF research demos.
- AkShare for China and Hong Kong public market data.
- GDELT for broad global news.
- SEC EDGAR for US filings.
- HKMA Open API for Hong Kong macro context.

Key-based or licensed providers remain optional:

- Tushare for structured China data.
- Alpha Vantage for prices and indicators.
- Finnhub, Marketaux, and NewsAPI for news and company-linked events.
- Licensed HKEX or exchange data only when a user explicitly needs it.

## Implementation Boundaries

The first code milestone should focus on a useful local loop:

1. Add `lychee discover today` as a non-interactive command for agents and scripts.
2. Add `Today Discovery` as the first TUI action.
3. Produce a local JSON cache under `.alphadesk/data/`.
4. Generate a readable terminal report from cached discovery data.
5. Fail clearly when the LLM provider is not configured or inactive.
6. Add tests for the report model, CLI command, and TUI menu ordering.

Do not add broker execution, automatic trading, high-frequency workflows, or portfolio recommendations in this milestone.

## Verification Plan

Documentation and implementation should be verified with:

- Markdown review for consistency across English and Chinese docs.
- Unit tests for discovery report serialization.
- CLI tests for `lychee discover today`.
- TUI tests confirming `Today Discovery` is the first action.
- Error-path tests with no LLM provider configured.
- Provider warning tests for missing or degraded market coverage.

## Open Follow-up

Implementation planning should decide the exact first provider set and whether the initial report is generated from live no-key providers, demo fixtures, or a hybrid of both. The product shape is settled: discovery comes before symbol entry.
