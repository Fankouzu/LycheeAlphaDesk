import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

import lychee_alphadesk.cli.app as cli_app
from lychee_alphadesk.cli.app import app
from lychee_alphadesk.core.action_queue import ActionQueueItem
from lychee_alphadesk.core.cache_freshness import record_cache_entry
from lychee_alphadesk.core.config import set_openai_compatible_llm
from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.live_data import PullResult
from lychee_alphadesk.core.research_db import (
    write_discovery_research_run,
    write_research_evidence_review_record,
    write_research_memo_record,
)
from lychee_alphadesk.core.research_memo import generate_research_memo
from lychee_alphadesk.core.research_requests import (
    ResearchDataRequest,
    ResearchDataRequestExecution,
    ResearchDataRequestFulfillment,
)

runner = CliRunner()


def test_demo_command_reports_available_demo_files() -> None:
    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 0
    assert "演示工作区已就绪" in result.stdout
    assert "examples/demo/policy.yaml" in result.stdout


def test_policy_check_command_prints_passes() -> None:
    result = runner.invoke(app, ["policy", "check", "examples/demo/policy.yaml"])

    assert result.exit_code == 0
    assert "投资政策检查通过" in result.stdout
    assert "实盘交易已关闭" in result.stdout


def test_report_demo_generates_markdown_report(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report", "--demo", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "报告已写入:" in result.stdout

    report_path = tmp_path / "daily-report-demo.md"
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "# Lychee AlphaDesk 演示日报" in report
    assert "本报告使用演示数据" in report
    assert "## 数据质量状态" in report
    assert "market-data-present" in report
    assert "非投资建议" in report


def test_audit_list_shows_generated_report(tmp_path: Path) -> None:
    report_result = runner.invoke(app, ["report", "--demo", "--output-dir", str(tmp_path)])
    assert report_result.exit_code == 0

    audit_result = runner.invoke(app, ["audit", "list", "--output-dir", str(tmp_path)])

    assert audit_result.exit_code == 0
    assert "daily-report-demo.md" in audit_result.stdout
    assert "demo" in audit_result.stdout


def test_data_snapshot_command_writes_unified_demo_snapshot(tmp_path: Path) -> None:
    result = runner.invoke(app, ["data", "snapshot", "--demo", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "数据快照已写入:" in result.stdout
    assert "行情: 3" in result.stdout
    assert (tmp_path / "data-snapshot-demo.json").exists()


def test_data_health_command_shows_provider_quality() -> None:
    result = runner.invoke(app, ["data", "health", "--demo"])

    assert result.exit_code == 0
    assert "demo-market-data" in result.stdout
    assert "market-data-present" in result.stdout
    assert "通过" in result.stdout


def test_data_set_fund_command_writes_proxy_metadata_cache(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "data",
            "set",
            "fund",
            "--symbol",
            "2800.HK",
            "--name",
            "盈富基金",
            "--market",
            "HK",
            "--tracking-index",
            "Hang Seng Index",
            "--expense-ratio",
            "0.10%",
            "--holdings-summary",
            "跟踪恒生指数成分股",
            "--source-url",
            "https://example.com/2800",
            "--as-of",
            "2026-07-05",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "基金资料已写入" in result.stdout
    assert "2800.HK" in result.stdout
    cache = json.loads((tmp_path / "data" / "fund-metadata.json").read_text("utf-8"))
    assert cache["rows"][0]["symbol"] == "2800.HK"
    assert cache["rows"][0]["tracking_index"] == "Hang Seng Index"
    assert cache["rows"][0]["expense_ratio"] == "0.10%"


def test_data_set_metric_command_writes_source_backed_research_metric(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "data",
            "set",
            "metric",
            "--symbol",
            "QQQ",
            "--domain",
            "market_breadth",
            "--name",
            "纳斯达克100上涨家数",
            "--value",
            "63/100",
            "--as-of",
            "2026-07-07",
            "--source-url",
            "https://example.com/nasdaq100-breadth",
            "--note",
            "用于观察科技反弹扩散度。",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "研究指标已写入" in result.stdout
    assert "QQQ" in result.stdout
    cache = json.loads((tmp_path / "data" / "research-metrics.json").read_text("utf-8"))
    assert cache["rows"][0]["symbol"] == "QQQ"
    assert cache["rows"][0]["domain"] == "market_breadth"
    assert cache["rows"][0]["name"] == "纳斯达克100上涨家数"
    assert cache["rows"][0]["value"] == "63/100"
    assert cache["rows"][0]["source_url"] == "https://example.com/nasdaq100-breadth"


def test_data_guide_fund_command_writes_beginner_metadata_template(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "data",
            "guide",
            "fund",
            "--symbol",
            "3456.HK",
            "--name",
            "E Fund HKEX Tech 100 ETF",
            "--market",
            "HK",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "基金资料补齐向导" in result.stdout
    assert "3456.HK" in result.stdout
    assert "先查这些资料" in result.stdout
    assert "基金公司产品页" in result.stdout
    assert "香港交易所 ETF 页面" in result.stdout
    assert "lychee data set fund --symbol 3456.HK" in result.stdout
    guide_path = tmp_path / "data" / "fund-metadata-guide-3456.HK.json"
    assert guide_path.exists()
    guide = json.loads(guide_path.read_text("utf-8"))
    assert guide["symbol"] == "3456.HK"
    assert guide["display_name"] == "E Fund HKEX Tech 100 ETF"
    assert guide["market"] == "HK"
    assert "tracking_index" in guide["required_fields"]
    assert "香港交易所 ETF 页面" in guide["suggested_sources"]
    assert guide["write_command"].startswith("lychee data set fund --symbol 3456.HK")
    assert guide["apply_command"].startswith("lychee data set fund --from-file")


def test_data_set_fund_command_imports_completed_guide_template(
    tmp_path: Path,
) -> None:
    guide_result = runner.invoke(
        app,
        [
            "data",
            "guide",
            "fund",
            "--symbol",
            "3456.HK",
            "--name",
            "E Fund HKEX Tech 100 ETF",
            "--market",
            "HK",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert guide_result.exit_code == 0
    guide_path = tmp_path / "data" / "fund-metadata-guide-3456.HK.json"
    guide = json.loads(guide_path.read_text("utf-8"))
    guide["template"].update(
        {
            "tracking_index": "HKEX Tech 100 Index",
            "expense_ratio": "0.99%",
            "holdings_summary": "前十大成分覆盖港股科技龙头",
            "source_url": "https://example.com/3456",
            "as_of": "2026-07-07",
        }
    )
    guide_path.write_text(json.dumps(guide, ensure_ascii=False), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "data",
            "set",
            "fund",
            "--from-file",
            str(guide_path),
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "基金资料已写入" in result.stdout
    assert "3456.HK" in result.stdout
    cache = json.loads((tmp_path / "data" / "fund-metadata.json").read_text("utf-8"))
    row = cache["rows"][0]
    assert row["symbol"] == "3456.HK"
    assert row["tracking_index"] == "HKEX Tech 100 Index"
    assert row["expense_ratio"] == "0.99%"
    assert row["holdings_summary"] == "前十大成分覆盖港股科技龙头"
    assert row["source_url"] == "https://example.com/3456"


def test_discover_today_requires_llm_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))

    result = runner.invoke(app, ["discover", "today", "--output-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "LLM 尚未配置" in result.stdout
    assert "lychee setup llm set" in result.stdout
    assert not (tmp_path / "data" / "discovery-today.json").exists()


def test_discover_today_command_writes_report_when_llm_configured(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    set_openai_compatible_llm(
        "https://llm.example.com/v1",
        "sk-demo-secret",
        "demo-model",
    )

    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == []
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        news_path = data_dir / "news-events.json"
        news_path.write_text(
            json.dumps(
                {
                    "provider": "newsapi",
                    "created_at": "2026-07-06T10:00:00+00:00",
                    "warnings": [],
                    "rows": [
                        {
                            "timestamp": "2026-07-06T10:00:00+00:00",
                            "headline": "CLI market news",
                            "summary": "Prepared before LLM.",
                            "symbols": ["MARKET"],
                            "source_url": "https://example.com/cli-market-news",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", 1, news_path, [])

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        return {
            "choices": [
                {
                    "message": {
                        "content": """
                        {
                          "themes": [
                            {
                              "name": "CLI model theme",
                              "markets": ["US", "HK", "CN"],
                              "summary": "Model generated this CLI theme.",
                              "evidence": ["news_001"],
                              "sectors": ["Technology"],
                              "risk_flags": ["Model uncertainty"],
                              "confidence": "medium"
                            }
                          ],
                          "candidates": [
                            {
                              "display_name": "CLI model candidate",
                              "symbol": "NVDA",
                              "market": "US",
                              "asset_type": "stock",
                              "related_theme": "CLI model theme",
                              "why_watch": "Generated by the model.",
                              "evidence": ["news_001"],
                              "risk_flags": ["Model uncertainty"],
                              "next_actions": ["Pull filings"],
                              "confidence": "medium",
                              "recommendation": "research"
                            }
                          ],
                          "warnings": [],
                          "next_actions": ["Review model evidence"]
                        }
                        """
                    }
                }
            ]
            }

    monkeypatch.setattr(
        "lychee_alphadesk.core.discovery.pull_news_events",
        fake_pull_news_events,
    )
    monkeypatch.setattr("lychee_alphadesk.core.llm._post_json", fake_post)

    result = runner.invoke(app, ["discover", "today", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "正在准备市场级新闻" in result.stdout
    assert "今日市场发现已写入:" in result.stdout
    assert "研究库已更新:" in result.stdout
    assert "非投资建议" in result.stdout
    assert "US" in result.stdout
    assert "HK" in result.stdout
    assert "CN" in result.stdout

    report_path = tmp_path / "data" / "discovery-today.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "llm-synthesized"
    assert report["markets"] == ["US", "HK", "CN"]
    assert report["themes"][0]["name"] == "CLI model theme"
    assert report["candidates"][0]["recommendation"] == "research"

    queue_result = runner.invoke(app, ["research", "queue", "--output-dir", str(tmp_path)])

    assert queue_result.exit_code == 0
    assert "研究队列" in queue_result.stdout
    assert "CLI model candidate" in queue_result.stdout
    assert "NVDA" in queue_result.stdout


def test_research_queue_command_hides_duplicate_observation_history(
    tmp_path: Path,
) -> None:
    def report(created_at: str, display_name: str, action: str) -> DiscoveryReport:
        return DiscoveryReport(
            mode="llm-synthesized",
            created_at=created_at,
            markets=["US"],
            sources=[DiscoverySource("test-llm", "US", "测试来源")],
            themes=[
                DiscoveryTheme(
                    name="AI 存储需求",
                    markets=["US"],
                    summary="测试重复发现。",
                    evidence=["news_001"],
                    sectors=["Technology"],
                    risk_flags=[],
                    confidence="medium",
                )
            ],
            candidates=[
                DiscoveryCandidate(
                    display_name=display_name,
                    symbol="STX",
                    market="US",
                    asset_type="stock",
                    related_theme="AI 存储需求",
                    why_watch="测试重复发现。",
                    evidence=["news_001"],
                    risk_flags=[],
                    next_actions=[action],
                    confidence="medium",
                    recommendation="research",
                )
            ],
            warnings=[],
            next_actions=[],
            disclaimer="非投资建议。",
        )

    write_discovery_research_run(
        report("2026-07-05T10:00:00+00:00", "Seagate Legacy", "旧动作"),
        tmp_path,
        tmp_path / "data" / "discovery-old.json",
    )
    write_discovery_research_run(
        report("2026-07-05T11:00:00+00:00", "Seagate Active", "新动作"),
        tmp_path,
        tmp_path / "data" / "discovery-new.json",
    )

    result = runner.invoke(app, ["research", "queue", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Seagate Active" in result.stdout
    assert "Seagate Legacy" not in result.stdout
    assert "旧动作" not in result.stdout


def test_research_deepen_command_writes_research_packets(tmp_path: Path) -> None:
    _write_cli_research_seed(tmp_path)
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

    result = runner.invoke(app, ["research", "deepen", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "研究深挖包已写入" in result.stdout
    assert "Seagate" in result.stdout
    assert "news_001" in result.stdout
    assert (tmp_path / "research").exists()


def test_research_detail_command_prints_actionable_workbench_detail(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
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

    result = runner.invoke(
        app,
        ["research", "detail", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "研究任务面板" in result.stdout
    assert "任务: Seagate [US]" in result.stdout
    assert "本次研究要解决的问题" in result.stdout
    assert "研究启动" in result.stdout
    assert "第一步: lychee research verify --symbol STX" in result.stdout
    assert "看证据板: 支持证据 / 风险或反向待查 / 待补证据" in result.stdout
    assert (
        "记录判断: lychee research review --symbol STX --verdict needs_more_evidence"
        in result.stdout
    )
    assert "当前研究结论:" not in result.stdout
    assert "研究状态" in result.stdout
    assert "阶段: 可下钻研究" in result.stdout
    assert "一致性: 待核验" in result.stdout
    assert "排序理由:" in result.stdout
    assert "信号读数:" in result.stdout
    assert "证据矩阵" in result.stdout
    assert "行情: STX 110.50 USD" in result.stdout
    assert "AI storage demand rises" in result.stdout
    assert "10-K 2026-07-01" in result.stdout
    assert "可执行动作" in result.stdout
    assert "lychee data pull market --symbols STX --provider auto --force" in result.stdout
    assert "lychee data pull filings --symbols STX" in result.stdout
    assert "lychee research verify --symbol STX" in result.stdout


def test_research_run_command_executes_refresh_chain_and_writes_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "alpha_vantage",
                "rows": [
                    {
                        "symbol": "STX",
                        "date": "2026-07-01",
                        "close": 100.0,
                        "volume": 1000000,
                        "currency": "USD",
                    }
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
                        "timestamp": "2026-07-01T09:00:00+00:00",
                        "headline": "Old storage news",
                        "summary": "Old storage signal.",
                        "symbols": ["STX"],
                        "source_url": "https://example.com/old-storage",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
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
                        "source_url": "https://example.com/10k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, list[str], bool, str]] = []

    def fake_pull_market_prices(**kwargs: object) -> PullResult:
        symbols = list(kwargs["symbols"])
        force = bool(kwargs["force"])
        calls.append(("market", symbols, force, ""))
        output_path = tmp_path / "data" / "market-prices.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "auto",
                    "rows": [
                        {
                            "symbol": "STX",
                            "date": "2026-07-05",
                            "close": 120.0,
                            "volume": 4560000,
                            "currency": "USD",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("market", "auto", 1, output_path, [])

    def fake_pull_news_events(**kwargs: object) -> PullResult:
        symbols = list(kwargs["symbols"])
        force = bool(kwargs["force"])
        calls.append(("news", symbols, force, str(kwargs.get("query") or "")))
        output_path = tmp_path / "data" / "news-events.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "newsapi",
                    "rows": [
                        {
                            "timestamp": "2026-07-05T09:00:00+00:00",
                            "headline": "Updated AI storage growth news",
                            "summary": (
                                "Fresh storage demand improved with stronger "
                                "AI data-center orders."
                            ),
                            "symbols": ["STX"],
                            "source_url": "https://example.com/updated-storage",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", 1, output_path, [])

    def fake_pull_sec_filings(**kwargs: object) -> PullResult:
        symbols = list(kwargs["symbols"])
        calls.append(("filings", symbols, False, ""))
        output_path = tmp_path / "data" / "filings.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "sec_edgar",
                    "rows": [
                        {
                            "date": "2026-07-04",
                            "company": "Seagate",
                            "form": "8-K",
                            "summary": "STX 在 2026-07-04 提交了 8-K。",
                            "source_url": "https://example.com/8k",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("filings", "sec_edgar", 1, output_path, [])

    monkeypatch.setattr(
        "lychee_alphadesk.core.workbench.pull_market_prices",
        fake_pull_market_prices,
    )
    monkeypatch.setattr(
        "lychee_alphadesk.core.workbench.pull_news_events",
        fake_pull_news_events,
    )
    monkeypatch.setattr(
        "lychee_alphadesk.core.workbench.pull_sec_filings",
        fake_pull_sec_filings,
    )

    result = runner.invoke(
        app,
        [
            "research",
            "run",
            "--symbol",
            "STX",
            "--force",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert calls[0] == ("market", ["STX"], True, "")
    assert calls[1] == ("news", ["STX"], True, "")
    assert calls[2][0:3] == ("news", ["STX"], True)
    assert "AI 存储需求" in calls[2][3]
    assert calls[3] == ("filings", ["STX"], False, "")
    assert "研究执行记录已写入" in result.stdout
    assert "刷新行情" in result.stdout
    assert "刷新新闻" in result.stdout
    assert "刷新美股公告/财报" in result.stdout
    assert "STX 120.00 USD" in result.stdout
    assert "研究状态" in result.stdout
    assert "阶段: 可下钻研究" in result.stdout
    assert "Updated AI storage growth news" in result.stdout
    assert "8-K 2026-07-04" in result.stdout
    artifacts = list((tmp_path / "research").glob("research-run-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["candidate"]["symbol"] == "STX"
    assert payload["assessment"]["stage"] == "ready_for_drilldown"
    assert payload["assessment"]["consistency"] == "pending_review"
    assert payload["actions"][0]["action_type"] == "refresh_market"
    assert payload["detail"].startswith("研究任务面板")


def test_research_verify_command_writes_drilldown_checklist(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["research", "verify", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "下钻核验" in result.stdout
    assert "行情核验" in result.stdout
    assert "成交量核验" in result.stdout
    assert "新闻核验" in result.stdout
    assert "公告/财报核验" in result.stdout
    assert "一致性结论: 待人工核验" in result.stdout
    assert "证据板" in result.stdout
    assert "支持证据" in result.stdout
    assert "风险/反向待查" in result.stdout
    assert "待补证据" in result.stdout
    assert "研究决策板" in result.stdout
    assert "状态: 可进入人工一致性复核" in result.stdout
    assert "建议记录: continue_research" in result.stdout
    artifacts = list((tmp_path / "research").glob("research-verification-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["candidate"]["symbol"] == "STX"
    assert payload["status"] == "pending_review"
    assert payload["checks"][0]["name"] == "行情核验"
    assert payload["checks"][0]["status"] == "pass"
    assert payload["evidence_board"]["support"]
    assert payload["evidence_board"]["risk"]
    assert payload["evidence_board"]["missing"] == []
    assert payload["decision_board"]["workflow_state"] == "ready_for_review"
    assert payload["decision_board"]["suggested_verdict"] == "continue_research"
    assert "证据可以进入人工一致性复核" in payload["decision_board"]["decision_rule"]


def test_research_verify_reports_evidence_change_from_previous_snapshot(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    research_dir = tmp_path / "research"
    research_dir.mkdir(parents=True)
    (research_dir / "research-verification-20260704-010000Z.json").write_text(
        json.dumps(
            {
                "created_at": "2026-07-04T01:00:00+00:00",
                "candidate": {
                    "display_name": "Seagate",
                    "market": "US",
                    "symbol": "STX",
                    "proxy_symbols": [],
                },
                "evidence_board": {
                    "support": ["旧支持证据: 仅有市场传闻。"],
                    "risk": ["旧风险: 需要核验新闻来源。"],
                    "missing": ["旧核验缺少行情。"],
                },
                "decision_board": {
                    "suggested_verdict": "needs_more_evidence",
                    "suggested_verdict_label": "需要补证据",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["research", "verify", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "证据变化" in result.stdout
    assert "支持证据增加" in result.stdout
    assert "证据变化明细" in result.stdout
    assert "新增支持证据" in result.stdout
    assert "已补掉待补证据" in result.stdout
    artifacts = sorted((tmp_path / "research").glob("research-verification-*.json"))
    payload = json.loads(artifacts[-1].read_text(encoding="utf-8"))
    assert payload["evidence_change"]["status"] == "improved"
    assert payload["evidence_change"]["support_delta"] > 0
    assert payload["evidence_change"]["missing_delta"] == -1
    assert any(
        "STX 120.00 USD" in item
        for item in payload["evidence_change"]["added"]["support"]
    )
    assert "旧支持证据: 仅有市场传闻。" in payload["evidence_change"]["removed"][
        "support"
    ]
    assert "旧核验缺少行情。" in payload["evidence_change"]["removed"]["missing"]
    assert payload["evidence_change"]["previous_artifact_path"].endswith(
        "research-verification-20260704-010000Z.json"
    )


def test_research_verify_quarantines_off_topic_news(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Luxury handbags gain popularity in Europe",
                        "summary": "Consumer fashion spending improved this quarter.",
                        "symbols": ["STX"],
                        "source_url": "https://example.com/fashion",
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["research", "verify", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "主题相关性核验" in result.stdout
    assert "未命中研究主题关键词" in result.stdout
    assert "离题/已过滤" in result.stdout
    assert "执行命令: lychee research run --symbol STX --force" in result.stdout
    assert (
        "执行命令: lychee research review --symbol STX "
        "--verdict needs_more_evidence"
    ) in result.stdout
    artifacts = list((tmp_path / "research").glob("research-verification-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    topic_check = next(
        check for check in payload["checks"] if check["name"] == "主题相关性核验"
    )
    assert topic_check["status"] == "warn"
    assert payload["decision_board"]["workflow_state"] == "evidence_review"
    assert payload["decision_board"]["suggested_verdict"] == "needs_more_evidence"
    assert "主题关键词" in payload["decision_board"]["decision_rule"]
    assert payload["decision_board"]["next_commands"] == [
        "lychee research run --symbol STX --force",
        (
            "lychee research review --symbol STX "
            '--verdict needs_more_evidence --note "证据仍需补强，继续研究流程复核。"'
        ),
    ]
    support_text = "\n".join(payload["evidence_board"]["support"])
    risk_text = "\n".join(payload["evidence_board"]["risk"])
    off_topic_text = "\n".join(payload["evidence_board"]["off_topic"])
    assert "Luxury handbags gain popularity" not in support_text
    assert "Luxury handbags gain popularity" not in risk_text
    assert "Luxury handbags gain popularity" in off_topic_text


def test_research_verify_flags_reverse_news_as_risk(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "STX hard drive demand falls as cloud buyers cut orders",
                        "summary": (
                            "Weak AI infrastructure spending pressures "
                            "Seagate storage demand."
                        ),
                        "symbols": ["STX"],
                        "source_url": "https://example.com/stx-demand-falls",
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["research", "verify", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "证据方向核验" in result.stdout
    artifacts = list((tmp_path / "research").glob("research-verification-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    direction_check = next(
        check for check in payload["checks"] if check["name"] == "证据方向核验"
    )
    assert direction_check["status"] == "warn"
    assert payload["decision_board"]["workflow_state"] == "risk_review"
    assert payload["decision_board"]["suggested_verdict"] == "needs_more_evidence"
    assert "反向证据" in payload["decision_board"]["decision_rule"]
    support_text = "\n".join(payload["evidence_board"]["support"])
    risk_text = "\n".join(payload["evidence_board"]["risk"])
    assert "STX hard drive demand falls" not in support_text
    assert "反向证据: STX hard drive demand falls" in risk_text


def test_research_evidence_review_reclassifies_pending_news(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "STX hard drive demand update for AI storage",
                        "summary": "Cloud buyers discuss Seagate capacity plans.",
                        "symbols": ["STX"],
                        "source_url": "https://example.com/stx-demand-update",
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    before = runner.invoke(
        app,
        ["research", "verify", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )
    assert before.exit_code == 0
    assert "新闻待判定: STX hard drive demand update" in before.stdout

    review = runner.invoke(
        app,
        [
            "research",
            "evidence-review",
            "--symbol",
            "STX",
            "--text",
            "STX hard drive demand update",
            "--verdict",
            "support",
            "--note",
            "人工确认这条新闻与 AI 存储需求主题相关。",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert review.exit_code == 0
    assert "证据复核记录已写入" in review.stdout
    assert "复核方向: 支持证据" in review.stdout
    assert "工作台下一步" in review.stdout
    assert "重新下钻核验: lychee research verify --symbol STX" in review.stdout
    assert (
        "继续处理待判定证据: lychee research pending-evidence --symbol STX"
        in review.stdout
    )
    assert (
        "查看证据复核历史: lychee research evidence-reviews --symbol STX"
        in review.stdout
    )

    after = runner.invoke(
        app,
        ["research", "verify", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert after.exit_code == 0
    artifacts = sorted((tmp_path / "research").glob("research-verification-*.json"))
    payload = json.loads(artifacts[-1].read_text(encoding="utf-8"))
    support_text = "\n".join(payload["evidence_board"]["support"])
    risk_text = "\n".join(payload["evidence_board"]["risk"])
    direction_check = next(
        check for check in payload["checks"] if check["name"] == "证据方向核验"
    )
    assert "新闻: STX hard drive demand update for AI storage" in support_text
    assert "新闻待判定: STX hard drive demand update" not in risk_text
    assert direction_check["status"] == "pass"
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT symbol, evidence_text, verdict, note
            FROM research_evidence_reviews
            """
        ).fetchone()
    assert row == (
        "STX",
        "STX hard drive demand update",
        "support",
        "人工确认这条新闻与 AI 存储需求主题相关。",
    )


def test_research_verify_prints_pending_evidence_review_commands(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "STX hard drive demand update for AI storage",
                        "summary": "Cloud buyers discuss Seagate capacity plans.",
                        "symbols": ["STX"],
                        "source_url": "https://example.com/stx-demand-update",
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["research", "verify", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "待判定证据处理" in result.stdout
    assert "lychee research pending-evidence --symbol STX" in result.stdout
    assert "系统建议: 无关/排除" in result.stdout
    assert "lychee research evidence-review --symbol STX" in result.stdout
    assert '"STX hard drive demand update for AI storage"' in result.stdout
    assert "--verdict irrelevant" in result.stdout
    assert "<support|reverse|irrelevant>" not in result.stdout
    assert "分类后重新运行: lychee research verify --symbol STX" in result.stdout
    assert "待判定证据处理不是买卖建议" in result.stdout


def test_research_evidence_reviews_command_lists_audit_history(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
    review = runner.invoke(
        app,
        [
            "research",
            "evidence-review",
            "--symbol",
            "STX",
            "--text",
            "STX hard drive demand update",
            "--verdict",
            "reverse",
            "--note",
            "这条新闻更像需求放缓风险，需要排除乐观解释。",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert review.exit_code == 0

    history = runner.invoke(
        app,
        [
            "research",
            "evidence-reviews",
            "--symbol",
            "STX",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert history.exit_code == 0
    assert "Lychee AlphaDesk 证据复核历史" in history.stdout
    assert "Seagate" in history.stdout
    assert "STX" in history.stdout
    assert "STX hard drive demand update" in history.stdout
    assert "风险/反向待查" in history.stdout
    assert "这条新闻更像需求放缓风险" in history.stdout
    assert "research-evidence-review-" in history.stdout
    assert "单条证据复核历史不是买卖建议" in history.stdout


def test_research_pending_evidence_command_lists_unreviewed_queue(
    tmp_path: Path,
) -> None:
    _write_cli_verification_artifact(
        tmp_path,
        created_at="2026-07-05T10:00:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        risk_items=[
            "新闻待判定: stale QQQ headline 命中主题但方向未明。",
        ],
    )
    _write_cli_verification_artifact(
        tmp_path,
        created_at="2026-07-06T10:00:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        primary_question="美股科技股现在是独立主线，还是只是跟着大盘一起反弹？",
        risk_items=[
            "新闻待判定: QQQ tech rebound headline 命中主题但方向未明。",
            "新闻待判定: reviewed QQQ headline 命中主题但方向未明。",
        ],
    )
    write_research_evidence_review_record(
        output_dir=tmp_path,
        review_id="research-evidence-review:test",
        created_at="2026-07-06T10:05:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        evidence_text="reviewed QQQ headline",
        verdict="irrelevant",
        verdict_label="无关/排除",
        note="已确认和本次研究问题无关。",
        review_path=tmp_path / "research" / "research-evidence-review-test.json",
        payload={},
    )

    result = runner.invoke(
        app,
        ["research", "pending-evidence", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "Lychee AlphaDesk 待判定证据队列" in result.stdout
    assert "Invesco QQQ Trust" in result.stdout
    assert "QQQ" in result.stdout
    assert "美股科技股现在是独立主线" in result.stdout
    assert "QQQ tech rebound headline" in result.stdout
    assert "reviewed QQQ headline" not in result.stdout
    assert "stale QQQ headline" not in result.stdout
    assert "lychee research evidence-review --symbol QQQ" in result.stdout
    assert "系统建议" in result.stdout
    assert "--verdict support" in result.stdout
    assert "<support|reverse|irrelevant>" not in result.stdout
    assert "待判定证据队列不是买卖建议" in result.stdout


def test_research_pending_evidence_command_filters_by_symbol(
    tmp_path: Path,
) -> None:
    _write_cli_verification_artifact(
        tmp_path,
        created_at="2026-07-06T10:00:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        risk_items=[
            "新闻待判定: QQQ tech rebound headline 命中主题但方向未明。",
        ],
    )
    _write_cli_verification_artifact(
        tmp_path,
        created_at="2026-07-06T10:01:00+00:00",
        display_name="Seagate",
        symbol="STX",
        market="US",
        risk_items=[
            "新闻待判定: STX hard drive demand update 命中主题但方向未明。",
        ],
    )

    result = runner.invoke(
        app,
        ["research", "pending-evidence", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "STX hard drive demand update" in result.stdout
    assert "QQQ tech rebound headline" not in result.stdout


def test_research_pending_evidence_empty_message_mentions_filtered_noise(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "pending-evidence",
            "--symbol",
            "2800.HK",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "暂无待判定证据" in result.stdout
    assert "没有需要人工分类的新闻" in result.stdout
    assert "离题/已过滤" in result.stdout


def test_research_review_command_records_non_advisory_verdict(
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "research",
            "review",
            "--symbol",
            "STX",
            "--verdict",
            "continue_research",
            "--note",
            "证据完整，下一步做一致性人工复核。",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "研究复核记录已写入" in result.stdout
    assert "复核判断: 继续研究" in result.stdout
    assert "证据板" in result.stdout
    assert "工作台下一步" in result.stdout
    assert "生成研究备忘录: lychee research memo --symbol STX" in result.stdout
    assert "重新下钻核验: lychee research verify --symbol STX" in result.stdout
    assert "查看研究复核历史: lychee research reviews --symbol STX" in result.stdout
    assert "不是买卖建议" in result.stdout
    artifacts = list((tmp_path / "research").glob("research-review-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["verdict"] == "continue_research"
    assert payload["verdict_label"] == "继续研究"
    assert payload["note"] == "证据完整，下一步做一致性人工复核。"
    assert payload["verification"]["candidate"]["symbol"] == "STX"
    assert payload["evidence_counts"] == {
        "support": 4,
        "risk": 1,
        "off_topic": 0,
        "missing": 0,
    }
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT
                display_name,
                symbol,
                verdict,
                note,
                support_count,
                risk_count,
                missing_count,
                review_path,
                verification_path
            FROM research_reviews
            """
        ).fetchone()
    assert row == (
        "Seagate",
        "STX",
        "continue_research",
        "证据完整，下一步做一致性人工复核。",
        4,
        1,
        0,
        str(artifacts[0]),
        payload["verification_path"],
    )


def test_research_reviews_command_lists_review_history(tmp_path: Path) -> None:
    _write_cli_research_seed(tmp_path)
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    review_result = runner.invoke(
        app,
        [
            "research",
            "review",
            "--symbol",
            "STX",
            "--verdict",
            "pause_watch",
            "--note",
            "暂时观察，等待更多订单和财报证据。",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert review_result.exit_code == 0

    result = runner.invoke(
        app,
        ["research", "reviews", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "Lychee AlphaDesk 研究复核历史" in result.stdout
    assert "Seagate" in result.stdout
    assert "STX" in result.stdout
    assert "暂停观察" in result.stdout
    assert "暂时观察，等待更多订单和财报证据。" in result.stdout
    assert "支持 4 | 风险 1 | 待补 0" in result.stdout
    assert "research-review-" in result.stdout
    assert "不是买卖建议" in result.stdout


def test_research_memo_command_requires_llm_configuration(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))  # type: ignore[attr-defined]
    _write_cli_research_seed(tmp_path)

    result = runner.invoke(
        app,
        ["research", "memo", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "LLM 服务尚未配置" in result.stdout
    assert not list((tmp_path / "research").glob("research-memo-*.json"))


def test_research_memo_command_writes_llm_research_memo(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))  # type: ignore[attr-defined]
    set_openai_compatible_llm(
        "https://llm.example.com/v1",
        "sk-demo-secret",
        "demo-model",
    )
    _write_cli_research_seed(tmp_path)
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_request_chat_json(config: object, **kwargs: object) -> dict[str, object]:
        prompt = str(kwargs["messages"])
        assert "STX" in prompt
        assert "AI storage demand rises" in prompt
        assert "证据板" in prompt
        assert "working_hypothesis" in prompt
        assert "falsification_checks" in prompt
        assert "next_data_requests" in prompt
        return {
            "summary": "STX 的研究线索来自 AI 存储需求与本地行情证据的交叉。",
            "working_hypothesis": (
                "如果 AI 存储需求真实扩散，STX 的订单、毛利率和同行数据应同步改善。"
            ),
            "evidence_reading": "已有行情、新闻和公告材料，但仍需核对是否同向。",
            "support_points": ["新闻和行情都指向存储需求受到关注。"],
            "skeptic_review": ["硬盘行业仍可能受到周期和库存波动影响。"],
            "falsification_checks": ["若最新财报没有订单或毛利率改善，这条线索应降级。"],
            "missing_evidence": ["需要更多订单、毛利率和同业对比证据。"],
            "next_data_requests": ["拉取 STX 最新 10-Q 和同行 WDC 的财报摘要。"],
            "next_research_steps": ["核对最新财报中的收入和毛利率变化。"],
            "confidence": "medium",
        }

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "lychee_alphadesk.core.research_memo.request_chat_json",
        fake_request_chat_json,
    )

    result = runner.invoke(
        app,
        ["research", "memo", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "研究备忘录已写入" in result.stdout
    assert "STX 的研究线索" in result.stdout
    assert "工作假设" in result.stdout
    assert "反证检查" in result.stdout
    assert "下一批数据请求" in result.stdout
    assert "反方审查" in result.stdout
    assert "下一步研究动作" in result.stdout
    assert "工作台下一步" in result.stdout
    assert "查看数据请求队列: lychee research data-requests --symbol STX" in (
        result.stdout
    )
    assert (
        "记录研究复核: lychee research review --symbol STX "
        "--verdict continue_research"
    ) in result.stdout
    assert "重新下钻核验: lychee research verify --symbol STX" in result.stdout
    assert "查看研究备忘录历史: lychee research memos --symbol STX" in result.stdout
    assert "不是买卖建议" in result.stdout
    artifacts = list((tmp_path / "research").glob("research-memo-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["mode"] == "llm-research-memo"
    assert payload["candidate"]["symbol"] == "STX"
    assert payload["memo"]["confidence"] == "medium"
    assert payload["memo"]["working_hypothesis"].startswith("如果 AI 存储需求")
    assert payload["memo"]["falsification_checks"] == [
        "若最新财报没有订单或毛利率改善，这条线索应降级。"
    ]
    assert payload["memo"]["next_data_requests"] == [
        "拉取 STX 最新 10-Q 和同行 WDC 的财报摘要。"
    ]
    assert payload["memo"]["skeptic_review"] == [
        "硬盘行业仍可能受到周期和库存波动影响。"
    ]
    assert payload["verification_path"].endswith(".json")
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT
                display_name,
                symbol,
                confidence,
                summary,
                support_count,
                skeptic_count,
                missing_count,
                next_step_count,
                memo_path,
                verification_path
            FROM research_memos
            """
        ).fetchone()
    assert row == (
        "Seagate",
        "STX",
        "medium",
        "STX 的研究线索来自 AI 存储需求与本地行情证据的交叉。",
        1,
        1,
        1,
        1,
        str(artifacts[0]),
        payload["verification_path"],
    )


def test_research_memo_next_steps_follow_decision_board_for_weak_evidence(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))  # type: ignore[attr-defined]
    set_openai_compatible_llm(
        "https://llm.example.com/v1",
        "sk-demo-secret",
        "demo-model",
    )
    _write_cli_symbolless_mapping_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Generic global market article",
                        "summary": "Broad commentary without Hong Kong index evidence.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/generic",
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
                        "symbol": "2800.HK",
                        "date": "2026-07-05",
                        "close": 18.5,
                        "volume": 1000000,
                        "currency": "HKD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_request_chat_json(config: object, **kwargs: object) -> dict[str, object]:
        prompt = str(kwargs["messages"])
        assert "suggested_verdict" in prompt
        assert "needs_more_evidence" in prompt
        return {
            "summary": "港股压力观察已有代理行情，但主题新闻证据不足。",
            "working_hypothesis": "如果港股压力是市场级问题，宽基代理与主题新闻应同时显示压力。",
            "evidence_reading": "可交易性和成交量可观察，主题证据仍需补强。",
            "support_points": ["2800.HK 已有本地行情和成交量。"],
            "skeptic_review": ["现有新闻不能回答港股压力研究问题。"],
            "falsification_checks": ["若宽基代理没有走弱且主题新闻缺席，应降低这条线索优先级。"],
            "missing_evidence": ["缺少直接命中港股压力主题的新闻。"],
            "next_data_requests": ["刷新港股市场级新闻并拉取 2800.HK 与 3033.HK 行情。"],
            "next_research_steps": ["刷新主题新闻后重新下钻核验。"],
            "confidence": "low",
        }

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "lychee_alphadesk.core.research_memo.request_chat_json",
        fake_request_chat_json,
    )

    result = runner.invoke(
        app,
        [
            "research",
            "memo",
            "--name",
            "恒生指数压力观察",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "--verdict needs_more_evidence" in result.stdout
    assert "--verdict continue_research" not in result.stdout
    assert "刷新并补强证据: lychee research run --name \"恒生指数压力观察\" --force" in (
        result.stdout
    )
    artifact = next((tmp_path / "research").glob("research-memo-*.json"))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["verification"]["decision_board"]["suggested_verdict"] == (
        "needs_more_evidence"
    )


def test_research_memo_artifacts_do_not_overwrite_with_same_second(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))  # type: ignore[attr-defined]
    set_openai_compatible_llm(
        "https://llm.example.com/v1",
        "sk-demo-secret",
        "demo-model",
    )
    _write_cli_research_seed(tmp_path)
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
                        "date": "2026-07-05",
                        "close": 120.0,
                        "volume": 4560000,
                        "currency": "USD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "filings.json").write_text(
        json.dumps(
            {
                "provider": "sec_edgar",
                "rows": [
                    {
                        "date": "2026-07-04",
                        "company": "Seagate",
                        "form": "8-K",
                        "summary": "STX 在 2026-07-04 提交了 8-K。",
                        "source_url": "https://example.com/8k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_post_json(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "STX 研究备忘录。",
                                "working_hypothesis": (
                                    "如果 AI 存储需求扩散，STX 与同行数据应出现同向改善。"
                                ),
                                "evidence_reading": "已有行情、新闻和公告材料。",
                                "support_points": ["已有 STX 行情。"],
                                "skeptic_review": ["仍需核对周期风险。"],
                                "falsification_checks": [
                                    "若同行和财报没有同步改善，应降低线索置信度。"
                                ],
                                "missing_evidence": ["缺少同行对比。"],
                                "next_data_requests": ["拉取 WDC 行情与 STX 最新公告摘要。"],
                                "next_research_steps": ["继续补同行数据。"],
                                "confidence": "medium",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    now = datetime(2026, 7, 5, 11, 0, tzinfo=UTC)
    first = generate_research_memo(
        output_dir=tmp_path,
        symbol="STX",
        now=now,
        post_json=fake_post_json,
    )
    second = generate_research_memo(
        output_dir=tmp_path,
        symbol="STX",
        now=now,
        post_json=fake_post_json,
    )

    assert first.artifact_path != second.artifact_path
    assert first.artifact_path.exists()
    assert second.artifact_path.exists()
    assert len(list((tmp_path / "research").glob("research-memo-*.json"))) == 2
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:
        memo_rows = connection.execute(
            "SELECT memo_path FROM research_memos ORDER BY memo_path"
        ).fetchall()
    assert memo_rows == [(str(first.artifact_path),), (str(second.artifact_path),)]


def test_research_memos_command_lists_memo_history(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))  # type: ignore[attr-defined]
    set_openai_compatible_llm(
        "https://llm.example.com/v1",
        "sk-demo-secret",
        "demo-model",
    )
    _write_cli_research_seed(tmp_path)

    def fake_request_chat_json(config: object, **kwargs: object) -> dict[str, object]:
        return {
            "summary": "STX 需要继续核验 AI 存储需求是否反映到订单和利润。",
            "working_hypothesis": (
                "如果 AI 存储需求真实转化为基本面，订单、利润率和同行表现应互相印证。"
            ),
            "evidence_reading": "已有研究入口，但证据仍需继续补强。",
            "support_points": ["已有 STX 研究任务。"],
            "skeptic_review": ["单一新闻不能证明基本面变化。"],
            "falsification_checks": ["若最新财报和同行对比没有改善，应把线索降级为新闻噪声。"],
            "missing_evidence": ["缺少最新财报证据。"],
            "next_data_requests": ["补充 STX 财报摘要、WDC 对比和近 20 日成交量。"],
            "next_research_steps": ["补充财报和同行对比。"],
            "confidence": "low",
        }

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "lychee_alphadesk.core.research_memo.request_chat_json",
        fake_request_chat_json,
    )
    memo_result = runner.invoke(
        app,
        ["research", "memo", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )
    assert memo_result.exit_code == 0

    result = runner.invoke(
        app,
        ["research", "memos", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "Lychee AlphaDesk 研究备忘录历史" in result.stdout
    assert "Seagate" in result.stdout
    assert "STX" in result.stdout
    assert "low" in result.stdout
    assert "STX 需要继续核验" in result.stdout
    assert "支持 1 | 反方 1 | 待补 1 | 下一步 1" in result.stdout
    assert "research-memo-" in result.stdout
    assert "研究备忘录历史不是买卖建议" in result.stdout


def test_research_data_requests_command_lists_actionable_requests(tmp_path: Path) -> None:
    memo_path = tmp_path / "research" / "research-memo-test.json"
    verification_path = tmp_path / "research" / "research-verification-test.json"
    payload = {
        "memo": {
            "next_data_requests": [
                "请补齐 QQQ 的基金资料：跟踪指数、费用率、成分摘要和资料来源 URL。",
                "请提供 QQQ 与更宽市场基准的行情、成交量和相对强弱对比。",
            ]
        }
    }
    write_research_memo_record(
        output_dir=tmp_path,
        memo_id="research-memo:2026-07-05T10:02:00+00:00",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="low",
        summary="QQQ 仍需补齐 ETF 资料和行情对比。",
        support_count=1,
        skeptic_count=1,
        missing_count=2,
        next_step_count=2,
        memo_path=memo_path,
        verification_path=verification_path,
        payload=payload,
    )

    result = runner.invoke(
        app,
        [
            "research",
            "data-requests",
            "--symbol",
            "QQQ",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Lychee AlphaDesk 研究数据请求" in result.stdout
    assert "Invesco QQQ Trust" in result.stdout
    assert "请补齐 QQQ 的基金资料" in result.stdout
    assert "lychee data guide fund --symbol QQQ" in result.stdout
    assert "lychee data set fund --from-file .alphadesk/data/fund-metadata-guide-QQQ.json" in (
        result.stdout
    )
    assert "lychee data pull market --symbols QQQ --provider auto --force" in (
        result.stdout
    )
    assert "lychee research verify --symbol QQQ" in result.stdout
    assert "数据请求队列只用于补证据，不是买卖建议" in result.stdout


def test_research_next_command_lists_unified_action_queue(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    def fake_build_action_queue(output_dir: Path, **kwargs: object) -> list[ActionQueueItem]:
        assert output_dir == tmp_path
        assert kwargs["limit"] == 10
        return [
            ActionQueueItem(
                priority=1,
                area="待判定证据",
                title="复核 Seagate 的待判定新闻",
                detail="判断这条新闻是支持、反向还是无关。",
                command="lychee research evidence-review --symbol STX --verdict support",
                source="research-verification-test.json",
            ),
            ActionQueueItem(
                priority=2,
                area="数据源缺口",
                title="补充 Seagate 的市场广度指标",
                detail="当前缺少市场广度 provider。",
                command="lychee data set metric --symbol STX --domain market_breadth",
                source="research-memo-test.json",
            ),
        ]

    monkeypatch.setattr(cli_app, "build_action_queue", fake_build_action_queue)

    result = runner.invoke(
        app,
        ["research", "next", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "Lychee AlphaDesk 下一步行动队列" in result.stdout
    assert "待判定证据" in result.stdout
    assert "数据源缺口" in result.stdout
    assert "lychee research evidence-review --symbol STX" in result.stdout
    assert "lychee data set metric --symbol STX --domain market_breadth" in result.stdout
    assert "行动队列只推进研究流程，不是买卖建议" in result.stdout


def test_research_provider_backlog_command_lists_manual_provider_gaps(
    tmp_path: Path,
) -> None:
    memo_path = tmp_path / "research" / "research-memo-test.json"
    verification_path = tmp_path / "research" / "research-verification-test.json"
    write_research_memo_record(
        output_dir=tmp_path,
        memo_id="research-memo:2026-07-05T10:02:00+00:00",
        created_at="2026-07-05T10:02:00+00:00",
        display_name="Invesco QQQ Trust",
        symbol="QQQ",
        market="US",
        confidence="low",
        summary="QQQ 仍需补齐广度数据。",
        support_count=1,
        skeptic_count=1,
        missing_count=1,
        next_step_count=1,
        memo_path=memo_path,
        verification_path=verification_path,
        payload={
            "memo": {
                "next_data_requests": [
                    "请补充纳斯达克 100 成分股上涨家数和等权指数对比。"
                ]
            }
        },
    )

    result = runner.invoke(
        app,
        [
            "research",
            "provider-backlog",
            "--symbol",
            "QQQ",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Lychee AlphaDesk 数据源缺口队列" in result.stdout
    assert "Invesco QQQ Trust" in result.stdout
    assert "市场广度" in result.stdout
    assert "market_breadth" in result.stdout
    assert "指数成分数据源" in result.stdout
    assert "lychee data set metric --symbol QQQ --domain market_breadth" in result.stdout
    assert "接入可审计的市场广度 provider" in result.stdout
    assert "数据源缺口队列只用于规划补数据能力，不是买卖建议" in result.stdout


def test_research_run_data_request_command_executes_supported_actions(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_fulfill_research_data_request(
        output_dir: Path,
        **kwargs: object,
    ) -> ResearchDataRequestFulfillment:
        calls.append({"output_dir": output_dir, **kwargs})
        return ResearchDataRequestFulfillment(
            request=ResearchDataRequest(
                request_id="memo:test:data-request:1",
                created_at="2026-07-05T10:02:00+00:00",
                display_name="Invesco QQQ Trust",
                symbol="QQQ",
                market="US",
                confidence="low",
                request_text="请提供 QQQ 行情和成交量。",
                suggested_commands=["lychee data pull market --symbols QQQ"],
                memo_path=str(tmp_path / "research" / "memo.json"),
                verification_path=str(tmp_path / "research" / "verify.json"),
            ),
            executions=[
                ResearchDataRequestExecution(
                    action_type="market",
                    status="completed",
                    command="lychee data pull market --symbols QQQ",
                    count=1,
                    output_path=tmp_path / "data" / "market-prices.json",
                    message="行情已刷新。",
                ),
                ResearchDataRequestExecution(
                    action_type="verify",
                    status="completed",
                    command="lychee research verify --symbol QQQ",
                    count=1,
                    output_path=tmp_path / "research" / "verify-new.json",
                    message="已重新下钻核验。",
                ),
            ],
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli_app,
        "fulfill_research_data_request",
        fake_fulfill_research_data_request,
    )

    result = runner.invoke(
        app,
        [
            "research",
            "run-data-request",
            "--request",
            "2",
            "--symbol",
            "QQQ",
            "--output-dir",
            str(tmp_path),
            "--no-force",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "output_dir": tmp_path,
            "request_index": 2,
            "symbol": "QQQ",
            "name": None,
            "limit": 20,
            "force": False,
        }
    ]
    assert "研究数据请求执行结果" in result.stdout
    assert "行情已刷新" in result.stdout
    assert "已重新下钻核验" in result.stdout
    assert "数据请求执行只补证据，不是买卖建议" in result.stdout


def test_research_memo_command_rejects_investment_advice_language(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))  # type: ignore[attr-defined]
    set_openai_compatible_llm(
        "https://llm.example.com/v1",
        "sk-demo-secret",
        "demo-model",
    )
    _write_cli_research_seed(tmp_path)

    def fake_request_chat_json(config: object, **kwargs: object) -> dict[str, object]:
        return {
            "summary": "这份备忘录错误地给出了目标价。",
            "working_hypothesis": "如果 AI 存储线索成立，需要看到订单和毛利率改善。",
            "evidence_reading": "已有材料不足以支持交易判断。",
            "support_points": ["新闻提到了 AI 存储需求。"],
            "skeptic_review": ["周期行业波动仍需核验。"],
            "falsification_checks": ["若财报没有订单改善，线索应降级。"],
            "missing_evidence": ["需要订单和毛利率证据。"],
            "next_data_requests": ["补充最新财报和同行数据。"],
            "next_research_steps": ["目标价 150 美元，预期收益较高。"],
            "confidence": "medium",
        }

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "lychee_alphadesk.core.research_memo.request_chat_json",
        fake_request_chat_json,
    )

    result = runner.invoke(
        app,
        ["research", "memo", "--symbol", "STX", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "包含买卖或仓位建议语言" in result.stdout
    assert not list((tmp_path / "research").glob("research-memo-*.json"))


def test_research_deepen_command_shows_proxy_mapping_symbols(tmp_path: Path) -> None:
    _write_cli_symbolless_mapping_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Hong Kong stocks face index pressure",
                        "summary": "Hang Seng Index liquidity pressure should be mapped.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/hsi",
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
                        "symbol": "2800.HK",
                        "date": "2026-07-02",
                        "close": 18.5,
                        "volume": 1000000,
                        "currency": "HKD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["research", "deepen", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "代理" in result.stdout
    assert "2800.HK" in result.stdout
    assert "AlphaDesk 研究工作台" in result.stdout
    assert "今日研究任务" in result.stdout
    assert "研究问题:" in result.stdout
    assert "证据状态:" in result.stdout
    assert "关键核验:" in result.stdout
    assert "下一步队列" in result.stdout
    assert "给新手的读法" not in result.stdout
    assert "怎么理解代理" not in result.stdout


def test_research_deepen_command_handles_empty_queue(tmp_path: Path) -> None:
    result = runner.invoke(app, ["research", "deepen", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "研究队列为空" in result.stdout


def test_research_fill_gaps_command_runs_gap_fill_and_redeepens(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)

    def fake_pull_market_prices(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["STX"]
        output_path = tmp_path / "data" / "market-prices.json"
        output_path.parent.mkdir(exist_ok=True)
        output_path.write_text(
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
        return PullResult("market", "alpha_vantage", 1, output_path, [])

    def fake_pull_sec_filings(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["STX"]
        output_path = tmp_path / "data" / "filings.json"
        output_path.parent.mkdir(exist_ok=True)
        output_path.write_text(
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
        return PullResult("filings", "sec_edgar", 1, output_path, [])

    monkeypatch.setattr(
        "lychee_alphadesk.core.research.pull_market_prices",
        fake_pull_market_prices,
    )
    monkeypatch.setattr(
        "lychee_alphadesk.core.research.pull_sec_filings",
        fake_pull_sec_filings,
    )

    result = runner.invoke(app, ["research", "fill-gaps", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "自动补数据完成" in result.stdout
    assert "STX" in result.stdout
    assert "补齐后研究深挖包已写入" in result.stdout


def test_research_fill_gaps_command_reports_proxy_symbol_mappings(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write_cli_symbolless_mapping_seed(tmp_path)

    def fake_pull_market_prices(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["2800.HK"]
        output_path = tmp_path / "data" / "market-prices.json"
        output_path.parent.mkdir(exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "provider": "auto",
                    "rows": [
                        {
                            "symbol": "2800.HK",
                            "date": "2026-07-02",
                            "close": 18.5,
                            "volume": 1000000,
                            "currency": "HKD",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("market", "auto", 1, output_path, [])

    monkeypatch.setattr(
        "lychee_alphadesk.core.research.pull_market_prices",
        fake_pull_market_prices,
    )

    result = runner.invoke(app, ["research", "fill-gaps", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "代码映射" in result.stdout
    assert "已生成映射" in result.stdout
    assert "2800.HK" in result.stdout


def test_research_check_command_runs_closed_loop_and_prints_beginner_report(
    tmp_path: Path,
) -> None:
    _write_cli_symbolless_mapping_seed(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-05T09:00:00+00:00",
                        "headline": "Hong Kong stocks face index pressure",
                        "summary": "Hang Seng Index liquidity pressure should be mapped.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/hsi",
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
                        "symbol": "2800.HK",
                        "date": "2026-07-02",
                        "close": 18.5,
                        "volume": 1000000,
                        "currency": "HKD",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["research", "check", "--strict", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "工作台自检报告已写入" in result.stdout
    assert "状态: 可继续研究" in result.stdout
    assert "AlphaDesk 研究工作台" in result.stdout
    assert "2800.HK" in result.stdout
    assert "今日研究任务" in result.stdout
    assert "研究问题:" in result.stdout
    assert "证据状态:" in result.stdout
    assert "关键核验:" in result.stdout
    assert "下一步队列" in result.stdout
    assert "给新手的读法" not in result.stdout
    assert list((tmp_path / "research").glob("workbench-check-*.json"))


def test_research_check_command_strict_fails_when_blocked(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write_cli_research_seed(tmp_path)
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

    def failed_filings(**kwargs: object) -> PullResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        return PullResult(
            "filings",
            "sec_edgar",
            0,
            output_dir / "data" / "filings.json",
            ["SEC blocked"],
        )

    monkeypatch.setattr("lychee_alphadesk.core.workbench.pull_sec_filings", failed_filings)

    result = runner.invoke(
        app,
        ["research", "check", "--strict", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "状态: 未达标" in result.stdout
    assert "阻塞任务" in result.stdout
    assert "处理动作: 先补齐" in result.stdout
    assert "缺少 STX SEC 公告缓存" in result.stdout


def test_discover_today_reports_market_news_preparation_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    set_openai_compatible_llm(
        "https://llm.example.com/v1",
        "sk-demo-secret",
        "demo-model",
    )

    def fake_pull_news_events(**kwargs: object) -> PullResult:
        raise ValueError("尚未配置新闻数据源")

    monkeypatch.setattr(
        "lychee_alphadesk.core.discovery.pull_news_events",
        fake_pull_news_events,
    )

    result = runner.invoke(app, ["discover", "today", "--output-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "市场级新闻准备失败" in result.stdout
    assert "尚未配置新闻数据源" in result.stdout
    assert not (tmp_path / "data" / "discovery-today.json").exists()


def test_data_pull_market_command_writes_live_cache(monkeypatch, tmp_path: Path) -> None:
    def fake_pull_market_prices(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["AAPL", "TSLA"]
        assert kwargs["output_dir"] == tmp_path
        assert kwargs["force"] is False
        return PullResult(
            domain="market",
            provider="alpha_vantage",
            count=2,
            output_path=tmp_path / "data" / "market-prices.json",
            warnings=[],
        )

    monkeypatch.setattr(cli_app, "pull_market_prices", fake_pull_market_prices)

    result = runner.invoke(
        app,
        ["data", "pull", "market", "--symbols", "AAPL,TSLA", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "已拉取行情: 2" in result.stdout
    assert "alpha_vantage" in result.stdout


def test_data_pull_market_command_passes_force(monkeypatch, tmp_path: Path) -> None:
    def fake_pull_market_prices(**kwargs: object) -> PullResult:
        assert kwargs["force"] is True
        return PullResult(
            domain="market",
            provider="alpha_vantage",
            count=1,
            output_path=tmp_path / "data" / "market-prices.json",
            warnings=[],
        )

    monkeypatch.setattr(cli_app, "pull_market_prices", fake_pull_market_prices)

    result = runner.invoke(
        app,
        [
            "data",
            "pull",
            "market",
            "--symbols",
            "AAPL",
            "--force",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0


def test_data_freshness_command_lists_cache_entries(tmp_path: Path) -> None:
    artifact_path = tmp_path / "data" / "market-prices.json"
    artifact_path.parent.mkdir()
    artifact_path.write_text('{"provider": "alpha_vantage", "rows": []}', encoding="utf-8")
    now = datetime.now(UTC)
    record_cache_entry(
        output_dir=tmp_path,
        layer="market",
        cache_key="market:alpha_vantage:AAPL,TSLA",
        provider="alpha_vantage",
        artifact_path=artifact_path,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=1),
        ttl_seconds=900,
        row_count=2,
        market="US",
        session_state="closed",
        is_final_for_session=True,
    )

    result = runner.invoke(app, ["data", "freshness", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "数据新鲜度" in result.stdout
    assert "market" in result.stdout
    assert "alpha_vantage" in result.stdout
    assert "收盘确认" in result.stdout
    assert "AAPL,TSLA" in result.stdout


def test_data_freshness_command_translates_ttl_state(tmp_path: Path) -> None:
    artifact_path = tmp_path / "data" / "news-events.json"
    artifact_path.parent.mkdir()
    artifact_path.write_text('{"provider": "finnhub", "rows": []}', encoding="utf-8")
    record_cache_entry(
        output_dir=tmp_path,
        layer="news",
        cache_key="news:finnhub:AAPL:2026-07-01:2026-07-03",
        provider="finnhub",
        artifact_path=artifact_path,
        created_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        expires_at=datetime(2026, 7, 6, 11, 0, tzinfo=UTC),
        ttl_seconds=3600,
        row_count=1,
        market="US",
        session_state="ttl",
        is_final_for_session=False,
    )

    result = runner.invoke(app, ["data", "freshness", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "保质期" in result.stdout


def test_data_freshness_command_handles_empty_cache(tmp_path: Path) -> None:
    result = runner.invoke(app, ["data", "freshness", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "暂无缓存新鲜度记录" in result.stdout


def test_data_pull_news_command_writes_live_cache(monkeypatch, tmp_path: Path) -> None:
    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["AAPL"]
        assert kwargs["query"] is None
        assert kwargs["provider_id"] == "finnhub"
        assert kwargs["start_date"] == "2026-07-01"
        assert kwargs["end_date"] == "2026-07-03"
        assert kwargs["force"] is False
        return PullResult(
            domain="news",
            provider="finnhub",
            count=1,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    monkeypatch.setattr(cli_app, "pull_news_events", fake_pull_news_events)

    result = runner.invoke(
        app,
        [
            "data",
            "pull",
            "news",
            "--symbols",
            "AAPL",
            "--provider",
            "finnhub",
            "--from",
            "2026-07-01",
            "--to",
            "2026-07-03",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "已拉取新闻事件: 1" in result.stdout


def test_data_pull_news_command_passes_topic_query(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["STX"]
        assert kwargs["query"] == "AI storage demand"
        assert kwargs["provider_id"] == "newsapi"
        return PullResult(
            domain="news",
            provider="newsapi",
            count=1,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    monkeypatch.setattr(cli_app, "pull_news_events", fake_pull_news_events)

    result = runner.invoke(
        app,
        [
            "data",
            "pull",
            "news",
            "--symbols",
            "STX",
            "--query",
            "AI storage demand",
            "--provider",
            "newsapi",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "已拉取新闻事件: 1" in result.stdout


def test_data_pull_news_command_allows_market_news_without_symbols(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == []
        assert kwargs["provider_id"] == "auto"
        return PullResult(
            domain="news",
            provider="newsapi",
            count=1,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    monkeypatch.setattr(cli_app, "pull_news_events", fake_pull_news_events)

    result = runner.invoke(
        app,
        [
            "data",
            "pull",
            "news",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "已拉取新闻事件: 1" in result.stdout


def test_data_pull_news_command_passes_force(monkeypatch, tmp_path: Path) -> None:
    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["force"] is True
        return PullResult(
            domain="news",
            provider="finnhub",
            count=1,
            output_path=tmp_path / "data" / "news-events.json",
            warnings=[],
        )

    monkeypatch.setattr(cli_app, "pull_news_events", fake_pull_news_events)

    result = runner.invoke(
        app,
        [
            "data",
            "pull",
            "news",
            "--symbols",
            "AAPL",
            "--force",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0


def test_data_snapshot_command_reads_live_cache_by_default(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        '{"provider":"alpha_vantage","rows":[{"symbol":"AAPL","date":"2026-07-02",'
        '"close":214.33,"volume":51230000,"currency":"USD"}]}',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["data", "snapshot", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "数据快照已写入:" in result.stdout
    assert "模式: 实时" in result.stdout
    assert (tmp_path / "data-snapshot-live.json").exists()


def _write_cli_research_seed(output_dir: Path) -> None:
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
    write_discovery_research_run(
        report,
        output_dir,
        output_dir / "data" / "discovery-today.json",
    )


def _write_cli_verification_artifact(
    output_dir: Path,
    *,
    created_at: str,
    display_name: str,
    symbol: str | None,
    market: str,
    risk_items: list[str],
    primary_question: str = "这个研究任务要先回答什么问题？",
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (
        created_at.replace("-", "")
        .replace(":", "")
        .replace("+00:00", "Z")
        .replace("T", "-")
    )
    artifact_path = research_dir / f"research-verification-{timestamp}.json"
    artifact_path.write_text(
        json.dumps(
            {
                "created_at": created_at,
                "candidate": {
                    "display_name": display_name,
                    "symbol": symbol,
                    "market": market,
                    "proxy_symbols": [],
                },
                "decision_board": {
                    "primary_question": primary_question,
                },
                "evidence_board": {
                    "support": [],
                    "risk": risk_items,
                    "missing": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return artifact_path


def _write_cli_symbolless_mapping_seed(output_dir: Path) -> None:
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
        output_dir,
        output_dir / "data" / "discovery-today.json",
    )
