from pathlib import Path

from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.research_db import (
    list_research_packets,
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


def test_research_queue_defaults_to_latest_candidate_per_observation(
    tmp_path: Path,
) -> None:
    first_report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["US"],
        sources=[DiscoverySource("test-llm", "US", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 存储需求",
                markets=["US"],
                summary="第一次发现的存储线索。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=[],
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
                why_watch="第一次发现。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["旧动作"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=[],
        next_actions=[],
        disclaimer="非投资建议。",
    )
    latest_report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T11:00:00+00:00",
        markets=["US"],
        sources=[DiscoverySource("test-llm", "US", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 存储需求更新",
                markets=["US"],
                summary="更新后的存储线索。",
                evidence=["news_002"],
                sectors=["Technology"],
                risk_flags=[],
                confidence="high",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="Seagate Technology",
                symbol="STX",
                market="US",
                asset_type="stock",
                related_theme="AI 存储需求更新",
                why_watch="更新后的发现。",
                evidence=["news_002"],
                risk_flags=[],
                next_actions=["新动作"],
                confidence="high",
                recommendation="research",
            )
        ],
        warnings=[],
        next_actions=[],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(
        first_report,
        tmp_path,
        tmp_path / "data" / "discovery-first.json",
    )
    write_discovery_research_run(
        latest_report,
        tmp_path,
        tmp_path / "data" / "discovery-latest.json",
    )

    queue = list_research_queue(tmp_path)

    assert len(queue) == 1
    assert queue[0].display_name == "Seagate Technology"
    assert queue[0].symbol == "STX"
    assert queue[0].related_theme == "AI 存储需求更新"
    assert queue[0].next_actions == ["新动作"]


def test_research_queue_groups_obvious_symbolless_topic_variants(
    tmp_path: Path,
) -> None:
    first_report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["CN"],
        sources=[DiscoverySource("test-llm", "CN", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 基础设施扩散",
                markets=["CN"],
                summary="第一次发现的无代码主题。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=[],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="中国AI数据中心供应链观察",
                symbol=None,
                market="CN",
                asset_type="theme",
                related_theme="AI基础设施跨市场扩散观察",
                why_watch="第一次发现。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["旧动作"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=[],
        next_actions=[],
        disclaimer="非投资建议。",
    )
    latest_report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T11:00:00+00:00",
        markets=["CN"],
        sources=[DiscoverySource("test-llm", "CN", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="AI 基础设施外溢",
                markets=["CN"],
                summary="更新后的无代码主题。",
                evidence=["news_002"],
                sectors=["Technology"],
                risk_flags=[],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="中国 AI 数据中心与高科技链条",
                symbol=None,
                market="CN",
                asset_type="theme",
                related_theme="AI 基础设施外溢观察",
                why_watch="更新后的发现。",
                evidence=["news_002"],
                risk_flags=[],
                next_actions=["新动作"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=[],
        next_actions=[],
        disclaimer="非投资建议。",
    )
    write_discovery_research_run(
        first_report,
        tmp_path,
        tmp_path / "data" / "discovery-first.json",
    )
    write_discovery_research_run(
        latest_report,
        tmp_path,
        tmp_path / "data" / "discovery-latest.json",
    )

    queue = list_research_queue(tmp_path)

    assert len(queue) == 1
    assert queue[0].display_name == "中国 AI 数据中心与高科技链条"
    assert queue[0].next_actions == ["新动作"]


def test_research_queue_keeps_distinct_symbolless_topics(
    tmp_path: Path,
) -> None:
    report = DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T10:00:00+00:00",
        markets=["HK"],
        sources=[DiscoverySource("test-llm", "HK", "测试来源")],
        themes=[
            DiscoveryTheme(
                name="港股主题",
                markets=["HK"],
                summary="不同无代码主题。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=[],
                confidence="medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                display_name="港股科技板块观察",
                symbol=None,
                market="HK",
                asset_type="theme",
                related_theme="港股科技与中国资金流观察",
                why_watch="科技板块。",
                evidence=["news_001"],
                risk_flags=[],
                next_actions=["核验科技 ETF"],
                confidence="medium",
                recommendation="research",
            ),
            DiscoveryCandidate(
                display_name="恒生指数压力观察",
                symbol=None,
                market="HK",
                asset_type="theme",
                related_theme="港股流动性与中国科技压力观察",
                why_watch="市场压力。",
                evidence=["news_002"],
                risk_flags=[],
                next_actions=["核验指数压力"],
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
        tmp_path / "data" / "discovery.json",
    )

    queue = list_research_queue(tmp_path)

    assert len(queue) == 2
    assert {item.display_name for item in queue} == {
        "港股科技板块观察",
        "恒生指数压力观察",
    }


def test_research_packets_are_persisted_for_queue_candidates(tmp_path: Path) -> None:
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
    report_path = tmp_path / "data" / "discovery-today.json"
    write_discovery_research_run(report, tmp_path, report_path)

    packet_payload = {
        "candidate": {"display_name": "Seagate", "symbol": "STX"},
        "evidence": [{"id": "news_001", "headline": "AI storage demand rises"}],
        "data_gaps": [],
    }
    db_path = research_db_path(tmp_path)

    from lychee_alphadesk.core.research_db import write_research_packet

    write_research_packet(
        output_dir=tmp_path,
        candidate_id=1,
        packet_id="packet-test-001",
        created_at="2026-07-05T11:00:00+00:00",
        display_name="Seagate",
        symbol="STX",
        market="US",
        packet=packet_payload,
        artifact_path=tmp_path / "research" / "research-packets.json",
    )
    packets = list_research_packets(tmp_path)

    assert db_path.exists()
    assert len(packets) == 1
    assert packets[0].packet_id == "packet-test-001"
    assert packets[0].candidate_id == 1
    assert packets[0].display_name == "Seagate"
    assert packets[0].symbol == "STX"
    assert packets[0].packet["evidence"][0]["id"] == "news_001"
