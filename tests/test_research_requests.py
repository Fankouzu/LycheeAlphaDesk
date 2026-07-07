from pathlib import Path

from lychee_alphadesk.core.research_db import write_research_memo_record
from lychee_alphadesk.core.research_requests import (
    list_research_data_requests,
    research_data_request_needs_manual_source,
)


def test_research_data_requests_map_memo_requests_to_precise_commands(
    tmp_path: Path,
) -> None:
    write_research_memo_record(
        output_dir=tmp_path,
        memo_id="research-memo:2026-07-05T10:02:00+00:00",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="low",
        summary="QQQ 仍需补齐数据。",
        support_count=1,
        skeptic_count=1,
        missing_count=3,
        next_step_count=3,
        memo_path=tmp_path / "research" / "research-memo-test.json",
        verification_path=tmp_path / "research" / "research-verification-test.json",
        payload={
            "memo": {
                "next_data_requests": [
                    "请补齐 QQQ 的基金资料：跟踪指数、费用率、成分摘要和资料来源 URL。",
                    "请提供证据板中 6 条新闻的原文链接、发布日期、来源、标题和摘要。",
                    "请补充纳斯达克 100 成分股上涨家数和等权指数对比。",
                ]
            }
        },
    )

    requests = list_research_data_requests(tmp_path, symbol="QQQ")

    assert len(requests) == 3
    fund_commands = requests[0].suggested_commands
    assert any(command.startswith("lychee data guide fund") for command in fund_commands)
    assert not any("data pull news" in command for command in fund_commands)

    news_commands = requests[1].suggested_commands
    assert any("lychee data pull news --symbols QQQ" in command for command in news_commands)

    breadth_commands = requests[2].suggested_commands
    assert not any("data guide fund" in command for command in breadth_commands)
    assert breadth_commands == ["lychee research verify --symbol QQQ"]
    assert research_data_request_needs_manual_source(requests[2])
