import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import record_cache_entry
from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.live_data import PullResult, write_research_metric_cache
from lychee_alphadesk.core.research import ResearchPacket
from lychee_alphadesk.core.research_db import (
    write_discovery_research_run,
    write_research_data_request_fulfillment_record,
)
from lychee_alphadesk.core.workbench import (
    CandidateCheck,
    ResearchDeepenResult,
    ResearchGapFillResult,
    WorkbenchCheckResult,
    _headline_lines,
    _next_step,
    _packet_related_news_count,
    _pull_research_action,
    _research_candidate_news_query,
    beginner_research_brief,
    build_research_evidence_change,
    build_research_verification_checks,
    record_research_evidence_review,
    render_research_task_detail,
    research_action_commands,
    run_research_task,
    run_workbench_check,
    select_research_candidate_index,
    suggest_pending_evidence_review,
    verify_research_task,
)


def test_next_step_summarizes_raw_data_gaps_as_a_single_user_action() -> None:
    step = _next_step(
        [],
        [
            "部分 discovery 证据 ID 未在当前本地新闻缓存中找到。",
            "缺少可审计新闻证据，需先刷新市场级或个股新闻缓存。",
            "缺少 QQQ 本地行情缓存。",
        ],
        object(),
    )

    assert step == "先补齐行情、新闻数据，再重新核验。"
    assert "discovery" not in step
    assert "。；" not in step


def test_headline_lines_explain_source_provenance_for_beginner_readout() -> None:
    lines = _headline_lines(
        [
            {
                "headline": "Tencent Cloud update",
                "source_url": "https://www.tencent.com/tencent-cloud-update/",
            },
            {
                "headline": "Tencent filing",
                "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0701/notice.pdf",
            },
        ],
        empty="无",
    )

    assert lines == [
        "- [公司官方] Tencent Cloud update (https://www.tencent.com/tencent-cloud-update/)",
        "- [交易所公告] Tencent filing (https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0701/notice.pdf)",
    ]


def test_research_run_action_marks_empty_provider_warnings_as_failed() -> None:
    action = _pull_research_action(
        action_type="refresh_news",
        symbols=["000001.SZ"],
        call=lambda: PullResult(
            "news",
            "auto",
            0,
            Path("/tmp/news-events.json"),
            ["Marketaux 被拒绝访问（HTTP 403）"],
        ),
    )

    assert action.status == "failed"
    assert action.message == "刷新新闻未完成。"


def test_research_run_action_marks_empty_clean_response_as_no_data() -> None:
    action = _pull_research_action(
        action_type="refresh_news",
        symbols=["000001.SZ"],
        call=lambda: PullResult(
            "news",
            "auto",
            0,
            Path("/tmp/news-events.json"),
            [],
        ),
    )

    assert action.status == "no_data"
    assert action.message == "刷新新闻没有获取到匹配数据。"


def test_research_news_query_uses_cn_stock_display_name() -> None:
    candidate = CandidateCheck(
        display_name="平安银行",
        market="CN",
        symbol="000001.SZ",
        proxy_symbols=[],
        evidence_count=0,
        gap_count=0,
        data_gaps=[],
        status="new",
        explanation="",
        beginner_question="",
        why_it_matters="",
        observation_entry="000001.SZ",
        what_to_check="",
        next_step="",
        priority="P1",
        evidence_status="",
    )

    assert _research_candidate_news_query(candidate) == "平安银行"


def test_workbench_check_runs_closed_loop_and_writes_beginner_ready_report(
    tmp_path: Path,
) -> None:
    _write_symbolless_seed(tmp_path)
    _write_live_caches(tmp_path, include_proxy_price=True)

    result = run_workbench_check(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.status == "ready"
    assert result.is_ready is True
    assert result.artifact_path is not None
    assert result.artifact_path.exists()
    assert "AlphaDesk 研究工作台" in result.beginner_brief
    assert "边界: 研究任务台，不给买卖建议。" in result.beginner_brief
    assert "今日研究任务" in result.beginner_brief
    assert "研究问题:" in result.beginner_brief
    assert "优先级:" in result.beginner_brief
    assert "排序理由:" in result.beginner_brief
    assert "证据状态:" in result.beginner_brief
    assert "关键核验:" in result.beginner_brief
    assert '执行命令: lychee research run --name "恒生指数压力观察" --force' in (
        result.beginner_brief
    )
    assert "下一步队列" in result.beginner_brief
    assert "2800.HK" in result.beginner_brief
    assert "给新手的读法" not in result.beginner_brief
    assert "怎么理解代理" not in result.beginner_brief
    assert "系统替你问的问题" not in result.beginner_brief
    assert "触发原因:" not in result.beginner_brief
    assert "。；" not in result.beginner_brief
    assert "代理核验: 核对成分、费用、流动性和是否可交易。" not in (
        result.beginner_brief
    )
    assert "代理核验: 重点补成分/费用" not in result.beginner_brief
    assert (
        "代理核验: 查看下钻核验证据中的成分/费用、可交易性和成交量；缺什么按待补证据处理。"
        in result.beginner_brief
    )

    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["proxy_price_coverage"] == "1/1"
    assert payload["candidates"][0]["beginner_question"]
    assert payload["candidates"][0]["what_to_check"]
    assert payload["candidates"][0]["priority"]
    assert payload["candidates"][0]["ranking_reason"]
    assert payload["candidates"][0]["evidence_status"]
    assert (
        payload["candidates"][0]["next_command"]
        == 'lychee research run --name "恒生指数压力观察" --force'
    )


def test_workbench_next_commands_preserve_expanded_selection_limit(
    tmp_path: Path,
) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource(provider="test-llm", market="HK", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股轮动观察",
                markets=["HK"],
                summary="多条港股线索需要扩大扫描范围才会显示。",
                evidence=["news_001"],
                sectors=["Market"],
                risk_flags=[],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="后排候选",
                symbol="LATE.HK",
                market="HK",
                asset_type="stock",
                related_theme="港股轮动观察",
                why_watch="用于复现扩大 limit 后的命令可执行性。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["下钻核验证据板"],
                confidence="medium",
                recommendation="research",
            ),
            *[
                DiscoveryCandidate(
                    display_name=f"前排候选 {index}",
                    symbol=f"FAST{index}.HK",
                    market="HK",
                    asset_type="stock",
                    related_theme="港股轮动观察",
                    why_watch="用于占据默认前五个研究位置。",
                    evidence=["news_001"],
                    risk_flags=[],
                    next_actions=["下钻核验证据板"],
                    confidence="medium",
                    recommendation="research",
                )
                for index in range(1, 6)
            ],
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery-today.json")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Hong Kong stocks rotate into smaller technology names",
                        "summary": "Market breadth improved across Hong Kong stocks.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/hk-rotation",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "auto",
                "rows": [
                    {
                        "symbol": symbol,
                        "date": "2026-07-05",
                        "close": 10 + index,
                        "volume": 1000000 + index,
                        "currency": "HKD",
                    }
                    for index, symbol in enumerate(
                        ["LATE.HK", "FAST1.HK", "FAST2.HK", "FAST3.HK", "FAST4.HK", "FAST5.HK"]
                    )
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "hkexnews",
                "rows": [
                    {
                        "date": "2026-07-05",
                        "company": symbol,
                        "form": "HKEX 公告",
                        "summary": "HKEXnews 公告: 测试公告",
                        "source_url": f"https://example.com/{symbol}",
                        "symbol": symbol,
                    }
                    for symbol in [
                        "LATE.HK",
                        "FAST1.HK",
                        "FAST2.HK",
                        "FAST3.HK",
                        "FAST4.HK",
                        "FAST5.HK",
                    ]
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_workbench_check(
        output_dir=tmp_path,
        limit=6,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    late_candidate = next(
        candidate for candidate in result.candidates if candidate.symbol == "LATE.HK"
    )
    assert late_candidate.next_command == "lychee research verify --symbol LATE.HK --limit 6"
    assert "执行: lychee research verify --symbol LATE.HK --limit 6" in result.beginner_brief
    assert (
        json.loads(result.artifact_path.read_text(encoding="utf-8"))["candidates"][-1][
            "next_command"
        ]
        == "lychee research verify --symbol LATE.HK --limit 6"
    )


def test_research_candidate_selection_prefers_direct_symbol_over_proxy(
    tmp_path: Path,
) -> None:
    proxy_theme = CandidateCheck(
        display_name="中国半导体设备观察",
        market="CN",
        symbol=None,
        proxy_symbols=["512480.SH"],
        evidence_count=2,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="主题代理。",
        beginner_question="AI 数据中心主题是否扩散？",
        why_it_matters="需要先用 ETF 代理观察。",
        observation_entry="512480.SH",
        what_to_check="核验代理。",
        next_step="核验证据板。",
        priority="P2",
        evidence_status="证据 2 条",
    )
    direct_candidate = CandidateCheck(
        display_name="半导体 ETF",
        market="CN",
        symbol="512480.SH",
        proxy_symbols=[],
        evidence_count=1,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="直接候选。",
        beginner_question="这个 ETF 自身是否有研究价值？",
        why_it_matters="这是用户命令指定的直接入口。",
        observation_entry="512480.SH",
        what_to_check="核验行情和新闻。",
        next_step="核验证据板。",
        priority="P2",
        evidence_status="证据 1 条",
    )
    result = WorkbenchCheckResult(
        created_at="2026-07-05T11:00:00+00:00",
        status="ready",
        packets_checked=2,
        ready_count=2,
        blocked_count=0,
        proxy_price_count=1,
        proxy_total=1,
        gates=[],
        candidates=[proxy_theme, direct_candidate],
        beginner_brief="",
        artifact_path=None,
        fill_result=ResearchGapFillResult(0, [], [], [], []),
        deepen_result=ResearchDeepenResult(
            created_at="2026-07-05T11:00:00+00:00",
            packets=[],
            artifact_path=None,
            db_path=tmp_path / "research.sqlite3",
        ),
    )

    selected = select_research_candidate_index(
        result,
        symbol="512480.SH",
        name=None,
    )

    assert selected == 1


def test_proxy_theme_detail_commands_use_task_name_not_proxy_symbol() -> None:
    candidate = CandidateCheck(
        display_name="中国半导体设备观察",
        market="CN",
        symbol=None,
        proxy_symbols=["512480.SH"],
        evidence_count=2,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="主题代理。",
        beginner_question="AI 数据中心主题是否扩散？",
        why_it_matters="需要先用 ETF 代理观察。",
        observation_entry="512480.SH",
        what_to_check="核验代理。",
        next_step="核验证据板。",
        priority="P2",
        evidence_status="证据 2 条",
        command_limit=25,
    )

    detail = render_research_task_detail(candidate, None)

    assert 'lychee research verify --name "中国半导体设备观察" --limit 25' in detail
    assert 'lychee research memo --name "中国半导体设备观察" --limit 25' in detail
    assert "lychee research verify --symbol 512480.SH" not in detail
    assert "lychee research memo --symbol 512480.SH" not in detail


def test_pending_evidence_suggestion_uses_research_question_context() -> None:
    verdict, reason = suggest_pending_evidence_review(
        "QQQ vs. VOO: Should the Nasdaq-100 or the S&P 500 Be Your Core Holding?",
        primary_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
    )

    assert verdict == "support"
    assert "宽基" in reason


def test_research_task_detail_separates_off_topic_related_news() -> None:
    candidate = CandidateCheck(
        display_name="盈富基金",
        market="HK",
        symbol="2800.HK",
        proxy_symbols=[],
        evidence_count=2,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="",
        beginner_question="港股变化是整个市场的问题，还是只集中在某个板块？",
        why_it_matters="",
        observation_entry="2800.HK",
        what_to_check="把它和更宽的市场基准对比。",
        next_step="复核主题新闻",
        priority="P2",
        evidence_status="证据质量待复核",
    )
    packet = ResearchPacket(
        packet_id="research:test:detail-news",
        candidate_id=1,
        created_at="2026-07-05T10:00:00+00:00",
        display_name="盈富基金",
        symbol="2800.HK",
        market="HK",
        packet={
            "candidate": {
                "asset_type": "ETF",
                "related_theme": "港股大盘与流动性观察",
                "why_watch": "用于观察港股大盘和流动性。",
            },
            "evidence": [],
            "local_data": {
                "price": {},
                "related_news": [
                    {
                        "headline": "Hong Kong stocks rise as Hang Seng liquidity improves",
                        "summary": "Hong Kong shares gained as turnover improved.",
                        "source_url": "https://example.com/hk-stocks",
                    },
                    {
                        "headline": "SkillCloak hides malicious AI agent tools",
                        "summary": "Security researchers disclosed an AI agent plugin issue.",
                        "source_url": "https://example.com/skillcloak",
                    },
                ],
                "filings": [],
                "symbol_mapping": [],
            },
            "data_gaps": [],
        },
    )

    detail = render_research_task_detail(candidate, packet)

    related_section = detail.split("相关新闻", 1)[1].split("离题/已过滤", 1)[0]
    assert "Hong Kong stocks rise as Hang Seng liquidity improves" in related_section
    assert "SkillCloak hides malicious AI agent tools" not in related_section
    assert "离题/已过滤" in detail
    assert "SkillCloak hides malicious AI agent tools" in detail
    assert _packet_related_news_count(candidate, packet) == 1


def test_research_task_detail_accepts_symbol_scoped_hk_news_without_market_literal() -> None:
    candidate = CandidateCheck(
        display_name="Tencent",
        market="HK",
        symbol="0700.HK",
        proxy_symbols=[],
        evidence_count=1,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="",
        beginner_question="",
        why_it_matters="",
        observation_entry="0700.HK",
        what_to_check="",
        next_step="复核主题新闻",
        priority="P2",
        evidence_status="证据质量待复核",
    )
    packet = ResearchPacket(
        packet_id="research:test:scoped-news",
        candidate_id=1,
        created_at="2026-07-05T10:00:00+00:00",
        display_name="Tencent",
        symbol="0700.HK",
        market="HK",
        packet={
            "candidate": {
                "asset_type": "stock",
                "related_theme": "AI 云与平台流动性",
                "why_watch": "",
            },
            "evidence": [],
            "local_data": {
                "price": {},
                "related_news": [
                    {
                        "headline": "Tencent cloud demand improves platform liquidity",
                        "summary": "AI cloud demand and platform liquidity improved.",
                        "symbols": ["0700.HK"],
                        "is_symbol_scoped": True,
                        "source_url": "https://example.com/tencent-cloud",
                    },
                    {
                        "headline": "Tencent SDK release 3.1.133",
                        "summary": "A Python SDK package release.",
                        "symbols": ["0700.HK"],
                        "is_symbol_scoped": True,
                        "source_url": "https://example.com/tencent-sdk",
                    },
                    {
                        "headline": "Tencent legacy cloud demand improves platform liquidity",
                        "summary": "AI cloud demand and platform liquidity improved.",
                        "symbols": ["0700.HK"],
                        "source_url": "https://example.com/legacy-batch-row",
                    },
                ],
                "filings": [],
                "symbol_mapping": [],
            },
            "data_gaps": [],
        },
    )

    detail = render_research_task_detail(candidate, packet)

    related_section = detail.split("相关新闻", 1)[1].split("离题/已过滤", 1)[0]
    assert "Tencent cloud demand improves platform liquidity" in related_section
    assert "Tencent SDK release 3.1.133" not in related_section
    assert "Tencent legacy cloud demand improves platform liquidity" not in related_section
    assert "Tencent SDK release 3.1.133" in detail.split("离题/已过滤", 1)[1]
    assert _packet_related_news_count(candidate, packet) == 1


def test_research_action_commands_include_hkex_filings_for_hk_stocks() -> None:
    candidate = CandidateCheck(
        display_name="Tencent",
        market="HK",
        symbol="0700.HK",
        proxy_symbols=[],
        evidence_count=0,
        gap_count=1,
        data_gaps=["缺少 0700.HK HKEX 公司公告缓存。"],
        status="blocked",
        explanation="",
        beginner_question="",
        why_it_matters="",
        observation_entry="0700.HK",
        what_to_check="",
        next_step="补公司公告",
        priority="P0 待补数据",
        evidence_status="",
    )
    packet = ResearchPacket(
        packet_id="research:test:hkex-action",
        candidate_id=1,
        created_at="2026-07-05T10:00:00+00:00",
        display_name="Tencent",
        symbol="0700.HK",
        market="HK",
        packet={"candidate": {"asset_type": "stock"}, "local_data": {}},
    )

    commands = research_action_commands(candidate, packet)

    assert "刷新公司公告: lychee data pull filings --symbols 0700.HK" in commands
    assert not any("刷新财务快照" in command for command in commands)


def test_research_action_commands_include_cninfo_filings_for_cn_stocks() -> None:
    candidate = CandidateCheck(
        display_name="平安银行",
        market="CN",
        symbol="000001.SZ",
        proxy_symbols=[],
        evidence_count=0,
        gap_count=1,
        data_gaps=["缺少 000001.SZ 巨潮公司公告缓存。"],
        status="blocked",
        explanation="",
        beginner_question="",
        why_it_matters="",
        observation_entry="000001.SZ",
        what_to_check="",
        next_step="补公司公告",
        priority="P0 待补数据",
        evidence_status="",
    )
    packet = ResearchPacket(
        packet_id="research:test:cninfo-action",
        candidate_id=1,
        created_at="2026-07-05T10:00:00+00:00",
        display_name="平安银行",
        symbol="000001.SZ",
        market="CN",
        packet={"candidate": {"asset_type": "stock"}, "local_data": {}},
    )

    commands = research_action_commands(candidate, packet)

    assert "刷新公司公告: lychee data pull filings --symbols 000001.SZ" in commands
    assert not any("刷新财务快照" in command for command in commands)


def test_research_verification_marks_hkex_announcements_as_required_for_hk_stocks() -> None:
    candidate = CandidateCheck(
        display_name="Tencent",
        market="HK",
        symbol="0700.HK",
        proxy_symbols=[],
        evidence_count=1,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="",
        beginner_question="",
        why_it_matters="",
        observation_entry="0700.HK",
        what_to_check="",
        next_step="核验公告",
        priority="P1 一致性复核",
        evidence_status="",
    )
    packet = ResearchPacket(
        packet_id="research:test:hkex-verification",
        candidate_id=1,
        created_at="2026-07-05T10:00:00+00:00",
        display_name="Tencent",
        symbol="0700.HK",
        market="HK",
        packet={
            "candidate": {"asset_type": "stock"},
            "local_data": {
                "price": {
                    "symbol": "0700.HK",
                    "date": "2026-07-05",
                    "close": 484.0,
                    "volume": 100000,
                    "currency": "HKD",
                },
                "related_news": [],
                "filings": [
                    {
                        "date": "2026-07-05",
                        "company": "TENCENT",
                        "form": "HKEX 公告",
                        "summary": "HKEXnews 公告: Quarterly Results",
                        "symbol": "0700.HK",
                        "source_url": "https://example.com/tencent-results",
                    }
                ],
            },
        },
    )

    checks = build_research_verification_checks(candidate, packet)
    filing_check = next(check for check in checks if check.name == "公告/财报核验")

    assert filing_check.status == "pass"
    assert filing_check.detail == "可核验 HKEX 公司公告 1 条。"


def test_research_verification_marks_cninfo_announcements_as_required_for_cn_stocks() -> None:
    candidate = CandidateCheck(
        display_name="平安银行",
        market="CN",
        symbol="000001.SZ",
        proxy_symbols=[],
        evidence_count=1,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="",
        beginner_question="",
        why_it_matters="",
        observation_entry="000001.SZ",
        what_to_check="",
        next_step="核验公告",
        priority="P1 一致性复核",
        evidence_status="",
    )
    packet = ResearchPacket(
        packet_id="research:test:cninfo-verification",
        candidate_id=1,
        created_at="2026-07-05T10:00:00+00:00",
        display_name="平安银行",
        symbol="000001.SZ",
        market="CN",
        packet={
            "candidate": {"asset_type": "stock"},
            "local_data": {
                "price": {
                    "symbol": "000001.SZ",
                    "date": "2026-07-05",
                    "close": 12.0,
                    "volume": 100000,
                    "currency": "CNY",
                },
                "related_news": [],
                "filings": [
                    {
                        "date": "2026-07-05",
                        "company": "平安银行",
                        "form": "巨潮公告",
                        "summary": "巨潮资讯公告: 董事会决议公告",
                        "symbol": "000001.SZ",
                        "source_url": "https://example.com/pingan-board.pdf",
                    }
                ],
            },
        },
    )

    checks = build_research_verification_checks(candidate, packet)
    filing_check = next(check for check in checks if check.name == "公告/财报核验")

    assert filing_check.status == "pass"
    assert filing_check.detail == "可核验 巨潮公司公告 1 条。"


def test_workbench_check_marks_blocked_when_research_gaps_remain(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(tmp_path, include_stock_price=True, include_filings=False)

    result = run_workbench_check(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_filings=_failed_filings_pull,
    )

    assert result.status == "blocked"
    assert result.is_ready is False
    data_gap_gate = next(gate for gate in result.gates if gate.name == "数据缺口")
    assert data_gap_gate.status == "fail"
    assert data_gap_gate.detail == "1 个任务仍缺少数据；请查看下方阻塞任务的处理动作。"
    assert "缺少 STX" not in data_gap_gate.detail
    assert "阻塞任务" in result.beginner_brief
    assert result.candidates[0].data_gaps == ["缺少 STX SEC 公告缓存。"]
    assert "缺口: 缺少 STX SEC 公告缓存" not in result.beginner_brief
    assert "研究问题:" in result.beginner_brief
    assert "当前状态: 数据尚未齐备，暂不进入下钻研究。" in result.beginner_brief
    assert "处理动作: 先补齐公告/财报数据，再重新核验。" in result.beginner_brief
    assert "处理动作: 先补齐 缺少" not in result.beginner_brief
    assert "处理命令: lychee research run --symbol STX --force" in (
        result.beginner_brief
    )


def test_workbench_check_routes_market_no_data_cooldown_to_data_health(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 5, 11, 0, tzinfo=UTC)
    _write_stock_seed(tmp_path)
    _write_live_caches(tmp_path, include_filings=True)
    record_cache_entry(
        output_dir=tmp_path,
        layer="market",
        cache_key="market:auto:STX",
        provider="auto",
        artifact_path=tmp_path / "data" / "market-prices.json",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        ttl_seconds=3600,
        status="no_data",
        row_count=0,
        market="US",
        session_state="open",
        is_final_for_session=False,
    )

    def cached_empty_market_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        return PullResult(
            "market",
            "auto",
            0,
            output_dir / "data" / "market-prices.json",
            ["上一次行情拉取没有获得数据，保质期内跳过重试。"],
            refreshed=False,
        )

    result = run_workbench_check(
        output_dir=tmp_path,
        now=now,
        pull_market=cached_empty_market_pull,
    )

    candidate = result.candidates[0]
    assert candidate.next_command == "lychee data health"
    assert candidate.next_step == "行情数据暂不可用，先检查数据健康或更新 provider 权限。"
    assert "处理动作: 行情数据暂不可用，先检查数据健康或更新 provider 权限。" in (
        result.beginner_brief
    )
    assert "处理命令: lychee data health" in result.beginner_brief


def test_verify_research_task_uses_proxy_prices_for_symbolless_themes(
    tmp_path: Path,
) -> None:
    _write_symbolless_seed(tmp_path)
    _write_live_caches(tmp_path, include_proxy_price=True)

    result = verify_research_task(
        output_dir=tmp_path,
        name="恒生指数压力观察",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    price_check = next(check for check in result.checks if check.name == "行情核验")
    volume_check = next(check for check in result.checks if check.name == "成交量核验")
    assert price_check.status == "pass"
    assert "2800.HK 18.50 HKD" in price_check.detail
    assert volume_check.status == "pass"
    assert "成交量 1000000" in volume_check.detail
    assert any("2800.HK 18.50 HKD" in item for item in result.evidence_board["support"])
    assert any(
        "代理映射: 2800.HK 盈富基金" in item
        and "置信度 medium" in item
        and "恒生指数压力主题" in item
        for item in result.evidence_board["support"]
    )
    assert any(
        "可交易性: 2800.HK 为 HK ETF 代理" in item
        for item in result.evidence_board["support"]
    )
    assert any(
        "流动性: 2800.HK 成交量 1000000" in item
        for item in result.evidence_board["support"]
    )
    assert not any(
        "行情核验: 缺少本地行情" in item
        for item in result.evidence_board["missing"]
    )
    assert any(
        "代理资料: 缺少 2800.HK 成分/费用缓存" in item
        for item in result.evidence_board["missing"]
    )
    proxy_check = next(check for check in result.checks if check.name == "代理标的核验")
    assert "2800.HK 盈富基金" in proxy_check.detail
    assert "置信度 medium" in proxy_check.detail
    assert "可交易性: 2800.HK 为 HK ETF 代理" in proxy_check.detail
    assert "流动性: 2800.HK 成交量 1000000" in proxy_check.detail
    assert "缺少 2800.HK 成分/费用缓存" in proxy_check.detail
    assert not any(
        item.startswith("代理标的: ")
        for item in result.evidence_board["risk"]
    )


def test_verify_research_task_builds_analyst_readout_for_beginners(
    tmp_path: Path,
) -> None:
    _write_symbolless_seed(tmp_path)
    _write_live_caches(tmp_path, include_proxy_price=True)

    result = verify_research_task(
        output_dir=tmp_path,
        name="恒生指数压力观察",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.analyst_readout.title == "分析师读数"
    assert "当前信号:" in result.analyst_readout.signal
    assert "支持" in result.analyst_readout.signal
    assert "反向压力:" in result.analyst_readout.pressure
    assert "证据缺口:" in result.analyst_readout.gap
    assert "工作台动作:" in result.analyst_readout.next_action
    assert result.analyst_readout.next_command == result.decision_board.next_commands[0]

    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["analyst_readout"]["title"] == "分析师读数"
    assert payload["analyst_readout"]["next_command"] == result.analyst_readout.next_command


def test_verify_research_task_builds_research_hypothesis_panel(
    tmp_path: Path,
) -> None:
    _write_symbolless_seed(tmp_path)
    _write_live_caches(tmp_path, include_proxy_price=True)

    result = verify_research_task(
        output_dir=tmp_path,
        name="恒生指数压力观察",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.hypothesis_panel.title == "研究假设面板"
    assert result.hypothesis_panel.core_question.startswith("核心问题:")
    assert "恒生指数压力观察" in result.hypothesis_panel.working_hypothesis
    assert result.hypothesis_panel.support_chain
    assert result.hypothesis_panel.counter_chain
    assert result.hypothesis_panel.gap_priorities
    assert result.hypothesis_panel.next_data_requests
    assert all("买入" not in item for item in result.hypothesis_panel.next_data_requests)

    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["hypothesis_panel"]["title"] == "研究假设面板"
    assert payload["hypothesis_panel"]["next_data_requests"] == (
        result.hypothesis_panel.next_data_requests
    )


def test_verify_research_task_uses_source_backed_research_metrics(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(tmp_path, include_stock_price=True, include_filings=True)
    write_research_metric_cache(
        output_dir=tmp_path,
        symbol="STX",
        domain="market_breadth",
        name="AI 存储链扩散指标",
        value="7/10 上涨",
        as_of="2026-07-07",
        source_url="https://example.com/storage-breadth",
        note="用于观察是否从单一股票扩散到供应链。",
    )
    for name, value in (
        ("QQQ 5 交易日变化", "-3.98%"),
        ("SPY 5 交易日变化", "-1.52%"),
        ("QQQ 相对 SPY 5 交易日差异", "-2.46 个百分点"),
    ):
        write_research_metric_cache(
            output_dir=tmp_path,
            symbol="STX",
            domain="benchmark_comparison",
            name=name,
            value=value,
            as_of="2026-07-17",
            source_url="https://query1.finance.yahoo.com/v7/finance/spark",
            note="用于测试主题与宽基的相对表现读数。",
        )

    result = verify_research_task(
        output_dir=tmp_path,
        symbol="STX",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    metric_check = next(check for check in result.checks if check.name == "研究指标核验")
    assert metric_check.status == "pass"
    assert "AI 存储链扩散指标" in metric_check.detail
    assert any(
        "研究指标: STX 市场广度 AI 存储链扩散指标 = 7/10 上涨" in item
        and "来源 https://example.com/storage-breadth" in item
        for item in result.evidence_board["support"]
    )
    benchmark_check = next(check for check in result.checks if check.name == "基准比较核验")
    assert benchmark_check.status == "pass"
    assert "QQQ 相对 SPY 5 交易日差异" in benchmark_check.detail
    assert any(
        "研究指标: STX 基准比较 QQQ 相对 SPY 5 交易日差异 = -2.46 个百分点" in item
        for item in result.evidence_board["support"]
    )
    detail = render_research_task_detail(result.candidate, result.packet)
    assert "研究指标" in detail
    assert "AI 存储链扩散指标 = 7/10 上涨" in detail


def test_verify_research_task_surfaces_forecast_as_non_supporting_reference(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(tmp_path, include_stock_price=True, include_filings=True)
    data_dir = tmp_path / "data"
    (data_dir / "forecasts.json").write_text(
        json.dumps(
            {
                "provider": "timesfm",
                "rows": [
                    {
                        "symbol": "STX",
                        "horizon_days": 20,
                        "lower": 90.0,
                        "midpoint": 100.0,
                        "upper": 110.0,
                        "method": "timesfm-2.5-200m-pytorch",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = verify_research_task(
        output_dir=tmp_path,
        symbol="STX",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    forecast_check = next(check for check in result.checks if check.name == "预测参考")
    assert forecast_check.status == "warn"
    assert "必须回测后再解释" in forecast_check.detail
    detail = render_research_task_detail(result.candidate, result.packet)
    assert "模型预测参考" in detail
    assert "90.00 - 110.00" in detail
    assert "不作为买卖信号" in detail


def test_verify_research_task_uses_cached_sec_financial_snapshot(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(tmp_path, include_stock_price=True, include_filings=True)
    data_dir = tmp_path / "data"
    (data_dir / "financials.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "symbol": "STX",
                        "company": "Seagate Technology Holdings plc",
                        "cik": 1137789,
                        "form": "10-Q",
                        "fiscal_year": 2026,
                        "fiscal_period": "Q3",
                        "period_end": "2026-03-27",
                        "filing_date": "2026-05-01",
                        "currency": "USD",
                        "revenue": 2310000000,
                        "revenue_period_start": "2025-12-27",
                        "revenue_period_end": "2026-03-27",
                        "revenue_prior": 2100000000,
                        "revenue_prior_period_start": "2024-12-28",
                        "revenue_prior_period_end": "2025-03-28",
                        "net_income": 330000000,
                        "net_income_period_start": "2025-12-27",
                        "net_income_period_end": "2026-03-27",
                        "net_income_prior": 300000000,
                        "net_income_prior_period_start": "2024-12-28",
                        "net_income_prior_period_end": "2025-03-28",
                        "operating_cash_flow": 410000000,
                        "operating_cash_flow_period_start": "2025-06-28",
                        "operating_cash_flow_period_end": "2026-03-27",
                        "operating_cash_flow_prior": 400000000,
                        "operating_cash_flow_prior_period_start": "2024-06-29",
                        "operating_cash_flow_prior_period_end": "2025-03-28",
                        "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001137789.json",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = verify_research_task(
        output_dir=tmp_path,
        symbol="STX",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    financial_check = next(
        check for check in result.checks if check.name == "财务快照核验"
    )
    assert financial_check.status == "pass"
    assert "营收 2,310,000,000 USD" in financial_check.detail
    assert any(
        "财务快照: STX 2026 Q3 10-Q" in item
        and "来源 https://data.sec.gov/api/xbrl/companyfacts/CIK0001137789.json" in item
        for item in result.evidence_board["support"]
    )
    detail = render_research_task_detail(result.candidate, result.packet)
    assert "财务快照" in detail
    assert "营收 2,310,000,000 USD (2025-12-27 至 2026-03-27)" in detail
    assert (
        "同比 +10.0% (上年同期 2,100,000,000 USD "
        "(2024-12-28 至 2025-03-28))"
    ) in detail
    assert "经营现金流 410,000,000 USD (2025-06-28 至 2026-03-27)" in detail


def test_verify_research_task_uses_cached_proxy_fund_metadata(
    tmp_path: Path,
) -> None:
    _write_symbolless_seed(tmp_path)
    _write_live_caches(tmp_path, include_proxy_price=True)
    _write_fund_metadata_cache(tmp_path)

    result = verify_research_task(
        output_dir=tmp_path,
        name="恒生指数压力观察",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert any(
        "代理资料: 2800.HK" in item
        and "跟踪指数 Hang Seng Index" in item
        and "费用 0.10%" in item
        and "来源 https://example.com/2800" in item
        for item in result.evidence_board["support"]
    )
    assert not any(
        "代理资料: 缺少 2800.HK 成分/费用缓存" in item
        for item in result.evidence_board["missing"]
    )
    proxy_check = next(check for check in result.checks if check.name == "代理标的核验")
    assert "代理资料: 2800.HK" in proxy_check.detail
    assert "跟踪指数 Hang Seng Index" in proxy_check.detail
    assert "费用 0.10%" in proxy_check.detail
    assert "缺少 2800.HK 成分/费用缓存" not in proxy_check.detail


def test_verify_research_task_requires_direct_etf_fund_metadata(
    tmp_path: Path,
) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource(provider="test-llm", market="HK", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股科技 ETF 观察",
                markets=["HK"],
                summary="观察港股科技 ETF 的成交额和成分覆盖。",
                evidence=["news_001"],
                sectors=["ETF"],
                risk_flags=["需要核对 ETF 成分和费用"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="E Fund HKEX Tech 100 ETF",
                symbol="3456.HK",
                market="HK",
                asset_type="ETF",
                related_theme="港股科技 ETF 观察",
                why_watch="用 ETF 观察港股科技板块资金是否回流。",
                evidence=["news_001"],
                risk_flags=["需要核对 ETF 成分和费用"],
                next_actions=["核对基金成分和费用", "观察成交额稳定性"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery-today.json")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Hong Kong pension fund increases gold ETFs",
                        "summary": "A pension fund may increase gold ETF exposure.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/gold-etf",
                    },
                    {
                        "timestamp": "2026-07-05T09:05:00+00:00",
                        "headline": "3456.HK HKEX Tech 100 ETF turnover improves",
                        "summary": "Hong Kong technology ETF turnover improved.",
                        "symbols": ["3456.HK"],
                        "source_url": "https://example.com/3456",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "auto",
                "rows": [
                    {
                        "symbol": "3456.HK",
                        "date": "2026-07-05",
                        "close": 7.8,
                        "volume": 647200,
                        "currency": "HKD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = verify_research_task(
        output_dir=tmp_path,
        symbol="3456.HK",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    fund_check = next(check for check in result.checks if check.name == "基金资料核验")
    assert fund_check.status == "warn"
    assert "缺少 3456.HK 成分/费用缓存" in fund_check.detail
    assert any(
        "基金资料: 缺少 3456.HK 成分/费用缓存" in item
        for item in result.evidence_board["missing"]
    )
    assert any(
        "新闻: 3456.HK HKEX Tech 100 ETF turnover improves" in item
        for item in result.evidence_board["support"]
    )
    assert not any(
        "Hong Kong pension fund increases gold ETFs" in item
        for item in result.evidence_board["support"]
    )
    assert any(
        "新闻待查: Hong Kong pension fund increases gold ETFs" in item
        for item in result.evidence_board["off_topic"]
    )
    assert result.decision_board.workflow_state == "fund_metadata_review"
    assert result.decision_board.next_commands[0].startswith(
        "lychee data guide fund --symbol 3456.HK"
    )
    assert any(
        "lychee data set fund --symbol 3456.HK" in command
        for command in result.decision_board.next_commands
    )
    assert not any(
        "lychee research memo --symbol 3456.HK" in command
        for command in result.decision_board.next_commands
    )
    workbench = run_workbench_check(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
    )
    candidate = workbench.candidates[0]
    assert workbench.status == "blocked"
    assert candidate.status == "blocked"
    assert candidate.gap_count == 1
    assert candidate.data_gaps == []
    assert "核验阻塞: 待补基金资料" in candidate.evidence_status
    assert candidate.priority == "P2 待补基金资料"
    assert "先生成 ETF/基金资料补齐向导" in candidate.next_step
    assert "lychee data guide fund --symbol 3456.HK" in candidate.next_command
    assert "lychee data guide fund --symbol 3456.HK" in workbench.beginner_brief


def test_workbench_uses_latest_ready_verification_as_next_research_step(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        include_filings=True,
        news_headline="STX AI storage demand expands",
        news_summary="AI storage demand supports the Seagate research question.",
    )
    research_dir = tmp_path / "research"
    research_dir.mkdir(exist_ok=True)
    (research_dir / "research-verification-ready.json").write_text(
        json.dumps(
            {
                "created_at": "2026-07-05T11:00:00+00:00",
                "candidate": {
                    "display_name": "Seagate",
                    "symbol": "STX",
                    "market": "US",
                },
                "decision_board": {
                    "workflow_state": "ready_for_review",
                    "workflow_label": "可进入人工一致性复核",
                    "next_steps": ["记录继续研究，并进入人工一致性复核。"],
                    "next_commands": ["lychee research memo --symbol STX"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    workbench = run_workbench_check(output_dir=tmp_path)

    candidate = workbench.candidates[0]
    assert candidate.status == "ready"
    assert candidate.priority == "P1 一致性复核"
    assert candidate.next_step == "记录继续研究，并进入人工一致性复核。"
    assert candidate.next_command == "lychee research memo --symbol STX"
    assert "lychee research memo --symbol STX" in workbench.beginner_brief


def test_verify_research_task_uses_direct_etf_fund_metadata(
    tmp_path: Path,
) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource(provider="test-llm", market="HK", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股科技 ETF 观察",
                markets=["HK"],
                summary="观察港股科技 ETF 的成交额和成分覆盖。",
                evidence=["news_001"],
                sectors=["ETF"],
                risk_flags=["需要核对 ETF 成分和费用"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="E Fund HKEX Tech 100 ETF",
                symbol="3456.HK",
                market="HK",
                asset_type="ETF",
                related_theme="港股科技 ETF 观察",
                why_watch="用 ETF 观察港股科技板块资金是否回流。",
                evidence=["news_001"],
                risk_flags=["需要核对 ETF 成分和费用"],
                next_actions=["核对基金成分和费用", "观察成交额稳定性"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery-today.json")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Hong Kong pension fund increases gold ETFs",
                        "summary": "A pension fund may increase gold ETF exposure.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/gold-etf",
                    },
                    {
                        "timestamp": "2026-07-05T09:05:00+00:00",
                        "headline": "HKEX Tech 100 ETF turnover improves",
                        "summary": "Hong Kong technology ETF turnover improved.",
                        "symbols": ["3456.HK"],
                        "source_url": "https://example.com/3456",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "auto",
                "rows": [
                    {
                        "symbol": "3456.HK",
                        "date": "2026-07-05",
                        "close": 7.8,
                        "volume": 647200,
                        "currency": "HKD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "fund-metadata.json").write_text(
        json.dumps(
            {
                "provider": "manual",
                "rows": [
                    {
                        "symbol": "3456.HK",
                        "display_name": "E Fund HKEX Tech 100 ETF",
                        "market": "HK",
                        "tracking_index": "HKEX Tech 100 Index",
                        "expense_ratio": "0.99%",
                        "holdings_summary": "港股科技龙头组合",
                        "source_url": "https://example.com/3456",
                        "as_of": "2026-07-05",
                        "provider": "manual",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = verify_research_task(
        output_dir=tmp_path,
        symbol="3456.HK",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    fund_check = next(check for check in result.checks if check.name == "基金资料核验")
    assert fund_check.status == "pass"
    assert "跟踪指数 HKEX Tech 100 Index" in fund_check.detail
    assert "费用 0.99%" in fund_check.detail
    assert any(
        "基金资料: 3456.HK" in item
        and "跟踪指数 HKEX Tech 100 Index" in item
        and "费用 0.99%" in item
        for item in result.evidence_board["support"]
    )
    assert not any(
        "基金资料: 缺少 3456.HK 成分/费用缓存" in item
        for item in result.evidence_board["missing"]
    )


def test_workbench_check_downgrades_reverse_only_evidence(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        include_filings=True,
        news_headline="STX hard drive demand falls as cloud buyers cut orders",
        news_summary=(
            "Weak AI infrastructure spending pressures Seagate storage demand."
        ),
    )

    result = run_workbench_check(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.status == "ready"
    candidate = result.candidates[0]
    assert candidate.display_name == "Seagate"
    assert candidate.priority == "P2 先复核证据"
    assert "证据质量: 支持 0 | 反向 " in candidate.evidence_status
    assert "只有反向或待判定证据" in candidate.ranking_reason
    assert "刷新主题新闻" in candidate.next_step
    assert candidate.next_command == "lychee research run --symbol STX --force"
    assert "P2 先复核证据" in result.beginner_brief
    assert "只有反向或待判定证据" in result.beginner_brief

    detail = render_research_task_detail(candidate, result.deepen_result.packets[0])
    assert "阶段: 先复核证据" in detail
    assert "信号读数: 证据需复核" in detail
    assert "下一步判断: 先刷新主题新闻" in detail

    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["candidates"][0]["evidence_quality"] == "needs_review"


def test_research_run_refreshes_topic_news_for_weak_evidence(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        include_filings=True,
        news_headline="Generic market article",
        news_summary="Broad market commentary without the storage theme.",
    )
    calls: list[dict[str, object]] = []

    def fake_news_pull(**kwargs: object) -> PullResult:
        calls.append(dict(kwargs))
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        query = str(kwargs.get("query") or "")
        if query:
            headline = "AI storage demand growth improves for Seagate"
            summary = "Cloud data-center demand increased for hard drives."
        else:
            headline = "Generic STX market news"
            summary = "Symbol-only news without storage demand direction."
        output_path = data_dir / "news-events.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "newsapi",
                    "rows": [
                        {
                            "timestamp": "2026-07-05T09:00:00+00:00",
                            "headline": headline,
                            "summary": summary,
                            "symbols": ["STX"],
                            "source_url": "https://example.com/topic-news",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", 1, output_path, [])

    result = run_research_task(
        output_dir=tmp_path,
        symbol="STX",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=fake_news_pull,
        pull_filings=_fake_filings_pull,
    )

    news_calls = [call for call in calls if call["provider_id"] == "auto"]
    assert len(news_calls) == 2
    assert news_calls[0]["symbols"] == ["STX"]
    assert news_calls[0].get("query") in {"", None}
    assert news_calls[1]["symbols"] == ["STX"]
    assert "AI 存储需求" in str(news_calls[1]["query"])
    assert result.actions[1].action_type == "refresh_news"
    assert result.actions[2].action_type == "refresh_topic_news"
    assert result.assessment.stage == "ready_for_drilldown"
    assert "阶段: 可下钻研究" in result.detail
    assert "AI storage demand growth improves" in result.detail


def test_research_run_hides_followup_commands_after_unresolved_empty_refresh(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        include_filings=True,
    )
    (tmp_path / "data" / "news-events.json").write_text(
        json.dumps({"provider": "newsapi", "rows": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    def empty_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        return PullResult(
            "news",
            "auto",
            0,
            output_dir / "data" / "news-events.json",
            ["NewsAPI 没有返回匹配新闻"],
        )

    result = run_research_task(
        output_dir=tmp_path,
        symbol="STX",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=empty_news_pull,
        pull_filings=_fake_filings_pull,
    )

    assert result.status == "partial"
    assert result.actions[1].status == "failed"
    assert "刷新新闻 | 失败" in result.detail
    assert "- 刷新新闻: lychee data pull news --symbols STX" in result.detail
    assert "- 下钻核验: lychee research verify --symbol STX" not in result.detail
    assert "- 研究备忘录: lychee research memo --symbol STX" not in result.detail


def test_research_run_hides_followup_commands_after_off_topic_news_refresh(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        include_filings=True,
    )
    (tmp_path / "data" / "news-events.json").write_text(
        json.dumps({"provider": "newsapi", "rows": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    def noisy_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        return PullResult(
            "news",
            "newsapi",
            20,
            output_dir / "data" / "news-events.json",
            [],
        )

    def fresh_market_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        return PullResult(
            "market",
            "auto",
            1,
            output_dir / "data" / "market-prices.json",
            [],
        )

    result = run_research_task(
        output_dir=tmp_path,
        symbol="STX",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=fresh_market_pull,
        pull_news=noisy_news_pull,
        pull_filings=_fake_filings_pull,
    )

    assert result.actions[1].status == "pulled"
    assert result.candidate.data_gaps
    assert "第一步: lychee research verify --symbol STX" not in result.detail
    assert "- 下钻核验: lychee research verify --symbol STX" not in result.detail
    assert "- 研究备忘录: lychee research memo --symbol STX" not in result.detail


def test_research_run_stops_repeating_topic_refresh_after_exhausted_news(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        include_filings=True,
        news_headline="Generic market article",
        news_summary="Broad market commentary without the storage theme.",
    )

    def fake_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        output_path = data_dir / "news-events.json"
        query = str(kwargs.get("query") or "")
        if query:
            rows = [
                {
                    "timestamp": "2026-07-05T09:30:00+00:00",
                    "headline": "Luxury hotels expand summer travel discounts",
                    "summary": "Travel operators promoted resort packages.",
                    "symbols": ["MARKET"],
                    "source_url": "https://example.com/hotels",
                },
                {
                    "timestamp": "2026-07-05T09:31:00+00:00",
                    "headline": "Restaurants launch new breakfast menus",
                    "summary": "Food chains tested new meals in city stores.",
                    "symbols": ["MARKET"],
                    "source_url": "https://example.com/restaurants",
                },
            ]
        else:
            rows = [
                {
                    "timestamp": "2026-07-05T09:00:00+00:00",
                    "headline": "Generic STX market news",
                    "summary": "Symbol-only news without storage demand direction.",
                    "symbols": ["STX"],
                    "source_url": "https://example.com/stx",
                }
            ]
        output_path.write_text(
            json.dumps({"provider": "newsapi", "rows": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", len(rows), output_path, [])

    def fresh_market_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        return PullResult(
            "market",
            "auto",
            1,
            output_dir / "data" / "market-prices.json",
            [],
        )

    result = run_research_task(
        output_dir=tmp_path,
        symbol="STX",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=fresh_market_pull,
        pull_news=fake_news_pull,
        pull_filings=_fake_filings_pull,
    )

    assert result.candidate.topic_news_exhausted is True
    assert result.status == "partial"
    assert result.candidate.next_command == "lychee research verify --symbol STX"
    assert "不要重复刷新同一主题新闻" in result.assessment.next_decision
    assert "下一步判断: 不要重复刷新同一主题新闻" in result.detail
    assert "主题新闻过滤: 本次拉取 2 条，0 条进入相关新闻。" in result.detail
    assert "数据完整性: 无" in result.detail
    assert "研究缺口: 主题新闻已刷新，但没有回答当前研究问题的材料。" in result.detail
    assert "- 刷新主题新闻: lychee data pull news" not in result.detail
    assert "- 下钻核验: lychee research verify --symbol STX" in result.detail
    assert "- 研究备忘录: lychee research memo --symbol STX" not in result.detail


def test_research_run_expands_pool_for_explicit_symbol(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["US"],
        sources=[DiscoverySource(provider="test-llm", market="US", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 基础设施扩散",
                markets=["US"],
                summary="AI 基础设施主题需要下钻多个公司。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=[],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="NVIDIA",
                symbol="NVDA",
                market="US",
                asset_type="stock",
                related_theme="AI 基础设施扩散",
                why_watch="用算力芯片龙头校验主题。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["下钻 NVDA"],
                confidence="medium",
                recommendation="research",
            ),
            DiscoveryCandidate(
                display_name="Seagate",
                symbol="STX",
                market="US",
                asset_type="stock",
                related_theme="AI 存储需求",
                why_watch="硬盘供需可能改善。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["下钻 STX"],
                confidence="medium",
                recommendation="research",
            ),
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery.json")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "auto",
                "rows": [
                    {
                        "symbol": "NVDA",
                        "date": "2026-07-02",
                        "close": 194.83,
                        "volume": 142068700,
                        "currency": "USD",
                    },
                    {
                        "symbol": "STX",
                        "date": "2026-07-02",
                        "close": 110.5,
                        "volume": 3210000,
                        "currency": "USD",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "AI chip data center demand supports Nvidia",
                        "summary": (
                            "Artificial intelligence infrastructure demand remains "
                            "relevant."
                        ),
                        "symbols": ["NVDA"],
                        "source_url": "https://example.com/nvda",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_research_task(
        output_dir=tmp_path,
        symbol="NVDA",
        limit=1,
        force=False,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=_fake_news_pull,
        pull_filings=_fake_filings_pull,
    )

    assert result.candidate.symbol == "NVDA"


def test_research_verify_expands_pool_for_explicit_symbol(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["US"],
        sources=[DiscoverySource(provider="test-llm", market="US", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 基础设施扩散",
                markets=["US"],
                summary="测试显式代码是否能超出默认候选范围。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=[],
                confidence="medium",
            )
        ],
        candidates=[
            *[
                DiscoveryCandidate(
                    display_name=f"前排候选 {index}",
                    symbol=f"FAST{index}",
                    market="US",
                    asset_type="stock",
                    related_theme="AI 基础设施扩散",
                    why_watch="占据默认候选范围。",
                    evidence=["news_001", "news_002"],
                    risk_flags=[],
                    next_actions=["继续研究"],
                    confidence="medium",
                    recommendation="research",
                )
                for index in range(1, 6)
            ],
            DiscoveryCandidate(
                display_name="后排候选",
                symbol="LATE",
                market="US",
                asset_type="stock",
                related_theme="AI 基础设施扩散",
                why_watch="需要超出默认范围后才能被选中。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["继续研究"],
                confidence="medium",
                recommendation="research",
            ),
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery.json")

    result = verify_research_task(
        output_dir=tmp_path,
        symbol="LATE",
        limit=1,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_filings=_fake_filings_pull,
    )

    assert result.candidate.symbol == "LATE"


def test_evidence_review_expands_pool_for_explicit_symbol(tmp_path: Path) -> None:
    _write_explicit_symbol_pool_seed(tmp_path)

    result = record_research_evidence_review(
        output_dir=tmp_path,
        symbol="NVDA",
        evidence_text="Perplexity says it plans to use Nvidia's new CPU",
        verdict="irrelevant",
        note="系统暂未识别明确方向，建议先按无关/排除处理。",
        limit=1,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.candidate.symbol == "NVDA"
    assert result.verdict == "irrelevant"
    assert result.artifact_path.exists()


def test_workbench_check_remembers_exhausted_topic_news_run(tmp_path: Path) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        include_filings=True,
        news_headline="Generic market article",
        news_summary="Broad market commentary without the storage theme.",
    )

    def fake_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        output_path = data_dir / "news-events.json"
        query = str(kwargs.get("query") or "")
        rows = [
            {
                "timestamp": "2026-07-05T09:30:00+00:00",
                "headline": "Luxury hotels expand summer travel discounts",
                "summary": "Travel operators promoted resort packages.",
                "symbols": ["MARKET"],
                "source_url": "https://example.com/hotels",
            }
        ]
        if not query:
            rows = [
                {
                    "timestamp": "2026-07-05T09:00:00+00:00",
                    "headline": "Generic STX market news",
                    "summary": "Symbol-only news without storage demand direction.",
                    "symbols": ["STX"],
                    "source_url": "https://example.com/stx",
                }
            ]
        output_path.write_text(
            json.dumps({"provider": "newsapi", "rows": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", len(rows), output_path, [])

    run_research_task(
        output_dir=tmp_path,
        symbol="STX",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=fake_news_pull,
        pull_filings=_fake_filings_pull,
    )
    result = run_workbench_check(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
    )
    candidate = result.candidates[0]

    assert candidate.topic_news_exhausted is True
    assert candidate.next_command == "lychee research verify --symbol STX"
    assert "主题新闻已刷新但没有可用材料" in candidate.next_step
    assert "lychee research run --symbol STX --force" not in result.beginner_brief

    run_research_task(
        output_dir=tmp_path,
        symbol="STX",
        force=True,
        now=datetime(2026, 7, 5, 11, 10, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=fake_news_pull,
        pull_filings=_fake_filings_pull,
    )
    repeated_check = run_workbench_check(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 15, tzinfo=UTC),
    )

    assert repeated_check.candidates[0].topic_news_exhausted is True
    assert repeated_check.candidates[0].next_command == "lychee research verify --symbol STX"
    assert "lychee research run --symbol STX --force" not in repeated_check.beginner_brief


def test_workbench_stops_auto_news_refresh_after_data_request_returns_no_topic_evidence(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        news_headline="Generic market commentary",
        news_summary="Broad news without the AI storage research theme.",
    )
    (tmp_path / "data" / "news-events.json").write_text(
        json.dumps({"provider": "newsapi", "rows": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    fulfillment_path = tmp_path / "research" / "request-fulfillment.json"
    fulfillment_path.parent.mkdir(exist_ok=True)
    fulfillment_path.write_text("{}", encoding="utf-8")
    write_research_data_request_fulfillment_record(
        output_dir=tmp_path,
        fulfillment_id="data-request:2026-07-05T11:00:00+00:00:STX",
        created_at="2026-07-05T11:00:00+00:00",
        request_id="verification:STX:news",
        display_name="Seagate",
        symbol="STX",
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
                    "count": 5,
                },
                {
                    "action_type": "verify",
                    "status": "completed",
                    "count": 1,
                },
            ]
        },
    )

    def no_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        return PullResult("news", "newsapi", 0, output_dir / "data" / "news-events.json", [])

    result = run_workbench_check(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
        pull_news=no_news_pull,
    )

    candidate = result.candidates[0]
    assert candidate.topic_news_exhausted is True
    assert candidate.next_command == "lychee research verify --symbol STX"
    assert "lychee research run --symbol STX --force" not in result.beginner_brief


def test_research_run_routes_refreshed_mixed_news_to_verification(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(
        tmp_path,
        include_stock_price=True,
        include_filings=True,
        news_headline="Generic market article",
        news_summary="Broad market commentary without the storage theme.",
    )

    def fake_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        output_path = data_dir / "news-events.json"
        query = str(kwargs.get("query") or "")
        if query:
            rows = [
                {
                    "timestamp": "2026-07-05T09:30:00+00:00",
                    "headline": "STX hard drive demand falls as cloud buyers cut orders",
                    "summary": "Weak AI infrastructure spending pressures storage demand.",
                    "symbols": ["STX"],
                    "source_url": "https://example.com/stx-reverse",
                },
                {
                    "timestamp": "2026-07-05T09:31:00+00:00",
                    "headline": "STX storage demand debate continues",
                    "summary": "Analysts debate whether data center storage demand is changing.",
                    "symbols": ["STX"],
                    "source_url": "https://example.com/stx-neutral",
                },
            ]
        else:
            rows = [
                {
                    "timestamp": "2026-07-05T09:00:00+00:00",
                    "headline": "Generic STX market news",
                    "summary": "Symbol-only news without storage demand direction.",
                    "symbols": ["STX"],
                    "source_url": "https://example.com/stx",
                }
            ]
        output_path.write_text(
            json.dumps({"provider": "newsapi", "rows": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", len(rows), output_path, [])

    run_result = run_research_task(
        output_dir=tmp_path,
        symbol="STX",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=fake_news_pull,
        pull_filings=_fake_filings_pull,
    )

    assert run_result.candidate.topic_news_review_ready is True
    assert run_result.candidate.next_command == "lychee research verify --symbol STX"
    assert "不要重复刷新" in run_result.assessment.next_decision
    assert "主题新闻过滤: 本次拉取 2 条，2 条进入相关新闻。" in run_result.detail
    assert "- 刷新主题新闻: lychee data pull news" not in run_result.detail

    verify_result = verify_research_task(
        output_dir=tmp_path,
        symbol="STX",
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
    )

    assert not any(
        "research run --symbol STX --force" in command
        for command in verify_result.decision_board.next_commands
    )
    assert any(
        "pending-evidence --symbol STX" in command
        for command in verify_result.decision_board.next_commands
    )


def test_research_run_detail_shows_refreshed_proxy_prices(tmp_path: Path) -> None:
    _write_symbolless_seed(tmp_path)
    _write_live_caches(tmp_path)

    def fake_market_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        output_path = data_dir / "market-prices.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "auto",
                    "rows": [
                        {
                            "symbol": "2800.HK",
                            "date": "2026-07-05",
                            "close": 24.06,
                            "volume": 315211058,
                            "currency": "HKD",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("market", "auto", 1, output_path, [])

    result = run_research_task(
        output_dir=tmp_path,
        name="恒生指数压力观察",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=fake_market_pull,
        pull_news=_fake_news_pull,
    )

    assert "行情: 2800.HK 24.06 HKD | 2026-07-05" in result.detail
    assert "行情: 暂无本地行情。" not in result.detail


def test_research_run_detail_includes_topic_news_for_proxy_theme(
    tmp_path: Path,
) -> None:
    _write_symbolless_seed(tmp_path)
    _write_live_caches(tmp_path, include_proxy_price=True)

    def fake_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        output_path = data_dir / "news-events.json"
        query = str(kwargs.get("query") or "")
        if query:
            output_path.write_text(
                json.dumps(
                    {
                        "provider": "newsapi",
                        "rows": [
                            {
                                "timestamp": "2026-07-05T09:30:00+00:00",
                                "headline": "Hang Seng liquidity improves for Hong Kong stocks",
                                "summary": (
                                    "Hong Kong stocks rose as broad market "
                                    "liquidity improved."
                                ),
                                "symbols": ["MARKET"],
                                "source_url": "https://example.com/hang-seng",
                            },
                            {
                                "timestamp": "2026-07-05T09:31:00+00:00",
                                "headline": "QQQ broad market technology ETF gains",
                                "summary": (
                                    "US broad market technology shares rose "
                                    "with Nasdaq strength."
                                ),
                                "symbols": ["QQQ"],
                                "source_url": "https://example.com/qqq",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return PullResult("news", "newsapi", 2, output_path, [])
        return PullResult("news", "newsapi", 0, output_path, [])

    result = run_research_task(
        output_dir=tmp_path,
        name="恒生指数压力观察",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=fake_news_pull,
    )

    assert "相关新闻" in result.detail
    assert "Hang Seng liquidity improves for Hong Kong stocks" in result.detail
    assert "QQQ broad market technology ETF gains" not in result.detail
    assert "相关新闻: 1 条" in result.detail
    assert "主题新闻过滤: 本次拉取 2 条，1 条进入相关新闻。" in result.detail


def test_research_run_does_not_match_ai_inside_unrelated_words(
    tmp_path: Path,
) -> None:
    _write_hk_tech_seed(tmp_path)
    _write_live_caches(tmp_path, include_hk_tech_price=True)

    def fake_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        output_path = data_dir / "news-events.json"
        query = str(kwargs.get("query") or "")
        if query:
            output_path.write_text(
                json.dumps(
                    {
                        "provider": "newsapi",
                        "rows": [
                            {
                                "timestamp": "2026-07-05T09:30:00+00:00",
                                "headline": (
                                    "Hong Kong technology stocks rise as "
                                    "AI ETF turnover improves"
                                ),
                                "summary": (
                                    "Hong Kong technology shares gained as "
                                    "market turnover improved."
                                ),
                                "symbols": ["MARKET"],
                                "source_url": "https://example.com/hk-tech-stocks",
                            },
                            {
                                "timestamp": "2026-07-05T09:30:30+00:00",
                                "headline": "AWS Summit Hong Kong highlights enterprise AI agents",
                                "summary": (
                                    "Hong Kong technology teams are deploying "
                                    "agentic AI tools."
                                ),
                                "symbols": ["MARKET"],
                                "source_url": "https://example.com/aws-hk-ai",
                            },
                            {
                                "timestamp": "2026-07-05T09:31:00+00:00",
                                "headline": "Domino's China expands Mainland stores",
                                "summary": (
                                    "The Hong Kong-listed restaurant operator "
                                    "reported Mainland sales momentum."
                                ),
                                "symbols": ["MARKET"],
                                "source_url": "https://example.com/dominos",
                            },
                            {
                                "timestamp": "2026-07-05T09:32:00+00:00",
                                "headline": "Dubai tops Asian crypto hubs",
                                "summary": (
                                    "Dubai overtakes Singapore and Hong Kong "
                                    "for licensed crypto firms."
                                ),
                                "symbols": ["MARKET"],
                                "source_url": "https://example.com/dubai",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return PullResult("news", "newsapi", 4, output_path, [])
        return PullResult("news", "newsapi", 0, output_path, [])

    result = run_research_task(
        output_dir=tmp_path,
        name="港股科技板块观察",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=fake_news_pull,
    )

    assert "Hong Kong technology stocks rise as AI ETF turnover improves" in result.detail
    related_section = result.detail.split("相关新闻", 1)[1].split("离题/已过滤", 1)[0]
    assert "AWS Summit Hong Kong highlights enterprise AI agents" not in related_section
    assert "Domino's China expands Mainland stores" not in related_section
    assert "Dubai tops Asian crypto hubs" not in related_section
    assert "主题新闻过滤: 本次拉取 4 条，1 条进入相关新闻。" in result.detail


def test_verify_research_task_keeps_cross_market_ai_discovery_out_of_pending(
    tmp_path: Path,
) -> None:
    _write_hk_tech_seed(tmp_path)
    _write_live_caches(tmp_path, include_hk_tech_price=True)
    data_dir = tmp_path / "data"
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T08:00:00+00:00",
                        "headline": "US AI capex cools for Nasdaq giants",
                        "summary": "US technology companies slowed AI infrastructure spending.",
                        "symbols": ["QQQ"],
                        "source_url": "https://example.com/us-ai",
                    },
                    {
                        "timestamp": "2026-07-05T09:30:00+00:00",
                        "headline": (
                            "Hong Kong technology stocks rise as "
                            "AI ETF turnover improves"
                        ),
                        "summary": (
                            "Hong Kong technology shares gained as "
                            "market turnover improved."
                        ),
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/hk-tech-stocks",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = verify_research_task(
        output_dir=tmp_path,
        name="港股科技板块观察",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    support_text = "\n".join(result.evidence_board["support"])
    risk_text = "\n".join(result.evidence_board["risk"])
    off_topic_text = "\n".join(result.evidence_board["off_topic"])
    assert (
        "新闻: Hong Kong technology stocks rise as AI ETF turnover improves"
        in support_text
    )
    assert "Hong Kong technology stocks rise as AI ETF turnover improves" not in risk_text
    assert "新闻待判定: US AI capex cools for Nasdaq giants" not in risk_text
    assert "新闻待查: US AI capex cools for Nasdaq giants" in off_topic_text


def test_verify_research_task_filters_non_financial_technology_news_for_etf(
    tmp_path: Path,
) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource(provider="test-llm", market="HK", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股中国科技与资金流观察",
                markets=["HK"],
                summary="观察港股科技和资金流是否影响宽基 ETF。",
                evidence=["news_001"],
                sectors=["ETF"],
                risk_flags=["主题噪音"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="盈富基金",
                symbol="2800.HK",
                market="HK",
                asset_type="ETF",
                related_theme="港股中国科技与资金流观察",
                why_watch="可作为港股大盘情绪参照，区分科技主题与整体市场压力。",
                evidence=["news_001"],
                risk_flags=["ETF 只适合观察市场方向"],
                next_actions=["对比恒生科技和恒生指数"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery-today.json")
    _write_live_caches(tmp_path, include_proxy_price=True)
    data_dir = tmp_path / "data"
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:30:00+00:00",
                        "headline": "SkillCloak hides malicious AI agent tools",
                        "summary": (
                            "Researchers at the Hong Kong University of Science "
                            "and Technology disclosed an AI plugin issue."
                        ),
                        "symbols": ["2800.HK"],
                        "source_url": "https://example.com/skillcloak",
                    },
                    {
                        "timestamp": "2026-07-05T09:31:00+00:00",
                        "headline": "香港科技登陸歐洲",
                        "summary": "香港創科公司參加法國巴黎科技展。",
                        "symbols": ["2800.HK"],
                        "source_url": "https://example.com/hk-tech-expo",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = verify_research_task(
        output_dir=tmp_path,
        symbol="2800.HK",
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    risk_text = "\n".join(result.evidence_board["risk"])
    off_topic_text = "\n".join(result.evidence_board["off_topic"])
    assert "SkillCloak hides malicious AI agent tools" not in risk_text
    assert "香港科技登陸歐洲" not in risk_text
    assert "新闻待查: SkillCloak hides malicious AI agent tools" in off_topic_text
    assert "新闻待查: 香港科技登陸歐洲" in off_topic_text
    research_dir = tmp_path / "research"
    research_dir.mkdir(exist_ok=True)
    (research_dir / "research-run-20260705-110100Z.json").write_text(
        json.dumps(
            {
                "created_at": "2026-07-05T11:01:00+00:00",
                "status": "completed",
                "candidate": {
                    "display_name": "盈富基金",
                    "market": "HK",
                    "symbol": "2800.HK",
                    "proxy_symbols": [],
                    "topic_news_exhausted": True,
                },
                "assessment": {"consistency": "topic_news_exhausted"},
                "actions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = verify_research_task(
        output_dir=tmp_path,
        symbol="2800.HK",
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
    )
    assert "刷新主题新闻" not in "\n".join(result.decision_board.next_steps)
    assert not any(
        "research run --symbol 2800.HK --force" in command
        for command in result.decision_board.next_commands
    )


def test_evidence_change_marks_content_replacement_as_changed(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir(parents=True)
    (research_dir / "research-verification-20260704-010000Z.json").write_text(
        json.dumps(
            {
                "created_at": "2026-07-04T01:00:00+00:00",
                "candidate": {
                    "display_name": "Invesco QQQ Trust",
                    "market": "US",
                    "symbol": "QQQ",
                    "proxy_symbols": [],
                },
                "evidence_board": {
                    "support": ["QQQ 712.60 USD | 2026-07-02 | 成交量 50959800"],
                    "risk": ["新闻待判定: Old QQQ headline 命中主题但方向未明。"],
                    "missing": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    candidate = CandidateCheck(
        display_name="Invesco QQQ Trust",
        market="US",
        symbol="QQQ",
        proxy_symbols=[],
        evidence_count=1,
        gap_count=0,
        data_gaps=[],
        status="ready",
        explanation="",
        beginner_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
        why_it_matters="",
        observation_entry="QQQ",
        what_to_check="",
        next_step="",
        priority="P2 先复核证据",
        evidence_status="",
    )

    change = build_research_evidence_change(
        output_dir=tmp_path,
        candidate=candidate,
        evidence_board={
            "support": ["QQQ 722.82 USD | 2026-07-06 | 成交量 30220428"],
            "risk": ["新闻待判定: New QQQ headline 命中主题但方向未明。"],
            "missing": [],
        },
    )

    assert change.status != "unchanged"
    assert change.status_label != "证据未变化"
    assert change.added["support"]
    assert change.removed["support"]


def test_research_verification_artifacts_do_not_overwrite_with_same_second(
    tmp_path: Path,
) -> None:
    _write_stock_seed(tmp_path)
    _write_live_caches(tmp_path, include_stock_price=True, include_filings=True)
    now = datetime(2026, 7, 5, 11, 0, tzinfo=UTC)

    first = verify_research_task(output_dir=tmp_path, symbol="STX", now=now)
    second = verify_research_task(output_dir=tmp_path, symbol="STX", now=now)

    assert first.artifact_path != second.artifact_path
    assert first.artifact_path.exists()
    assert second.artifact_path.exists()
    assert len(list((tmp_path / "research").glob("research-verification-*.json"))) == 2


def test_beginner_brief_formats_direct_etf_entry_readably() -> None:
    brief = beginner_research_brief(
        [
            ResearchPacket(
                packet_id="research:test:1",
                candidate_id=1,
                created_at="2026-07-05T10:00:00+00:00",
                display_name="纳斯达克100ETF观察",
                symbol="QQQ",
                market="US",
                packet={
                    "candidate": {
                        "asset_type": "ETF",
                        "related_theme": "利率与大盘风险观察",
                        "why_watch": "用于观察科技反弹是否扩散。",
                    },
                    "evidence_ids": ["news_001"],
                    "evidence": [
                        {
                            "headline": "QQQ technology growth rises",
                            "summary": "Technology shares improved with stronger demand.",
                        }
                    ],
                    "data_gaps": [],
                    "next_actions": ["对比 QQQ 与 SPY。", "检查成交量。"],
                },
            )
        ]
    )

    assert "入口: QQQ" in brief
    assert "优先级:" in brief
    assert "排序理由:" in brief
    assert "证据状态:" in brief
    assert "触发原因:" not in brief
    assert "对比 QQQ 与 SPY；检查成交量" in brief
    assert "今日研究任务" in brief


def _write_symbolless_seed(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource(provider="test-llm", market="HK", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股压力观察",
                markets=["HK"],
                summary="港股流动性变化需要先用宽基代理观察。",
                evidence=["news_001"],
                sectors=["Index"],
                risk_flags=["宏观波动"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="恒生指数压力观察",
                symbol=None,
                market="HK",
                asset_type="index",
                related_theme="港股压力观察",
                why_watch="用于观察港股大盘压力。",
                evidence=["news_001"],
                risk_flags=["指数不能直接交易"],
                next_actions=["映射到可交易 ETF"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery-today.json")


def _write_stock_seed(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["US"],
        sources=[DiscoverySource(provider="test-llm", market="US", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 存储需求",
                markets=["US"],
                summary="AI 基础设施扩张可能影响存储设备需求。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=["供应链周期波动"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="Seagate",
                symbol="STX",
                market="US",
                asset_type="stock",
                related_theme="AI 存储需求",
                why_watch="硬盘供需可能改善。",
                evidence=["news_001"],
                risk_flags=["周期行业波动"],
                next_actions=["检查最新行情", "阅读公告"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery-today.json")


def _write_hk_tech_seed(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource(provider="test-llm", market="HK", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股科技与中国资金流观察",
                markets=["HK"],
                summary="港股科技板块可能受 AI 交易和资金流变化影响。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=["跨市场噪音"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="港股科技板块观察",
                symbol=None,
                market="HK",
                asset_type="sector",
                related_theme="港股科技与中国资金流观察",
                why_watch="港股上半年跑输且错过部分 AI 交易，后续需观察是否修复。",
                evidence=["news_001"],
                risk_flags=["主题证据可能较噪"],
                next_actions=["映射到港股科技 ETF"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery-today.json")


def _write_live_caches(
    tmp_path: Path,
    *,
    include_proxy_price: bool = False,
    include_hk_tech_price: bool = False,
    include_stock_price: bool = False,
    include_filings: bool = False,
    news_headline: str = "Market evidence",
    news_summary: str = "Evidence for the candidate.",
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": news_headline,
                        "summary": news_summary,
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/news",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = []
    if include_proxy_price:
        rows.append(
            {
                "symbol": "2800.HK",
                "date": "2026-07-02",
                "close": 18.5,
                "volume": 1000000,
                "currency": "HKD",
            }
        )
    if include_stock_price:
        rows.append(
            {
                "symbol": "STX",
                "date": "2026-07-02",
                "close": 110.5,
                "volume": 3210000,
                "currency": "USD",
            }
        )
    if include_hk_tech_price:
        rows.append(
            {
                "symbol": "3033.HK",
                "date": "2026-07-02",
                "close": 4.4,
                "volume": 2000000,
                "currency": "HKD",
            }
        )
    if rows:
        (data_dir / "market-prices.json").write_text(
            json.dumps({"provider": "auto", "rows": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
    if include_filings:
        (data_dir / "filings.json").write_text(
            json.dumps(
                {
                    "provider": "sec_edgar",
                    "rows": [
                        {
                            "date": "2026-07-01",
                            "company": "Seagate",
                            "form": "10-K",
                            "summary": "STX 在 2026-07-01 提交了 10-K。",
                            "source_url": "https://example.com/filing",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _write_fund_metadata_cache(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "fund-metadata.json").write_text(
        json.dumps(
            {
                "provider": "manual",
                "rows": [
                    {
                        "symbol": "2800.HK",
                        "display_name": "盈富基金",
                        "market": "HK",
                        "tracking_index": "Hang Seng Index",
                        "expense_ratio": "0.10%",
                        "holdings_summary": "跟踪恒生指数成分股",
                        "source_url": "https://example.com/2800",
                        "as_of": "2026-07-05",
                        "provider": "manual",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _fake_market_pull(**kwargs: object) -> PullResult:
    output_dir = kwargs["output_dir"]
    assert isinstance(output_dir, Path)
    return PullResult("market", "auto", 0, output_dir / "data" / "market-prices.json", [])


def _failed_filings_pull(**kwargs: object) -> PullResult:
    output_dir = kwargs["output_dir"]
    assert isinstance(output_dir, Path)
    return PullResult(
        "filings",
        "sec_edgar",
        0,
        output_dir / "data" / "filings.json",
        ["SEC blocked"],
    )


def _fake_filings_pull(**kwargs: object) -> PullResult:
    output_dir = kwargs["output_dir"]
    assert isinstance(output_dir, Path)
    return PullResult("filings", "sec_edgar", 1, output_dir / "data" / "filings.json", [])


def _fake_news_pull(**kwargs: object) -> PullResult:
    output_dir = kwargs["output_dir"]
    assert isinstance(output_dir, Path)
    return PullResult("news", "auto", 0, output_dir / "data" / "news-events.json", [])


def _write_explicit_symbol_pool_seed(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["US"],
        sources=[DiscoverySource(provider="test-llm", market="US", description="测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 基础设施扩散",
                markets=["US"],
                summary="AI 基础设施主题需要下钻多个公司。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=[],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="NVIDIA",
                symbol="NVDA",
                market="US",
                asset_type="stock",
                related_theme="AI 基础设施扩散",
                why_watch="用算力芯片龙头校验主题。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["下钻 NVDA"],
                confidence="medium",
                recommendation="research",
            ),
            DiscoveryCandidate(
                display_name="Seagate",
                symbol="STX",
                market="US",
                asset_type="stock",
                related_theme="AI 存储需求",
                why_watch="硬盘供需可能改善。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["下钻 STX"],
                confidence="medium",
                recommendation="research",
            ),
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery.json")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "auto",
                "rows": [
                    {
                        "symbol": "NVDA",
                        "date": "2026-07-02",
                        "close": 194.83,
                        "volume": 142068700,
                        "currency": "USD",
                    },
                    {
                        "symbol": "STX",
                        "date": "2026-07-02",
                        "close": 110.5,
                        "volume": 3210000,
                        "currency": "USD",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "AI chip data center demand supports Nvidia",
                        "summary": (
                            "Artificial intelligence infrastructure demand remains "
                            "relevant."
                        ),
                        "symbols": ["NVDA"],
                        "source_url": "https://example.com/nvda",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
