import asyncio
import json
from pathlib import Path

from textual.widgets import Input

from lychee_alphadesk.core.action_queue import ActionQueueItem
from lychee_alphadesk.core.research_db import write_research_memo_record
from lychee_alphadesk.core.research_requests import list_research_data_requests
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


def test_tui_manual_filing_entry_writes_audited_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        write_research_memo_record(
            output_dir=tmp_path,
            memo_id="research-memo:nvda:manual-filing",
            created_at="2026-07-16T09:05:00+00:00",
            display_name="NVIDIA",
            symbol="NVDA",
            market="US",
            confidence="medium",
            summary="需要核验 Form 4 内容。",
            support_count=1,
            skeptic_count=1,
            missing_count=1,
            next_step_count=1,
            memo_path=tmp_path / "research" / "research-memo-nvda.json",
            verification_path=tmp_path / "research" / "research-verification-nvda.json",
            payload={
                "memo": {
                    "next_data_requests": [
                        "复核 2026-07-06 的 Form 4 正文，确认其是否仅为内部人交易披露。"
                    ]
                }
            },
        )
        app = AlphaDeskApp(output_dir=tmp_path)
        item = ActionQueueItem(
            priority=25,
            area="人工文件证据",
            title="补充已核验文件: NVIDIA",
            detail="请只记录已核验的公告或表单关键事实及原始链接。",
            command=(
                "lychee data set filing --symbol NVDA --company NVIDIA --form \"4\" "
                '--date YYYY-MM-DD --summary "已核验的关键事实" '
                '--source-url "https://..."'
            ),
            source="research-memo-nvda.json",
        )
        async with app.run_test() as pilot:
            app.action_queue_items = [item]
            await app._run_next_action_item("next_action_item:0")
            await pilot.pause()
            app.query_one("#manual-filing-date", Input).value = "2026-07-06"
            app.query_one("#manual-filing-summary", Input).value = (
                "已核验：该 Form 4 为内部人交易披露。"
            )
            app.query_one("#manual-filing-source-url", Input).value = (
                "https://www.sec.gov/Archives/edgar/data/1045810/form4.html"
            )

            await app._save_manual_filing_entry()
            await pilot.pause()

            cache = json.loads(
                (tmp_path / "data" / "filings.json").read_text(encoding="utf-8")
            )
            assert cache["rows"][0]["symbol"] == "NVDA"
            assert cache["rows"][0]["form"] == "4"
            assert list_research_data_requests(tmp_path, symbol="NVDA") == []
            assert app.query_one("#manual-filing-followup-menu")

    asyncio.run(scenario())
