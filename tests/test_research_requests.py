import json
from pathlib import Path
from types import SimpleNamespace

from lychee_alphadesk.core.live_data import (
    FundMetadataGuide,
    PullResult,
    write_research_metric_cache,
)
from lychee_alphadesk.core.research_db import (
    write_research_data_request_fulfillment_record,
    write_research_memo_record,
)
from lychee_alphadesk.core.research_requests import (
    acknowledge_manual_research_data_request,
    diagnose_research_data_request,
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
    assert fund_commands == [
        "lychee data pull fund-metadata --symbols QQQ --force",
        "lychee research verify --symbol QQQ",
    ]
    assert not any("data pull news" in command for command in fund_commands)

    news_commands = requests[1].suggested_commands
    assert any("lychee data pull news --symbols QQQ" in command for command in news_commands)

    breadth_commands = requests[2].suggested_commands
    assert not any("data guide fund" in command for command in breadth_commands)
    assert breadth_commands == [
        "lychee data pull breadth --symbols QQQ --force",
        "lychee research verify --symbol QQQ",
    ]
    assert not research_data_request_needs_manual_source(requests[2])


def test_research_data_requests_hide_qqq_fund_gap_after_official_cache(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["请补齐 QQQ 的基金资料：跟踪指数、费用率、成分摘要和资料来源 URL。"],
    )
    from lychee_alphadesk.core.live_data import write_fund_metadata_cache

    write_fund_metadata_cache(
        output_dir=tmp_path,
        symbol="QQQ",
        display_name="Invesco QQQ Trust",
        market="US",
        tracking_index="Nasdaq-100 Index",
        expense_ratio="0.18%",
        holdings_summary="持仓 108 个",
        source_url="https://www.invesco.com/qqq-etf/en/about.html",
        as_of="2026-07-16",
        provider="invesco_official",
    )

    assert list_research_data_requests(tmp_path, symbol="QQQ") == []


def test_research_data_request_routes_form4_content_to_manual_filing_evidence(
    tmp_path: Path,
) -> None:
    write_research_memo_record(
        output_dir=tmp_path,
        memo_id="research-memo:2026-07-16T09:05:00+00:00",
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

    requests = list_research_data_requests(tmp_path, symbol="NVDA")

    assert len(requests) == 1
    assert research_data_request_needs_manual_source(requests[0])
    assert requests[0].suggested_commands == [
        (
            "lychee data set filing --symbol NVDA --company NVIDIA --form \"4\" --date YYYY-MM-DD "
            '--summary "已核验的关键事实" --source-url "https://..."'
        ),
        "lychee research verify --symbol NVDA",
    ]
    assert [action.action_type for action in requests[0].suggested_actions] == [
        "manual_filing",
        "verify",
    ]
    assert list_provider_backlog_items(tmp_path, symbol="NVDA") == []


def test_manual_filing_acknowledgement_removes_completed_handoff_from_queue(
    tmp_path: Path,
) -> None:
    write_research_memo_record(
        output_dir=tmp_path,
        memo_id="research-memo:2026-07-16T09:05:00+00:00",
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

    result = acknowledge_manual_research_data_request(
        tmp_path,
        action_type="manual_filing",
        symbol="NVDA",
        form="4",
    )

    assert result is not None
    assert result.status == "manual_required"
    assert result.executions[0].action_type == "manual_filing"
    assert list_research_data_requests(tmp_path, symbol="NVDA") == []


def test_research_data_requests_include_verification_hypothesis_requests(
    tmp_path: Path,
) -> None:
    verification_path = _write_verification_with_hypothesis_requests(
        tmp_path,
        [
            "补齐最高优先级缺口: 数据缺口: 缺少 QQQ 本地行情缓存。",
            "复核最强反证来源: 反向证据: AI capex slows.",
            "执行工作台下一步命令: lychee research run --symbol QQQ --force",
        ],
    )

    requests = list_research_data_requests(tmp_path, symbol="QQQ")

    assert len(requests) == 2
    assert requests[0].request_id == (
        f"{verification_path.stem}:hypothesis-data-request:1"
    )
    assert requests[0].source_type == "verification"
    assert requests[0].display_name == "Invesco QQQ Trust"
    assert requests[0].symbol == "QQQ"
    assert requests[0].market == "US"
    assert requests[0].memo_path == ""
    assert requests[0].verification_path == str(verification_path)
    assert requests[0].suggested_commands == [
        "lychee data pull market --symbols QQQ --provider auto --force",
        "lychee research verify --symbol QQQ",
    ]
    assert requests[1].suggested_commands == ["lychee research verify --symbol QQQ"]


def test_verification_request_stops_repeating_news_after_completed_refresh(
    tmp_path: Path,
) -> None:
    verification_path = _write_verification_with_hypothesis_requests(
        tmp_path,
        ["补齐最高优先级缺口: 缺少可审计新闻证据，需先刷新个股新闻缓存。"],
    )
    fulfillment_path = tmp_path / "research" / "research-data-request-fulfillment.json"
    fulfillment_path.write_text("{}", encoding="utf-8")
    write_research_data_request_fulfillment_record(
        output_dir=tmp_path,
        fulfillment_id="research-data-request:QQQ:news",
        created_at="2026-07-05T10:01:00+00:00",
        request_id="research-verification:QQQ:news",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        status="completed",
        action_count=2,
        fulfillment_path=fulfillment_path,
        output_path=tmp_path / "data" / "news-events.json",
        payload={
            "executions": [
                {
                    "action_type": "news",
                    "status": "completed",
                    "count": 8,
                },
                {
                    "action_type": "verify",
                    "status": "completed",
                    "count": 1,
                    "output_path": str(verification_path),
                },
            ]
        },
    )

    requests = list_research_data_requests(tmp_path, symbol="QQQ")

    assert len(requests) == 1
    assert "不要重复刷新同一查询" in requests[0].request_text
    assert research_data_request_needs_manual_source(requests[0])
    assert [action.action_type for action in requests[0].suggested_actions] == [
        "manual_source",
        "verify",
    ]
    assert requests[0].suggested_commands == [
        (
            "lychee data set news --symbol QQQ --headline \"已核验标题\" "
            "--summary \"与研究问题有关的关键事实\" "
            "--source-url \"https://...\""
        ),
        "lychee research verify --symbol QQQ",
    ]
    backlog = list_provider_backlog_items(tmp_path, symbol="QQQ")
    assert len(backlog) == 1
    assert backlog[0].data_domain == "可审计主题新闻"
    assert backlog[0].plugin_type == "entity_news"
    assert backlog[0].suggested_commands == [
        (
            "lychee data set news --symbol QQQ --headline \"已核验标题\" "
            "--summary \"与研究问题有关的关键事实\" "
            "--source-url \"https://...\""
        )
    ]

    result = acknowledge_manual_research_data_request(
        tmp_path,
        action_type="manual_source",
        symbol="QQQ",
    )

    assert result is not None
    assert result.status == "manual_required"
    assert list_research_data_requests(tmp_path, symbol="QQQ") == []


def test_verification_request_uses_topic_exhaustion_state_without_retrying_news(
    tmp_path: Path,
) -> None:
    _write_verification_with_hypothesis_requests(
        tmp_path,
        ["补齐最高优先级缺口: 缺少可审计新闻证据，需先刷新个股新闻缓存。"],
        topic_news_exhausted=True,
    )

    requests = list_research_data_requests(tmp_path, symbol="QQQ")

    assert len(requests) == 1
    assert "不要重复刷新同一查询" in requests[0].request_text
    assert [action.action_type for action in requests[0].suggested_actions] == [
        "manual_source",
        "verify",
    ]
    assert not any(action.action_type == "news" for action in requests[0].suggested_actions)


def test_tencent_topic_exhaustion_offers_official_news_before_manual_handoff(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research" / "research-verification-tencent.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "created_at": "2026-07-05T10:00:00+00:00",
                "candidate": {
                    "display_name": "Tencent",
                    "symbol": "0700.HK",
                    "market": "HK",
                    "topic_news_exhausted": True,
                },
                "hypothesis_panel": {
                    "next_data_requests": ["补齐可审计新闻证据。"]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    requests = list_research_data_requests(tmp_path, symbol="0700.HK")

    assert [action.action_type for action in requests[0].suggested_actions] == [
        "news_official",
        "manual_source",
        "verify",
    ]
    assert requests[0].suggested_commands[0] == (
        "lychee data pull news --symbols 0700.HK "
        "--provider tencent_official --force"
    )


def test_research_data_requests_prefer_latest_memo_over_verification_hypothesis(
    tmp_path: Path,
) -> None:
    _write_verification_with_hypothesis_requests(
        tmp_path,
        ["补齐最高优先级缺口: 数据缺口: 缺少 QQQ 本地行情缓存。"],
    )
    _write_request_memo(
        tmp_path,
        ["请补齐 QQQ 的基金资料：跟踪指数、费用率、成分摘要和资料来源 URL。"],
    )

    requests = list_research_data_requests(tmp_path, symbol="QQQ")

    assert len(requests) == 1
    assert requests[0].source_type == "memo"
    assert requests[0].request_text.startswith("请补齐 QQQ 的基金资料")


def test_financial_fact_request_adds_sec_financial_snapshot_action(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["补齐营收、净利润和经营现金流的 SEC XBRL 财务快照。"],
        display_name="Apple Inc.",
        symbol="AAPL",
    )

    requests = list_research_data_requests(tmp_path, symbol="AAPL")

    assert len(requests) == 1
    assert [action.action_type for action in requests[0].suggested_actions] == [
        "filings",
        "financials",
        "verify",
    ]
    assert requests[0].suggested_commands == [
        "lychee data pull filings --symbols AAPL",
        "lychee data pull financials --symbols AAPL --force",
        "lychee research verify --symbol AAPL",
    ]


def test_financial_fact_request_skips_sec_snapshot_for_etf(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["补齐营收、净利润和经营现金流的 SEC XBRL 财务快照。"],
        asset_type="ETF",
    )

    requests = list_research_data_requests(tmp_path, symbol="QQQ")

    assert [action.action_type for action in requests[0].suggested_actions] == [
        "filings",
        "verify",
    ]


def test_hk_filing_request_adds_hkex_announcement_action(tmp_path: Path) -> None:
    _write_request_memo(
        tmp_path,
        ["补齐腾讯的港股公司公告和业绩披露。"],
        display_name="Tencent",
        symbol="0700.HK",
        market="HK",
    )

    requests = list_research_data_requests(tmp_path, symbol="0700.HK")

    assert [action.action_type for action in requests[0].suggested_actions] == [
        "filings",
        "verify",
    ]
    assert requests[0].suggested_commands == [
        "lychee data pull filings --symbols 0700.HK",
        "lychee research verify --symbol 0700.HK",
    ]


def test_fulfill_financial_fact_request_refreshes_snapshot_then_verifies(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["补齐营收、净利润和经营现金流的 SEC XBRL 财务快照。"],
        display_name="Apple Inc.",
        symbol="AAPL",
    )
    filings_calls: list[dict[str, object]] = []
    financials_calls: list[dict[str, object]] = []
    verify_calls: list[dict[str, object]] = []

    def fake_pull_filings(**kwargs: object) -> PullResult:
        filings_calls.append(kwargs)
        return PullResult("filings", "sec_edgar", 1, tmp_path / "data" / "filings.json", [])

    def fake_pull_financials(**kwargs: object) -> PullResult:
        financials_calls.append(kwargs)
        return PullResult("financials", "sec_edgar", 1, tmp_path / "data" / "financials.json", [])

    def fake_verify(**kwargs: object) -> object:
        verify_calls.append(kwargs)
        return SimpleNamespace(artifact_path=tmp_path / "research" / "verify.json")

    result = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="AAPL",
        pull_filings=fake_pull_filings,
        pull_financials=fake_pull_financials,
        verify_task=fake_verify,
    )

    assert filings_calls == [{"symbols": ["AAPL"], "output_dir": tmp_path}]
    assert financials_calls == [
        {"symbols": ["AAPL"], "output_dir": tmp_path, "force": True}
    ]
    assert verify_calls == [{"output_dir": tmp_path, "symbol": "AAPL", "name": None}]
    assert [execution.action_type for execution in result.executions] == [
        "filings",
        "financials",
        "verify",
    ]
    assert [execution.status for execution in result.executions] == [
        "completed",
        "completed",
        "completed",
    ]


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


def test_volatility_request_refreshes_cboe_metrics_then_verifies(tmp_path: Path) -> None:
    _write_request_memo(
        tmp_path,
        ["请补充科技股波动率极端化新闻所引用的具体指标、时间窗口和历史分位背景。"],
    )
    volatility_calls: list[dict[str, object]] = []
    verify_calls: list[dict[str, object]] = []

    def fake_pull_volatility(**kwargs: object) -> PullResult:
        volatility_calls.append(kwargs)
        return PullResult(
            "research_metric",
            "cboe",
            3,
            tmp_path / "data" / "research-metrics.json",
            [],
        )

    def fake_verify(**kwargs: object) -> object:
        verify_calls.append(kwargs)
        return SimpleNamespace(artifact_path=tmp_path / "research" / "verify.json")

    requests = list_research_data_requests(tmp_path, symbol="QQQ")

    assert [action.action_type for action in requests[0].suggested_actions] == [
        "volatility",
        "verify",
    ]
    assert requests[0].suggested_commands == [
        "lychee data pull volatility --symbols QQQ --force",
        "lychee research verify --symbol QQQ",
    ]

    result = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
        pull_volatility=fake_pull_volatility,
        verify_task=fake_verify,
    )

    assert volatility_calls == [{"symbols": ["QQQ"], "output_dir": tmp_path, "force": True}]
    assert verify_calls == [{"output_dir": tmp_path, "symbol": "QQQ", "name": None}]
    assert [execution.action_type for execution in result.executions] == [
        "volatility",
        "verify",
    ]
    assert [execution.status for execution in result.executions] == [
        "completed",
        "completed",
    ]


def test_fulfilled_research_data_request_leaves_queue(tmp_path: Path) -> None:
    _write_request_memo(
        tmp_path,
        ["请提供 QQQ 与更宽市场基准的行情、成交量和相对强弱对比。"],
    )

    def fake_pull_market(**kwargs: object) -> PullResult:
        return PullResult("market", "auto", 1, tmp_path / "data" / "market.json", [])

    def fake_verify(**kwargs: object) -> object:
        return SimpleNamespace(artifact_path=tmp_path / "research" / "verify.json")

    result = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
        pull_market=fake_pull_market,
        verify_task=fake_verify,
    )

    assert result.status == "completed"
    assert result.artifact_path is not None
    assert result.artifact_path.exists()
    assert list_research_data_requests(tmp_path, symbol="QQQ") == []


def test_diagnose_research_data_request_explains_failed_pull_without_retrying(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["请提供 QQQ 与更宽市场基准的行情、成交量和相对强弱对比。"],
    )

    def blocked_pull_market(**kwargs: object) -> PullResult:
        raise RuntimeError(
            "无法从 https://query1.finance.yahoo.com 获取 JSON: "
            "<urlopen error [Errno 1] Operation not permitted>"
        )

    fulfillment = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
        pull_market=blocked_pull_market,
    )

    diagnostic = diagnose_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
    )

    assert fulfillment.status == "failed"
    assert diagnostic.summary == "网络连接或系统权限阻止了数据源请求。"
    assert diagnostic.failure_path == fulfillment.artifact_path
    assert diagnostic.retry_command == (
        "lychee research run-data-request --request 1 --symbol QQQ"
    )
    assert diagnostic.recovery_steps == [
        "这不是 API Key 配置问题；先确认当前终端允许访问网络。",
        "检查代理、防火墙、DNS 或系统网络权限后，再重试。",
    ]
    assert diagnostic.failed_actions[0].action_type == "market"
    assert "Operation not permitted" in diagnostic.failed_actions[0].message


def test_fulfill_research_data_request_no_data_does_not_verify(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["请提供 QQQ 与更宽市场基准的行情、成交量和相对强弱对比。"],
    )
    verify_calls: list[dict[str, object]] = []

    def fake_pull_market(**kwargs: object) -> PullResult:
        return PullResult("market", "auto", 0, tmp_path / "data" / "market.json", [])

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

    assert verify_calls == []
    assert [execution.action_type for execution in result.executions] == [
        "market",
        "verify",
    ]
    assert [execution.status for execution in result.executions] == [
        "no-data",
        "skipped",
    ]
    assert "没有获取到匹配数据" in result.executions[0].message
    assert "未重新核验" in result.executions[1].message


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

    def fake_pull_fund_metadata(**kwargs: object) -> PullResult:
        return PullResult(
            "fund_metadata",
            "invesco_official",
            1,
            tmp_path / "data" / "fund-metadata.json",
            [],
        )

    result = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
        pull_fund_metadata=fake_pull_fund_metadata,
        write_fund_guide=fake_write_fund_guide,
        verify_task=fake_verify,
    )

    assert guide_calls == []
    assert verify_calls == [{"output_dir": tmp_path, "symbol": "QQQ", "name": None}]
    assert [execution.action_type for execution in result.executions] == [
        "fund_metadata",
        "verify",
    ]
    assert [execution.status for execution in result.executions] == [
        "completed",
        "completed",
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

    def fake_breadth(**kwargs: object) -> PullResult:
        return PullResult(
            "research_metric",
            "nasdaq_public",
            3,
            tmp_path / "data" / "research-metrics.json",
            [],
        )

    result = fulfill_research_data_request(
        tmp_path,
        request_index=1,
        symbol="QQQ",
        pull_breadth=fake_breadth,
        verify_task=fake_verify,
    )

    assert verify_calls == [{"output_dir": tmp_path, "symbol": "QQQ", "name": None}]
    assert [execution.action_type for execution in result.executions] == [
        "breadth",
        "verify",
    ]
    assert result.executions[0].status == "completed"


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
    assert "不能把它解释成真实上涨家数/下跌家数" in item.coverage_gap
    assert item.plugin_type == "market_breadth"
    assert item.suggested_provider_examples == [
        "Nasdaq NDX/NDXE 公开历史（等权扩散代理）",
        "Nasdaq Data Link / GIDS 成分与行情（需授权）",
        "等权指数或市场广度数据源",
        "行业/子行业表现数据源",
    ]
    assert item.suggested_commands == [
        "lychee data pull breadth --symbols QQQ --force",
        "lychee data set metric --symbol QQQ --domain market_breadth "
        '--name "<填入上涨/下跌家数或成分级广度指标>" '
        '--value "<填入核验后的读数>" '
        '--as-of YYYY-MM-DD --source-url "<资料来源URL>"',
    ]
    assert item.next_step == (
        "已接入 Nasdaq NDX/NDXE 扩散代理；若需要真实上涨/下跌家数，"
        "仍需专门成分级 provider 或人工核验来源。"
    )
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


def test_provider_backlog_hides_volatility_request_when_cboe_metrics_exist(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        [
            "请补充纳斯达克 100 成分股上涨家数和等权指数对比。",
            "请补充科技股波动率极端化新闻所引用的具体指标、时间窗口和历史分位背景。",
        ],
    )
    for name, value in (
        ("Cboe VXN 收盘", "25.65"),
        ("Cboe VXN 20 交易日变化", "-1.04%"),
        ("Cboe VXN 近一年历史分位", "75.4%"),
    ):
        write_research_metric_cache(
            output_dir=tmp_path,
            symbol="QQQ",
            domain="volatility_metrics",
            name=name,
            value=value,
            as_of="2026-07-15",
            source_url="https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv",
            provider="cboe",
        )

    backlog = list_provider_backlog_items(tmp_path, symbol="QQQ")

    assert [item.plugin_type for item in backlog] == ["market_breadth"]


def test_provider_backlog_hides_breadth_request_when_nasdaq_proxy_exists(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["请补充 Nasdaq 100 等权指数和市值加权指数对比。"],
    )
    for name, value in (
        ("Nasdaq-100 市值加权 20 交易日变化", "+2.00%"),
        ("Nasdaq-100 等权 20 交易日变化", "+2.50%"),
        ("Nasdaq-100 等权相对市值加权差异", "+0.50 个百分点"),
    ):
        write_research_metric_cache(
            output_dir=tmp_path,
            symbol="QQQ",
            domain="market_breadth",
            name=name,
            value=value,
            as_of="2026-07-17",
            source_url="https://indexes.nasdaq.com/Index/History/NDX",
            provider="nasdaq_public",
        )

    assert list_provider_backlog_items(tmp_path, symbol="QQQ") == []


def test_provider_backlog_keeps_actual_advancer_count_gap_with_proxy(
    tmp_path: Path,
) -> None:
    _write_request_memo(
        tmp_path,
        ["请补充纳斯达克 100 成分股上涨家数和等权指数对比。"],
    )
    for name, value in (
        ("Nasdaq-100 市值加权 20 交易日变化", "+2.00%"),
        ("Nasdaq-100 等权 20 交易日变化", "+2.50%"),
        ("Nasdaq-100 等权相对市值加权差异", "+0.50 个百分点"),
    ):
        write_research_metric_cache(
            output_dir=tmp_path,
            symbol="QQQ",
            domain="market_breadth",
            name=name,
            value=value,
            as_of="2026-07-17",
            source_url="https://indexes.nasdaq.com/Index/History/NDX",
            provider="nasdaq_public",
        )

    backlog = list_provider_backlog_items(tmp_path, symbol="QQQ")

    assert len(backlog) == 1
    assert backlog[0].suggested_commands == [
        "lychee data set metric --symbol QQQ --domain market_breadth "
        '--name "<填入上涨/下跌家数或成分级广度指标>" '
        '--value "<填入核验后的读数>" '
        '--as-of YYYY-MM-DD --source-url "<资料来源URL>"',
    ]


def _write_request_memo(
    tmp_path: Path,
    requests: list[str],
    *,
    display_name: str = "Invesco QQQ Trust",
    symbol: str = "QQQ",
    market: str = "US",
    asset_type: str | None = None,
) -> None:
    write_research_memo_record(
        output_dir=tmp_path,
        memo_id="research-memo:2026-07-05T10:02:00+00:00",
        created_at="2026-07-05T10:02:00+00:00",
        display_name=display_name,
        symbol=symbol,
        market=market,
        confidence="low",
        summary="QQQ 仍需补齐数据。",
        support_count=1,
        skeptic_count=1,
        missing_count=len(requests),
        next_step_count=len(requests),
        memo_path=tmp_path / "research" / "research-memo-test.json",
        verification_path=tmp_path / "research" / "research-verification-test.json",
        payload={
            "memo": {"next_data_requests": requests},
            **({"candidate": {"asset_type": asset_type}} if asset_type else {}),
        },
    )


def _write_verification_with_hypothesis_requests(
    tmp_path: Path,
    requests: list[str],
    *,
    topic_news_exhausted: bool = False,
) -> Path:
    research_dir = tmp_path / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    path = research_dir / "research-verification-test.json"
    payload = {
        "created_at": "2026-07-05T10:00:00+00:00",
        "candidate": {
            "display_name": "Invesco QQQ Trust",
            "symbol": "QQQ",
            "market": "US",
            "topic_news_exhausted": topic_news_exhausted,
        },
        "status_label": "需要补证据",
        "hypothesis_panel": {
            "next_data_requests": requests,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
