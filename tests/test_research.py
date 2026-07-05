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
from lychee_alphadesk.core.research import deepen_research_queue, fill_research_data_gaps
from lychee_alphadesk.core.research_db import write_discovery_research_run


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
    assert filing_action.message == "SEC 公告补齐未完成。"


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


def _write_live_caches(
    tmp_path: Path,
    *,
    include_market: bool = True,
    include_filings: bool = False,
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
