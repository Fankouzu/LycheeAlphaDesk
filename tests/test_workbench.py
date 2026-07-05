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
from lychee_alphadesk.core.research_db import write_discovery_research_run
from lychee_alphadesk.core.workbench import run_workbench_check


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
    assert "给新手的读法" in result.beginner_brief
    assert "这不是买入建议" in result.beginner_brief
    assert "代理观察工具" in result.beginner_brief
    assert "2800.HK" in result.beginner_brief

    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["proxy_price_coverage"] == "1/1"


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
    assert "暂时不要下结论" in result.beginner_brief
    assert "缺少 STX SEC 公告缓存" in result.beginner_brief


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


def _write_live_caches(
    tmp_path: Path,
    *,
    include_proxy_price: bool = False,
    include_stock_price: bool = False,
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
                        "headline": "Market evidence",
                        "summary": "Evidence for the candidate.",
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
