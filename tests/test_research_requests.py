from pathlib import Path
from types import SimpleNamespace

from lychee_alphadesk.core.live_data import FundMetadataGuide, PullResult
from lychee_alphadesk.core.research_db import write_research_memo_record
from lychee_alphadesk.core.research_requests import (
    fulfill_research_data_request,
    list_provider_backlog_items,
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


def test_fulfill_research_data_request_runs_market_pull_and_verify(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["请提供 QQQ 与更宽市场基准的行情、成交量和相对强弱对比。"],
    )
    market_calls: list[dict[str, object]] = []
    verify_calls: list[dict[str, object]] = []

    def fake_pull_market(**kwargs: object) -> PullResult:
        market_calls.append(kwargs)
        return PullResult("market", "auto", 1, tmp_path / "data" / "market.json", [])

    def fake_verify(**kwargs: object) -> object:
        verify_calls.append(kwargs)
        return SimpleNamespace(artifact_path=tmp_path / "research" / "verify.json")

    result = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
        pull_market=fake_pull_market,
        verify_task=fake_verify,
    )

    assert market_calls == [
        {
            "symbols": ["QQQ"],
            "output_dir": tmp_path,
            "provider_id": "auto",
            "force": True,
        }
    ]
    assert verify_calls == [{"output_dir": tmp_path, "symbol": "QQQ", "name": None}]
    assert [execution.action_type for execution in result.executions] == [
        "market",
        "verify",
    ]
    assert [execution.status for execution in result.executions] == [
        "completed",
        "completed",
    ]


def test_fulfill_research_data_request_prepares_fund_template_without_verify(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["请补齐 QQQ 的基金资料：跟踪指数、费用率、成分摘要和资料来源 URL。"],
    )
    guide_calls: list[dict[str, object]] = []
    verify_calls: list[dict[str, object]] = []

    def fake_write_fund_guide(**kwargs: object) -> FundMetadataGuide:
        guide_calls.append(kwargs)
        return FundMetadataGuide(
            symbol="QQQ",
            display_name="Invesco QQQ Trust",
            market="US",
            required_fields=["tracking_index"],
            suggested_sources=["基金公司产品页"],
            write_command="lychee data set fund ...",
            apply_command="lychee data set fund --from-file guide.json",
            output_path=tmp_path / "data" / "fund-metadata-guide-QQQ.json",
        )

    def fake_verify(**kwargs: object) -> object:
        verify_calls.append(kwargs)
        return SimpleNamespace(artifact_path=tmp_path / "research" / "verify.json")

    result = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
        write_fund_guide=fake_write_fund_guide,
        verify_task=fake_verify,
    )

    assert guide_calls == [
        {
            "output_dir": tmp_path,
            "symbol": "QQQ",
            "display_name": "Invesco QQQ Trust",
            "market": "US",
        }
    ]
    assert verify_calls == []
    assert [execution.action_type for execution in result.executions] == [
        "fund_metadata_guide",
        "fund_metadata_import",
        "verify",
    ]
    assert [execution.status for execution in result.executions] == [
        "completed",
        "manual_required",
        "skipped",
    ]


def test_fulfill_research_data_request_skips_manual_source_only_request(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["请补充纳斯达克 100 成分股上涨家数和等权指数对比。"],
    )
    verify_calls: list[dict[str, object]] = []

    def fake_verify(**kwargs: object) -> object:
        verify_calls.append(kwargs)
        return SimpleNamespace(artifact_path=tmp_path / "research" / "verify.json")

    result = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
        verify_task=fake_verify,
    )

    assert verify_calls == []
    assert [execution.action_type for execution in result.executions] == ["verify"]
    assert result.executions[0].status == "skipped"
    assert "人工补来源" in result.executions[0].message


def test_provider_backlog_items_classify_manual_data_requests(tmp_path: Path) -> None:
    _write_request_memo(
        tmp_path,
        [
            "请补充纳斯达克 100 成分股上涨家数和等权指数对比。",
            "请补齐 QQQ 的基金资料：跟踪指数、费用率、成分摘要和资料来源 URL。",
            "请补充科技股波动率极端化新闻所引用的具体指标、时间窗口和历史分位背景。",
        ],
    )

    backlog = list_provider_backlog_items(tmp_path, symbol="QQQ")

    assert len(backlog) == 2
    item = backlog[0]
    assert item.display_name == "Invesco QQQ Trust"
    assert item.symbol == "QQQ"
    assert item.data_domain == "市场广度"
    assert "成分股上涨家数" in item.request_text
    assert "当前 provider 只能补行情、新闻、公告和基金资料" in item.coverage_gap
    assert item.plugin_type == "market_breadth"
    assert item.suggested_provider_examples == [
        "指数成分数据源",
        "等权指数或市场广度数据源",
        "行业/子行业表现数据源",
    ]
    assert item.next_step == "接入可审计的市场广度 provider 后，再重新运行研究数据请求。"
    assert item.memo_path.endswith("research-memo-test.json")
    assert item.verification_path.endswith("research-verification-test.json")

    volatility_item = backlog[1]
    assert volatility_item.data_domain == "波动率指标"
    assert volatility_item.plugin_type == "volatility_metrics"
    assert "期权或风险情绪指标" in volatility_item.coverage_gap
    assert volatility_item.suggested_provider_examples == [
        "波动率指数数据源",
        "期权链或隐含波动率数据源",
        "风险情绪指标数据源",
    ]


def _write_request_memo(tmp_path: Path, requests: list[str]) -> None:
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
        missing_count=len(requests),
        next_step_count=len(requests),
        memo_path=tmp_path / "research" / "research-memo-test.json",
        verification_path=tmp_path / "research" / "research-verification-test.json",
        payload={"memo": {"next_data_requests": requests}},
    )
