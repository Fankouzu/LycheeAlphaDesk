from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from lychee_alphadesk.core.data_engine import write_snapshot_json
from lychee_alphadesk.core.discovery import (
    DiscoveryLLMRequiredError,
    build_today_discovery_report,
    discovery_report_summary,
    write_discovery_report,
)
from lychee_alphadesk.core.live_data import (
    build_cached_data_snapshot,
    parse_symbols,
    pull_market_prices,
    pull_news_events,
    pull_sec_filings,
    run_cached_data_health,
)
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR

ActionId = Literal[
    "today_discovery",
    "pull_market",
    "pull_news",
    "pull_filings",
    "data_health",
    "write_snapshot",
    "refresh",
    "setup_help",
    "quit",
]


class AlphaDeskApp(App[None]):
    TITLE = "Lychee AlphaDesk"
    SUB_TITLE = "local investment workbench"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(self, output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
        super().__init__()
        self.output_dir = output_dir
        self.pending_action: ActionId | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._dashboard_summary(), id="dashboard-summary")
        yield Static("Actions", id="action-title")
        yield OptionList(
            Option("Today Discovery", id="today_discovery"),
            Option("Manual symbol prices", id="pull_market"),
            Option("Manual symbol news", id="pull_news"),
            Option("Manual SEC filings", id="pull_filings"),
            Option("Check data health", id="data_health"),
            Option("Write live snapshot", id="write_snapshot"),
            Option("Refresh dashboard", id="refresh"),
            Option("Setup guidance", id="setup_help"),
            Option("Quit", id="quit"),
            id="action-menu",
            markup=False,
        )
        yield Container(
            Static(
                "Use Up/Down/Tab to move, Enter to select, Esc to return.",
                id="action-status",
            ),
            id="action-panel",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def action_back(self) -> None:
        if self.pending_action is None:
            self.exit()
            return
        self.pending_action = None
        await self._replace_action_panel(
            Static(
                "Use Up/Down/Tab to move, Enter to select, Esc to return.",
                id="action-status",
            )
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        event.stop()
        action_id = event.option.id
        if action_id == "today_discovery":
            await self._show_today_discovery()
        elif action_id == "pull_market":
            await self._show_symbol_prompt(
                "pull_market",
                "Symbols for market prices, e.g. AAPL,TSLA,0700.HK",
            )
        elif action_id == "pull_news":
            await self._show_symbol_prompt("pull_news", "Symbols for news, e.g. AAPL,TSLA")
        elif action_id == "pull_filings":
            await self._show_symbol_prompt(
                "pull_filings",
                "US symbols for SEC filings, e.g. AAPL,TSLA",
            )
        elif action_id == "data_health":
            await self._show_data_health()
        elif action_id == "write_snapshot":
            await self._write_live_snapshot()
        elif action_id == "refresh":
            self._refresh_dashboard()
            self._set_status("Dashboard refreshed from local cache.")
        elif action_id == "setup_help":
            self._set_status(
                "Run `lychee setup` in your terminal to configure provider keys."
            )
        elif action_id == "quit":
            self.exit()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        symbols = parse_symbols(event.value)
        if not symbols:
            self._set_status("No symbols entered.")
            return
        await self._run_symbol_action(symbols)

    def _dashboard_summary(self) -> str:
        snapshot = build_cached_data_snapshot(self.output_dir)
        lines = [
            "Live cache",
            f"Prices: {snapshot.counts['prices']}",
            f"News events: {snapshot.counts['news_events']}",
            f"Filings: {snapshot.counts['filings']}",
        ]
        if snapshot.provider_names:
            lines.append(f"Providers: {', '.join(snapshot.provider_names)}")
        if snapshot.prices:
            lines.append("")
            lines.append("Latest prices")
            for price in snapshot.prices[:8]:
                lines.append(
                    f"{price.symbol:<10} {price.close:.2f} {price.currency} "
                    f"{price.date}"
                )
        else:
            lines.append("")
            lines.append("No live cache yet. Start with:")
            lines.append("  lychee discover today")
        return "\n".join(lines)

    async def _show_symbol_prompt(self, action: ActionId, placeholder: str) -> None:
        self.pending_action = action
        await self._replace_action_panel(
            Static(
                "Enter comma-separated symbols, then press Enter.",
                id="action-status",
            ),
            Input(placeholder=placeholder, id="symbols-input"),
        )
        self.set_focus(self.query_one("#symbols-input", Input))

    async def _show_today_discovery(self) -> None:
        try:
            report = build_today_discovery_report()
        except DiscoveryLLMRequiredError as error:
            await self._replace_action_panel(
                Static(f"Action failed: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return
        output_path = write_discovery_report(report, self.output_dir)
        await self._replace_action_panel(
            Static(
                discovery_report_summary(report, output_path),
                id="action-status",
            )
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _run_symbol_action(self, symbols: list[str]) -> None:
        action = self.pending_action
        if action is None:
            self._set_status("No action is active.")
            return

        try:
            if action == "pull_market":
                result = pull_market_prices(symbols=symbols, output_dir=self.output_dir)
                label = "market prices"
            elif action == "pull_news":
                result = pull_news_events(symbols=symbols, output_dir=self.output_dir)
                label = "news events"
            elif action == "pull_filings":
                result = pull_sec_filings(symbols=symbols, output_dir=self.output_dir)
                label = "filings"
            else:
                self._set_status("This action does not accept symbols.")
                return
        except (RuntimeError, ValueError) as error:
            await self._replace_action_panel(
                Static(f"Action failed: {error}", id="action-status")
            )
            self.set_focus(self.query_one("#action-menu", OptionList))
            return

        lines = [
            f"Pulled {label}: {result.count}",
            f"Provider: {result.provider}",
            f"Cache: {result.output_path}",
        ]
        for warning in result.warnings:
            lines.append(f"WARNING: {warning}")
        self.pending_action = None
        self._refresh_dashboard()
        await self._replace_action_panel(Static("\n".join(lines), id="action-status"))
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _show_data_health(self) -> None:
        checks = run_cached_data_health(self.output_dir)
        lines = [
            "Data health",
            *[
                f"{check.status.upper():<7} {check.name} - {check.message}"
                for check in checks
            ],
        ]
        await self._replace_action_panel(Static("\n".join(lines), id="action-status"))
        self.set_focus(self.query_one("#action-menu", OptionList))

    async def _write_live_snapshot(self) -> None:
        snapshot = build_cached_data_snapshot(self.output_dir)
        output_path = write_snapshot_json(snapshot, self.output_dir)
        self._refresh_dashboard()
        await self._replace_action_panel(
            Static(f"Live snapshot written: {output_path}", id="action-status")
        )
        self.set_focus(self.query_one("#action-menu", OptionList))

    def _refresh_dashboard(self) -> None:
        self.query_one("#dashboard-summary", Static).update(self._dashboard_summary())

    async def _replace_action_panel(self, *widgets: Widget) -> None:
        panel = self.query_one("#action-panel", Container)
        await panel.remove_children()
        await panel.mount(*widgets)

    def _set_status(self, message: str) -> None:
        status = self.query_one("#action-status", Static)
        status.update(message)


def run_tui() -> None:
    AlphaDeskApp().run()
