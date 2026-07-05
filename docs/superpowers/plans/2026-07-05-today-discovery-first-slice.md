# Today Discovery First Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the approved discovery-first product direction visible and runnable through a first `Today Discovery` CLI/TUI slice.

**Architecture:** Add a small discovery core that writes an auditable local JSON report only after LLM configuration is present. Wire it into a `lychee discover today` command and make the TUI home menu show `Today Discovery` as the first action. Keep live provider integrations out of this first slice; missing LLM configuration is an error, not a fallback path.

**Tech Stack:** Python 3.11+, Typer, Textual `OptionList`, Pydantic-free dataclasses for the first slice, pytest, ruff, mypy.

---

### File Structure

- Create `src/lychee_alphadesk/core/discovery.py`
  - Defines `DiscoveryTheme`, `DiscoveryCandidate`, `DiscoveryReport`, JSON serialization, LLM-required report generation, and cache writing.
- Modify `src/lychee_alphadesk/cli/app.py`
  - Adds `discover` command group and `discover today`.
  - Prints a compact terminal summary and cache path.
- Modify `src/lychee_alphadesk/tui/app.py`
  - Adds `Today Discovery` as the first action.
  - Runs the LLM-required discovery report and displays themes/candidates without asking for symbols.
  - Renames manual symbol actions so they are visibly drilldown tools.
- Modify `tests/test_cli.py`
  - Adds CLI test for `lychee discover today`.
- Modify `tests/test_tui_dashboard.py`
  - Updates first-menu test to expect `Today Discovery`.
  - Adds a TUI test that selecting the first action displays discovery output and writes a cache.
- Modify README and development spec if command status changes from planned to first-slice available.

### Task 1: Discovery Core

**Files:**
- Create: `src/lychee_alphadesk/core/discovery.py`
- Test: `tests/test_cli.py`

- [x] **Step 1: Write failing CLI test**

Add tests that invoke `discover today` with and without LLM configuration. The no-LLM case must fail and must not write a cache file.

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
uv run --no-editable pytest tests/test_cli.py::test_discover_today_requires_llm_configuration -q
```

Expected: fail because the command does not exist.

- [x] **Step 3: Implement minimal discovery core and CLI command**

Create dataclasses for the report, require an active LLM configuration, generate a starter report covering US, HK, and CN after that check passes, write `.alphadesk/data/discovery-today.json`, and print a compact summary.

- [x] **Step 4: Run focused CLI test**

Run:

```bash
uv run --no-editable pytest tests/test_cli.py::test_discover_today_command_writes_report_when_llm_configured -q
```

Expected: pass.

### Task 2: TUI Entry

**Files:**
- Modify: `src/lychee_alphadesk/tui/app.py`
- Test: `tests/test_tui_dashboard.py`

- [x] **Step 1: Update failing TUI tests**

Expect the first menu item to be `Today Discovery`. Add a test selecting it and asserting the status panel contains discovery themes and no symbol input.

- [x] **Step 2: Run focused TUI tests to verify failure**

Run:

```bash
uv run --no-editable pytest tests/test_tui_dashboard.py::test_dashboard_has_keyboard_action_menu tests/test_tui_dashboard.py::test_dashboard_today_discovery_action_writes_report -q
```

Expected: fail because the menu still starts with `Pull market prices` and no discovery action exists.

- [x] **Step 3: Implement TUI action**

Add `today_discovery` to `ActionId`, put it first in `OptionList`, call the discovery core, and display a concise report summary.

- [x] **Step 4: Run focused TUI tests**

Run:

```bash
uv run --no-editable pytest tests/test_tui_dashboard.py::test_dashboard_has_keyboard_action_menu tests/test_tui_dashboard.py::test_dashboard_today_discovery_action_writes_report -q
```

Expected: pass.

### Task 3: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/DEVELOPMENT_SPEC.md`
- Modify: `docs/DEVELOPMENT_SPEC.zh-CN.md`

- [x] **Step 1: Update docs**

Move `lychee discover today` from planned-only wording to first-slice wording. State that the first implementation requires LLM configuration and must fail instead of writing a fallback cache when LLM is missing.

- [x] **Step 2: Run full verification**

Run:

```bash
uv run --no-editable ruff check .
uv run --no-editable mypy src
uv run --no-editable pytest
```

Expected: all pass.

- [x] **Step 3: Commit**

Use a lore-style commit explaining why discovery is now visible in the product entry point.

### Self-Review

- Spec coverage: This first slice implements the visible entry point, local report cache, LLM-required error path, and non-advice language. It intentionally does not implement full live provider coverage yet.
- Placeholder scan: No TBD/TODO placeholders are used.
- Type consistency: The planned CLI/TUI both call the same discovery core and write the same cache file.
