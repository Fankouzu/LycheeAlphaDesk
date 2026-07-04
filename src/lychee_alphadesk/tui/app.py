from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static


class AlphaDeskApp(App[None]):
    TITLE = "Lychee AlphaDesk"
    SUB_TITLE = "demo research cockpit"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Today: No action\n\n"
            "Demo mode is active. Generate a richer report with:\n"
            "  lad report --demo\n\n"
            "Tabs planned for v0.1: Today, Portfolio, Policy, Providers, Audit."
        )
        yield Footer()


def run_tui() -> None:
    AlphaDeskApp().run()
