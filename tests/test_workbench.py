import json
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.live_data import PullResult
from lychee_alphadesk.core.research import ResearchPacket
from lychee_alphadesk.core.research_db import write_discovery_research_run
from lychee_alphadesk.core.workbench import (
    CandidateCheck,
    beginner_research_brief,
    build_research_evidence_change,
    render_research_task_detail,
    run_research_task,
    run_workbench_check,
    verify_research_task,
)


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
    assert '执行命令: lychee research verify --name "恒生指数压力观察"' in (
        result.beginner_brief
    )
    assert "下一步队列" in result.beginner_brief
    assert "2800.HK" in result.beginner_brief
    assert "给新手的读法" not in result.beginner_brief
    assert "怎么理解代理" not in result.beginner_brief
    assert "系统替你问的问题" not in result.beginner_brief
    assert "触发原因:" not in result.beginner_brief
    assert "。；" not in result.beginner_brief

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
        == 'lychee research verify --name "恒生指数压力观察"'
    )


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
    assert any(gate.status == "fail" and gate.name == "数据缺口" for gate in result.gates)
    assert "阻塞任务" in result.beginner_brief
    assert "缺少 STX SEC 公告缓存" in result.beginner_brief
    assert "研究问题:" in result.beginner_brief
    assert "处理动作: 先补齐" in result.beginner_brief
    assert "处理命令: lychee research run --symbol STX --force" in (
        result.beginner_brief
    )


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
    assert not any(
        "行情核验: 缺少本地行情" in item
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
    assert "先运行下钻核验复核证据方向" in candidate.next_step
    assert "P2 先复核证据" in result.beginner_brief
    assert "只有反向或待判定证据" in result.beginner_brief

    detail = render_research_task_detail(candidate, result.deepen_result.packets[0])
    assert "阶段: 先复核证据" in detail
    assert "信号读数: 证据需复核" in detail
    assert "下一步判断: 先运行下钻核验复核证据方向" in detail

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
            return PullResult("news", "newsapi", 3, output_path, [])
        return PullResult("news", "newsapi", 0, output_path, [])

    result = run_research_task(
        output_dir=tmp_path,
        name="港股科技板块观察",
        force=True,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
        pull_market=_fake_market_pull,
        pull_news=fake_news_pull,
    )

    assert "AWS Summit Hong Kong highlights enterprise AI agents" in result.detail
    assert "Domino's China expands Mainland stores" not in result.detail
    assert "Dubai tops Asian crypto hubs" not in result.detail
    assert "主题新闻过滤: 本次拉取 3 条，1 条进入相关新闻。" in result.detail


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
                        "headline": "AWS Summit Hong Kong highlights enterprise AI agents",
                        "summary": (
                            "Hong Kong technology teams are deploying "
                            "agentic AI tools."
                        ),
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/aws-hk-ai",
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

    risk_text = "\n".join(result.evidence_board["risk"])
    assert "新闻待判定: AWS Summit Hong Kong highlights enterprise AI agents" in risk_text
    assert "新闻待判定: US AI capex cools for Nasdaq giants" not in risk_text
    assert "新闻待查: US AI capex cools for Nasdaq giants" in risk_text


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
