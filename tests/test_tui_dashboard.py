import asyncio
import json
from pathlib import Path

from textual.widgets import Static

from lychee_alphadesk.tui.app import AlphaDeskApp


def test_dashboard_shows_cached_live_data_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "alpha_vantage",
                "rows": [
                    {
                        "symbol": "AAPL",
                        "date": "2026-07-02",
                        "close": 214.33,
                        "volume": 51230000,
                        "currency": "USD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def run_case() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            dashboard = app.query_one("#dashboard-summary", Static)
            text = str(dashboard.content)
            assert "Live cache" in text
            assert "AAPL" in text
            assert "214.33" in text

    asyncio.run(run_case())
