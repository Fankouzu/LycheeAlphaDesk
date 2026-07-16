import asyncio
import json
from pathlib import Path

from textual.widgets import Input

from lychee_alphadesk.core.action_queue import ActionQueueItem
from lychee_alphadesk.tui.app import AlphaDeskApp


def test_tui_manual_news_entry_writes_audited_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = AlphaDeskApp(output_dir=tmp_path)
        item = ActionQueueItem(
            priority=25,
            area="人工证据",
            title="补充可审计来源: Tencent",
            detail="自动新闻已刷新但未形成主题证据。",
            command=(
                "lychee data set news --symbol 0700.HK --headline \"已核验标题\" "
                "--summary \"与研究问题有关的关键事实\" "
                "--source-url \"https://...\""
            ),
            source="research-verification-test.json",
        )
        async with app.run_test() as pilot:
            app.action_queue_items = [item]
            await app._run_next_action_item("next_action_item:0")
            await pilot.pause()
            app.query_one("#manual-news-headline", Input).value = "Tencent cloud update"
            app.query_one("#manual-news-summary", Input).value = (
                "Tencent disclosed a Hong Kong cloud update relevant to this research task."
            )
            app.query_one("#manual-news-source-url", Input).value = (
                "https://example.com/tencent-cloud"
            )

            await app._save_manual_news_entry()
            await pilot.pause()

            cache = json.loads(
                (tmp_path / "data" / "news-events.json").read_text(encoding="utf-8")
            )
            assert cache["rows"][0]["symbols"] == ["0700.HK"]
            assert cache["rows"][0]["source_url"] == "https://example.com/tencent-cloud"
            assert app.query_one("#manual-news-followup-menu")

    asyncio.run(scenario())
