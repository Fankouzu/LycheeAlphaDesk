from pathlib import Path

from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.research_db import (
    list_research_queue,
    research_db_path,
    write_discovery_research_run,
)


def test_discovery_report_is_persisted_as_research_queue(tmp_path: Path) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["US", "HK", "CN"],
        sources=[
            DiscoverySource(
                provider="test-llm",
                market="US",
                description="测试来源",
            )
        ],
        themes=[
            DiscoveryTheme(
                name="硬盘价格异常",
                markets=["US"],
                summary="二手硬盘价格上涨可能指向 AI 存储需求。",
                evidence=["价格样本上涨"],
                sectors=["存储"],
                risk_flags=["可能已经反映在股价中"],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="Seagate",
                symbol="STX",
                market="US",
                asset_type="stock",
                related_theme="硬盘价格异常",
                why_watch="近线硬盘供需可能改善。",
                evidence=["价格样本上涨", "主题证据"],
                risk_flags=["周期行业波动"],
                next_actions=["检查财报电话会", "比较 Western Digital"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=["候选仅用于研究"],
        next_actions=["继续收集价格证据"],
        disclaimer="非投资建议。",
    )
    report_path = tmp_path / "data" / "discovery-today.json"

    db_path = write_discovery_research_run(report, tmp_path, report_path)
    queue = list_research_queue(tmp_path)

    assert db_path == research_db_path(tmp_path)
    assert db_path.exists()
    assert len(queue) == 1
    candidate = queue[0]
    assert candidate.display_name == "Seagate"
    assert candidate.symbol == "STX"
    assert candidate.market == "US"
    assert candidate.related_theme == "硬盘价格异常"
    assert candidate.status == "new"
    assert candidate.evidence == ["价格样本上涨", "主题证据"]
    assert candidate.next_actions == ["检查财报电话会", "比较 Western Digital"]
