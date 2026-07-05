import json
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.research import deepen_research_queue
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


def _write_live_caches(tmp_path: Path) -> None:
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
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "alpha_vantage",
                "rows": [
                    {
                        "symbol": "STX",
                        "date": "2026-07-02",
                        "close": 110.5,
                        "volume": 3210000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
