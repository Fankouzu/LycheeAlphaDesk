from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static

from lychee_alphadesk.core.live_data import build_cached_data_snapshot
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR


class AlphaDeskApp(App[None]):
    TITLE = "Lychee AlphaDesk"
    SUB_TITLE = "demo research cockpit"

    def __init__(self, output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
        super().__init__()
        self.output_dir = output_dir

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._dashboard_summary(), id="dashboard-summary")
        yield Footer()

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
            lines.append("  lychee data pull market --symbols AAPL,TSLA")
        return "\n".join(lines)


def run_tui() -> None:
    AlphaDeskApp().run()
