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
from lychee_alphadesk.core.research import (
    _related_news,
    _research_topic_terms,
    deepen_research_queue,
    fill_research_data_gaps,
)
from lychee_alphadesk.core.research_db import ResearchQueueItem, write_discovery_research_run
from lychee_alphadesk.providers.demo import NewsEvent


def test_research_topic_terms_match_manual_chinese_hk_platform_evidence() -> None:
    item = ResearchQueueItem(
        candidate_id=3,
        run_id="run-1",
        created_at="2026-07-18T00:00:00+00:00",
        display_name="Tencent",
        symbol="0700.HK",
        market="HK",
        asset_type="stock",
        related_theme="中国政策与消费观察",
        why_watch="大型港股中国平台公司，可观察跨市场情绪。",
        evidence=[],
        risk_flags=[],
        next_actions=[],
        confidence="medium",
        status="new",
    )
    event = NewsEvent(
        timestamp="2026-07-18T01:40:10+00:00",
        headline="港股中国平台公司2026年第一季度业绩改善",
        summary="腾讯官方业绩报告显示营收、净利润和经营现金流改善。",
        symbols=["0700.HK"],
        source_url="https://example.com/tencent-q1.pdf",
        is_symbol_scoped=True,
    )

    related = _related_news(
        item.symbol,
        item.display_name,
        item.market,
        item.asset_type,
        [event],
        topic_terms=_research_topic_terms(item),
    )

    assert "港股" in _research_topic_terms(item)
    assert "中国" in _research_topic_terms(item)
    assert related == [event.__dict__]


def test_deepen_research_queue_builds_source_backed_packet(tmp_path: Path) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    _write_live_caches(tmp_path)

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.count == 1
    assert result.artifact_path is not None
    assert result.artifact_path.exists()
    packet = result.packets[0].packet
    assert packet["evidence_ids"] == ["news_001"]
    assert packet["evidence"][0]["headline"] == "AI storage demand rises"
    assert packet["local_data"]["price"]["symbol"] == "STX"
    assert "缺少 STX SEC 公告缓存。" in packet["data_gaps"]
    assert "研究深挖包只用于决定下一步研究什么" in packet["disclaimer"]


def test_deepen_research_queue_handles_symbolless_candidates(tmp_path: Path) -> None:
    _write_discovery_seed(tmp_path, symbol=None)
    _write_live_caches(tmp_path)

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    packet = result.packets[0].packet
    assert packet["candidate"]["symbol"] is None
    assert "缺少可直接拉取的证券代码" in packet["data_gaps"][0]
    assert "先把观察对象映射到可交易代码" in packet["next_actions"][0]


def test_deepen_research_queue_adds_auditable_proxy_mappings(
    tmp_path: Path,
) -> None:
    _write_symbolless_mapping_seed(tmp_path)
    _write_live_caches(tmp_path)

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    packet = result.packets[0].packet
    symbol_mapping = packet["local_data"]["symbol_mapping"]

    assert packet["candidate"]["symbol"] is None
    assert symbol_mapping[0]["symbol"] == "2800.HK"
    assert symbol_mapping[0]["requires_review"] is True
    assert symbol_mapping[0]["latest_price"] is None
    assert "代理标的行情尚未补齐: 2800.HK。" in packet["data_gaps"]
    assert "先审查代理标的映射，再把通过的代理标的加入下钻研究。" in packet[
        "next_actions"
    ][0]


def test_deepen_research_queue_prefers_ready_packets_within_candidate_pool(
    tmp_path: Path,
) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource("test-llm", "HK", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股科技观察",
                markets=["HK"],
                summary="测试深挖排序。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=[],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="高证据但缺缓存",
                symbol="HIGH.HK",
                market="HK",
                asset_type="ETF",
                related_theme="港股科技观察",
                why_watch="证据 ID 还没有落到本地缓存。",
                evidence=["news_998", "news_999"],
                risk_flags=[],
                next_actions=["先补证据"],
                confidence="medium",
                recommendation="research",
            ),
            DiscoveryCandidate(
                display_name="可直接下钻",
                symbol="READY.HK",
                market="HK",
                asset_type="ETF",
                related_theme="港股科技观察",
                why_watch="证据和行情都已缓存。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["下钻核验"],
                confidence="medium",
                recommendation="research",
            ),
        ],
        warnings=[],
        next_actions=[],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(
        report,
        tmp_path,
        tmp_path / "data" / "discovery-today.json",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Ready ETF has cached evidence",
                        "summary": "Cached research evidence.",
                        "symbols": ["READY.HK"],
                        "source_url": "https://example.com/ready",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_market_cache(tmp_path, ["READY.HK"])

    result = deepen_research_queue(
        output_dir=tmp_path,
        limit=1,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.count == 1
    assert result.packets[0].display_name == "可直接下钻"
    assert result.packets[0].packet["data_gaps"] == []


def test_deepen_research_queue_prefers_latest_related_news(tmp_path: Path) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    rows = [
        {
            "timestamp": f"2026-07-0{index}T09:00:00+00:00",
            "headline": f"Old STX storage note {index}",
            "summary": "Older storage note.",
            "symbols": ["STX"],
            "source_url": f"https://example.com/old-{index}",
        }
        for index in range(1, 6)
    ]
    rows.append(
        {
            "timestamp": "2026-07-06T09:00:00+00:00",
            "headline": "Newest AI storage demand improves",
            "summary": "Fresh data-center storage demand increased.",
            "symbols": ["STX"],
            "source_url": "https://example.com/newest",
        }
    )
    (data_dir / "news-events.json").write_text(
        json.dumps({"provider": "newsapi", "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_market_cache(tmp_path, ["STX"])
    _write_filings_cache(tmp_path, ["STX"])

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    related_news = result.packets[0].packet["local_data"]["related_news"]
    assert related_news[0]["headline"] == "Newest AI storage demand improves"
    assert len(related_news) == 5


def test_deepen_research_queue_prioritizes_topic_related_news(
    tmp_path: Path,
) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    rows = [
        {
            "timestamp": f"2026-07-0{index}T09:00:00+00:00",
            "headline": f"Generic STX market note {index}",
            "summary": "Generic symbol-only note.",
            "symbols": ["STX"],
            "source_url": f"https://example.com/generic-{index}",
        }
        for index in range(1, 6)
    ]
    rows.append(
        {
            "timestamp": "2026-07-01T09:00:00+00:00",
            "headline": "AI storage demand improves for Seagate",
            "summary": "Data-center storage demand increased.",
            "symbols": ["STX"],
            "source_url": "https://example.com/topic",
        }
    )
    (data_dir / "news-events.json").write_text(
        json.dumps({"provider": "newsapi", "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_market_cache(tmp_path, ["STX"])
    _write_filings_cache(tmp_path, ["STX"])

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    related_news = result.packets[0].packet["local_data"]["related_news"]
    assert related_news[0]["headline"] == "AI storage demand improves for Seagate"
    assert len(related_news) == 5


def test_deepen_research_queue_rejects_broad_hk_technology_news_for_index_theme(
    tmp_path: Path,
) -> None:
    _write_hk_technology_index_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    rows = [
        {
            "timestamp": "2026-07-05T08:00:00+00:00",
            "headline": "Hong Kong technology researchers publish AI security study",
            "summary": (
                "Researchers at a Hong Kong university studied AI coding agent "
                "plugins for software supply-chain security."
            ),
            "symbols": ["2800.HK", "3033.HK"],
            "source_url": "https://example.com/hk-ai-security",
        },
        {
            "timestamp": "2026-07-05T09:00:00+00:00",
            "headline": "Hong Kong stocks rise as Hang Seng liquidity improves",
            "summary": "Hang Seng turnover improved as Hong Kong shares gained.",
            "symbols": ["MARKET"],
            "source_url": "https://example.com/hk-stocks",
        },
    ]
    (data_dir / "news-events.json").write_text(
        json.dumps({"provider": "newsapi", "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_market_cache(tmp_path, ["2800.HK", "3033.HK"])

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    related_news = result.packets[0].packet["local_data"]["related_news"]
    headlines = [row["headline"] for row in related_news]
    assert "Hong Kong stocks rise as Hang Seng liquidity improves" in headlines
    assert "Hong Kong technology researchers publish AI security study" not in headlines


def test_deepen_research_queue_accepts_symbol_scoped_hk_topic_news_without_market_literal(
    tmp_path: Path,
) -> None:
    _write_hk_tencent_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    rows = [
        {
            "timestamp": "2026-07-05T09:00:00+00:00",
            "headline": "Tencent platform liquidity improves with AI cloud demand",
            "summary": "Tencent cloud demand and platform liquidity are improving.",
            "symbols": ["0700.HK"],
            "source_url": "https://example.com/tencent-platform",
            "is_symbol_scoped": True,
        },
    ]
    (data_dir / "news-events.json").write_text(
        json.dumps({"provider": "newsapi", "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_market_cache(tmp_path, ["0700.HK"])

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    packet = result.packets[0].packet
    related_news = packet["local_data"]["related_news"]
    assert [row["source_url"] for row in related_news] == [
        "https://example.com/tencent-platform"
    ]
    assert "缺少可审计新闻证据，需先刷新市场级或个股新闻缓存。" not in packet[
        "data_gaps"
    ]


def test_deepen_research_queue_rejects_unscoped_hk_news_without_market_literal(
    tmp_path: Path,
) -> None:
    _write_hk_tencent_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "legacy-newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T10:00:00+00:00",
                        "headline": "Tencent platform liquidity improves with AI cloud demand",
                        "summary": "Tencent cloud demand and platform liquidity are improving.",
                        "symbols": ["0700.HK"],
                        "source_url": "https://example.com/legacy-batch-row",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_market_cache(tmp_path, ["0700.HK"])

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert "缺少可审计新闻证据，需先刷新市场级或个股新闻缓存。" in result.packets[
        0
    ].packet["data_gaps"]


def test_deepen_research_queue_requires_hkex_filings_for_hk_stocks(
    tmp_path: Path,
) -> None:
    _write_hk_tencent_seed(tmp_path)
    (tmp_path / "data").mkdir()
    _write_market_cache(tmp_path, ["0700.HK"])

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert "缺少 0700.HK HKEX 公司公告缓存。" in result.packets[0].packet["data_gaps"]
    assert "缺少 0700.HK 港股数字财务快照；请使用人工财务资料向导。" in result.packets[0].packet[
        "data_gaps"
    ]


def test_deepen_research_queue_requires_cninfo_filings_for_cn_stocks(
    tmp_path: Path,
) -> None:
    _write_cn_ping_an_seed(tmp_path)
    (tmp_path / "data").mkdir()
    _write_market_cache(tmp_path, ["000001.SZ"])

    result = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert "缺少 000001.SZ 巨潮公司公告缓存。" in result.packets[0].packet["data_gaps"]
    assert (
        "缺少 000001.SZ A 股财务快照；需要可用的 Tushare 权限或人工核验。"
        in result.packets[0].packet["data_gaps"]
    )


def test_fill_research_data_gaps_pulls_cninfo_announcements_for_cn_stocks(
    tmp_path: Path,
) -> None:
    _write_cn_ping_an_seed(tmp_path)
    (tmp_path / "data").mkdir()
    _write_market_cache(tmp_path, ["000001.SZ"])

    def pull_filings(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["000001.SZ"]
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        output_path = output_dir / "data" / "filings.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "cninfo",
                    "rows": [
                        {
                            "date": "2026-07-05",
                            "company": "平安银行",
                            "form": "巨潮公告",
                            "summary": "巨潮资讯公告: 董事会决议公告",
                            "source_url": "https://example.com/pingan-board.pdf",
                            "symbol": "000001.SZ",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("filings", "cninfo", 1, output_path, [])

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        fill_news=False,
        pull_filings=pull_filings,
    )
    after = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.filing_symbols == ["000001.SZ"]
    assert "缺少 000001.SZ 巨潮公司公告缓存。" not in after.packets[0].packet[
        "data_gaps"
    ]


def test_fill_research_data_gaps_pulls_hkex_announcements_for_hk_stocks(
    tmp_path: Path,
) -> None:
    _write_hk_tencent_seed(tmp_path)
    (tmp_path / "data").mkdir()
    _write_market_cache(tmp_path, ["0700.HK"])

    def pull_filings(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["0700.HK"]
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        output_path = output_dir / "data" / "filings.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "hkexnews",
                    "rows": [
                        {
                            "date": "2026-07-05",
                            "company": "TENCENT",
                            "form": "HKEX 公告",
                            "summary": "HKEXnews 公告: Quarterly Results",
                            "source_url": "https://example.com/tencent-results",
                            "symbol": "0700.HK",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("filings", "hkexnews", 1, output_path, [])

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        fill_news=False,
        pull_filings=pull_filings,
    )
    after = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )

    assert result.filing_symbols == ["0700.HK"]
    assert "缺少 0700.HK HKEX 公司公告缓存。" not in after.packets[0].packet[
        "data_gaps"
    ]


def test_fill_research_data_gaps_pulls_proxy_mapping_prices_without_mutating_candidate(
    tmp_path: Path,
) -> None:
    _write_symbolless_mapping_seed(tmp_path)
    _write_live_caches(tmp_path, include_market=False, include_filings=False)

    before = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )
    assert "代理标的行情尚未补齐: 2800.HK。" in before.packets[0].packet["data_gaps"]

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        pull_market=_fake_proxy_market_pull,
        pull_filings=_fake_filings_pull,
    )
    after = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
    )

    mapping_action = next(
        action for action in result.actions if action.action_type == "symbol_mapping"
    )
    packet = after.packets[0].packet
    symbol_mapping = packet["local_data"]["symbol_mapping"]

    assert result.market_symbols == ["2800.HK"]
    assert result.symbol_mapping_candidates == ["恒生指数压力观察"]
    assert mapping_action.status == "mapped"
    assert mapping_action.symbols == ["2800.HK"]
    assert "代理标的行情尚未补齐: 2800.HK。" not in packet["data_gaps"]
    assert symbol_mapping[0]["latest_price"]["symbol"] == "2800.HK"
    assert packet["candidate"]["symbol"] is None


def test_fill_research_data_gaps_reduces_missing_price_and_filing_gaps(
    tmp_path: Path,
) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    _write_live_caches(tmp_path, include_market=False, include_filings=False)

    before = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )
    before_gaps = before.packets[0].packet["data_gaps"]
    assert "缺少 STX 本地行情缓存。" in before_gaps
    assert "缺少 STX SEC 公告缓存。" in before_gaps

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        pull_market=_fake_market_pull,
        pull_filings=_fake_filings_pull,
    )
    after = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
    )
    after_gaps = after.packets[0].packet["data_gaps"]

    assert result.candidates_checked == 1
    assert result.market_symbols == ["STX"]
    assert result.filing_symbols == ["STX"]
    assert "缺少 STX 本地行情缓存。" not in after_gaps
    assert "缺少 STX SEC 公告缓存。" not in after_gaps


def test_fill_research_data_gaps_refreshes_missing_symbol_news(
    tmp_path: Path,
) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    _write_market_cache(tmp_path, ["STX"])
    _write_filings_cache(tmp_path, ["STX"])
    (data_dir / "financials.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "symbol": "STX",
                        "company": "Seagate",
                        "form": "10-K",
                        "currency": "USD",
                        "revenue": 100,
                        "net_income": 10,
                        "operating_cash_flow": 20,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    before = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 0, tzinfo=UTC),
    )
    assert "部分 discovery 证据 ID 未在当前本地新闻缓存中找到。" in (
        before.packets[0].packet["data_gaps"]
    )
    assert "缺少可审计新闻证据，需先刷新市场级或个股新闻缓存。" in (
        before.packets[0].packet["data_gaps"]
    )

    def fake_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        assert kwargs["symbols"] == ["STX"]
        assert kwargs["provider_id"] == "auto"
        assert "storage" in str(kwargs["query"])
        (output_dir / "data" / "news-events.json").write_text(
            json.dumps(
                {
                    "provider": "finnhub",
                    "rows": [
                        {
                            "timestamp": "2026-07-05T10:00:00+00:00",
                            "headline": "AI storage demand improves for hard drive makers",
                            "summary": "Storage demand improved for STX suppliers.",
                            "symbols": ["STX"],
                            "source_url": "https://example.com/stx-news",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult(
            "news",
            "finnhub",
            1,
            output_dir / "data" / "news-events.json",
            [],
        )

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        pull_news=fake_news_pull,
    )
    after = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
    )

    news_action = next(
        action for action in result.actions if action.action_type == "news_events"
    )
    assert result.news_symbols == ["STX"]
    assert result.unresolved_news_symbols == []
    assert news_action.status == "pulled"
    assert news_action.count == 1
    assert after.packets[0].packet["data_gaps"] == []


def test_fill_research_data_gaps_pulls_us_financial_snapshot(
    tmp_path: Path,
) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    _write_live_caches(
        tmp_path,
        include_market=True,
        include_filings=True,
        include_financials=False,
    )

    def fake_financials_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        assert kwargs["symbols"] == ["STX"]
        assert kwargs["force"] is False
        output_path = output_dir / "data" / "financials.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "sec_edgar",
                    "rows": [
                        {
                            "symbol": "STX",
                            "company": "Seagate",
                            "form": "10-K",
                            "currency": "USD",
                            "revenue": 100,
                            "net_income": 10,
                            "operating_cash_flow": 20,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("financials", "sec_edgar", 1, output_path, [])

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        fill_news=False,
        pull_financials=fake_financials_pull,
    )
    after = deepen_research_queue(
        output_dir=tmp_path,
        now=datetime(2026, 7, 5, 11, 5, tzinfo=UTC),
    )

    financial_action = next(
        action for action in result.actions if action.action_type == "sec_financials"
    )
    assert result.financial_symbols == ["STX"]
    assert financial_action.status == "pulled"
    assert financial_action.count == 1
    assert "缺少 STX SEC XBRL 财务快照。" not in after.packets[0].packet["data_gaps"]


def test_fill_research_data_gaps_marks_off_topic_news_as_unresolved(
    tmp_path: Path,
) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    _write_market_cache(tmp_path, ["STX"])
    _write_filings_cache(tmp_path, ["STX"])

    def fake_news_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        (output_dir / "data" / "news-events.json").write_text(
            json.dumps(
                {
                    "provider": "newsapi",
                    "rows": [
                        {
                            "timestamp": "2026-07-05T10:00:00+00:00",
                            "headline": "Unrelated developer tool repository update",
                            "summary": "A code project published a new release.",
                            "symbols": ["STX"],
                            "source_url": "https://example.com/off-topic",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult(
            "news",
            "newsapi",
            1,
            output_dir / "data" / "news-events.json",
            [],
        )

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        pull_news=fake_news_pull,
    )

    news_action = next(
        action for action in result.actions if action.action_type == "news_events"
    )
    assert result.news_symbols == ["STX"]
    assert result.unresolved_news_symbols == ["STX"]
    assert news_action.status == "partial"
    assert "STX" in news_action.message
    assert any("主题新闻证据" in warning for warning in news_action.warnings)


def test_fill_research_data_gaps_marks_empty_warning_pull_as_failed(
    tmp_path: Path,
) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    _write_live_caches(tmp_path, include_market=False, include_filings=False)

    def empty_filings_pull(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        return PullResult(
            "filings",
            "sec_edgar",
            0,
            output_dir / "data" / "filings.json",
            ["SEC blocked"],
        )

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        pull_market=_fake_market_pull,
        pull_filings=empty_filings_pull,
    )

    filing_action = next(
        action for action in result.actions if action.action_type == "sec_filings"
    )
    assert filing_action.status == "failed"
    assert filing_action.message == "公司公告补齐未完成。"


def test_fill_research_data_gaps_reports_cached_empty_market_as_no_data(
    tmp_path: Path,
) -> None:
    _write_discovery_seed(tmp_path, symbol="STX")
    _write_live_caches(tmp_path, include_market=False, include_filings=True)

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

    result = fill_research_data_gaps(
        output_dir=tmp_path,
        pull_market=cached_empty_market_pull,
    )

    market_action = next(
        action for action in result.actions if action.action_type == "market_prices"
    )
    assert market_action.status == "no_data"
    assert market_action.message == "行情暂无可用数据，保质期内不会重复请求。"


def _write_discovery_seed(tmp_path: Path, symbol: str | None) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["US"],
        sources=[
            DiscoverySource(
                provider="test-llm",
                market="US",
                description="测试来源",
            )
        ],
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
                symbol=symbol,
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
    write_discovery_research_run(
        report,
        tmp_path,
        tmp_path / "data" / "discovery-today.json",
    )


def _write_symbolless_mapping_seed(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[
            DiscoverySource(
                provider="test-llm",
                market="HK",
                description="测试来源",
            )
        ],
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
    write_discovery_research_run(
        report,
        tmp_path,
        tmp_path / "data" / "discovery-today.json",
    )


def _write_hk_technology_index_seed(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource("test-llm", "HK", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股科技与流动性观察",
                markets=["HK"],
                summary="港股科技板块和恒生指数流动性需要一起观察。",
                evidence=["news_001"],
                sectors=["Technology", "Index"],
                risk_flags=["市场噪音"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="恒生指数压力观察",
                symbol=None,
                market="HK",
                asset_type="index",
                related_theme="港股科技与流动性观察",
                why_watch="用于观察港股科技和大盘流动性。",
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
    write_discovery_research_run(
        report,
        tmp_path,
        tmp_path / "data" / "discovery-today.json",
    )


def _write_hk_tencent_seed(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource("test-llm", "HK", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 云与平台流动性观察",
                markets=["HK"],
                summary="观察 AI 云需求与平台流动性是否互相印证。",
                evidence=[],
                sectors=["Technology"],
                risk_flags=["市场噪音"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="Tencent",
                symbol="0700.HK",
                market="HK",
                asset_type="stock",
                related_theme="AI 云与平台流动性观察",
                why_watch="观察 AI 云需求与平台流动性。",
                evidence=[],
                risk_flags=["市场噪音"],
                next_actions=["复核主题新闻"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(
        report,
        tmp_path,
        tmp_path / "data" / "discovery-today.json",
    )


def _write_cn_ping_an_seed(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["CN"],
        sources=[DiscoverySource("test-llm", "CN", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="中国金融观察",
                markets=["CN"],
                summary="观察 A 股公司公告能否进入研究证据链。",
                evidence=[],
                sectors=["Financials"],
                risk_flags=["测试"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="平安银行",
                symbol="000001.SZ",
                market="CN",
                asset_type="stock",
                related_theme="中国金融观察",
                why_watch="用于验证 A 股公司公告数据链。",
                evidence=[],
                risk_flags=["测试"],
                next_actions=["拉取行情", "拉取公司公告"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集证据"],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(
        report,
        tmp_path,
        tmp_path / "data" / "discovery-today.json",
    )


def _write_live_caches(
    tmp_path: Path,
    *,
    include_market: bool = True,
    include_filings: bool = False,
    include_financials: bool = True,
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
                        "headline": "AI storage demand rises",
                        "summary": "Cloud infrastructure demand may affect hard drives.",
                        "symbols": ["STX"],
                        "source_url": "https://example.com/storage",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if include_market:
        _write_market_cache(tmp_path, ["STX"])
    if include_filings:
        _write_filings_cache(tmp_path, ["STX"])
    if include_financials:
        (data_dir / "financials.json").write_text(
            json.dumps(
                {
                    "provider": "sec_edgar",
                    "rows": [
                        {
                            "symbol": "STX",
                            "company": "Seagate",
                            "form": "10-K",
                            "currency": "USD",
                            "revenue": 100,
                            "net_income": 10,
                            "operating_cash_flow": 20,
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
    assert kwargs["symbols"] == ["STX"]
    _write_market_cache(output_dir, ["STX"])
    return PullResult(
        "market",
        "alpha_vantage",
        1,
        output_dir / "data" / "market-prices.json",
        [],
    )


def _fake_proxy_market_pull(**kwargs: object) -> PullResult:
    output_dir = kwargs["output_dir"]
    assert isinstance(output_dir, Path)
    assert kwargs["symbols"] == ["2800.HK"]
    _write_market_cache(output_dir, ["2800.HK"])
    return PullResult(
        "market",
        "auto",
        1,
        output_dir / "data" / "market-prices.json",
        [],
    )


def _fake_filings_pull(**kwargs: object) -> PullResult:
    output_dir = kwargs["output_dir"]
    assert isinstance(output_dir, Path)
    assert kwargs["symbols"] == ["STX"]
    _write_filings_cache(output_dir, ["STX"])
    return PullResult(
        "filings",
        "sec_edgar",
        1,
        output_dir / "data" / "filings.json",
        [],
    )


def _write_market_cache(tmp_path: Path, symbols: list[str]) -> None:
    rows = [
        {
            "symbol": symbol,
            "date": "2026-07-02",
            "close": 110.5,
            "volume": 3210000,
            "currency": "USD",
        }
        for symbol in symbols
    ]
    (tmp_path / "data" / "market-prices.json").write_text(
        json.dumps({"provider": "alpha_vantage", "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_filings_cache(tmp_path: Path, symbols: list[str]) -> None:
    rows = [
        {
            "date": "2026-07-01",
            "company": "Seagate",
            "form": "10-K",
            "summary": f"{symbol} 在 2026-07-01 提交了 10-K。",
            "source_url": "https://example.com/filing",
        }
        for symbol in symbols
    ]
    (tmp_path / "data" / "filings.json").write_text(
        json.dumps({"provider": "sec_edgar", "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
