import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

import lychee_alphadesk.cli.app as cli_app
from lychee_alphadesk.cli.app import app
from lychee_alphadesk.core.cache_freshness import record_cache_entry
from lychee_alphadesk.core.config import set_openai_compatible_llm
from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.live_data import PullResult
from lychee_alphadesk.core.research_db import write_discovery_research_run

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
    assert "研究结果" in result.stdout
    assert "任务: Seagate [US]" in result.stdout
    assert "研究状态" in result.stdout
    assert "阶段: 可下钻研究" in result.stdout
    assert "一致性: 待核验" in result.stdout
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
    calls: list[tuple[str, list[str], bool]] = []

    def fake_pull_market_prices(**kwargs: object) -> PullResult:
        symbols = list(kwargs["symbols"])
        force = bool(kwargs["force"])
        calls.append(("market", symbols, force))
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
        calls.append(("news", symbols, force))
        output_path = tmp_path / "data" / "news-events.json"
        output_path.write_text(
            json.dumps(
                {
                    "provider": "newsapi",
                    "rows": [
                        {
                            "timestamp": "2026-07-05T09:00:00+00:00",
                            "headline": "Updated AI storage news",
                            "summary": "Fresh storage signal.",
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
        calls.append(("filings", symbols, False))
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
    assert calls == [
        ("market", ["STX"], True),
        ("news", ["STX"], True),
        ("filings", ["STX"], False),
    ]
    assert "研究执行记录已写入" in result.stdout
    assert "刷新行情" in result.stdout
    assert "刷新新闻" in result.stdout
    assert "刷新美股公告/财报" in result.stdout
    assert "STX 120.00 USD" in result.stdout
    assert "研究状态" in result.stdout
    assert "阶段: 可下钻研究" in result.stdout
    assert "Updated AI storage news" in result.stdout
    assert "8-K 2026-07-04" in result.stdout
    artifacts = list((tmp_path / "research").glob("research-run-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["candidate"]["symbol"] == "STX"
    assert payload["assessment"]["stage"] == "ready_for_drilldown"
    assert payload["assessment"]["consistency"] == "pending_review"
    assert payload["actions"][0]["action_type"] == "refresh_market"
    assert payload["detail"].startswith("研究结果")


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
    assert "不是买卖建议" in result.stdout
    artifacts = list((tmp_path / "research").glob("research-review-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["verdict"] == "continue_research"
    assert payload["verdict_label"] == "继续研究"
    assert payload["note"] == "证据完整，下一步做一致性人工复核。"
    assert payload["verification"]["candidate"]["symbol"] == "STX"
    assert payload["evidence_counts"] == {"support": 4, "risk": 1, "missing": 0}
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
        return {
            "summary": "STX 的研究线索来自 AI 存储需求与本地行情证据的交叉。",
            "evidence_reading": "已有行情、新闻和公告材料，但仍需核对是否同向。",
            "support_points": ["新闻和行情都指向存储需求受到关注。"],
            "skeptic_review": ["硬盘行业仍可能受到周期和库存波动影响。"],
            "missing_evidence": ["需要更多订单、毛利率和同业对比证据。"],
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
    assert "反方审查" in result.stdout
    assert "下一步研究动作" in result.stdout
    assert "不是买卖建议" in result.stdout
    artifacts = list((tmp_path / "research").glob("research-memo-*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["mode"] == "llm-research-memo"
    assert payload["candidate"]["symbol"] == "STX"
    assert payload["memo"]["confidence"] == "medium"
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
            "evidence_reading": "已有研究入口，但证据仍需继续补强。",
            "support_points": ["已有 STX 研究任务。"],
            "skeptic_review": ["单一新闻不能证明基本面变化。"],
            "missing_evidence": ["缺少最新财报证据。"],
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
            "evidence_reading": "已有材料不足以支持交易判断。",
            "support_points": ["新闻提到了 AI 存储需求。"],
            "skeptic_review": ["周期行业波动仍需核验。"],
            "missing_evidence": ["需要订单和毛利率证据。"],
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
    record_cache_entry(
        output_dir=tmp_path,
        layer="market",
        cache_key="market:alpha_vantage:AAPL,TSLA",
        provider="alpha_vantage",
        artifact_path=artifact_path,
        created_at=datetime(2026, 7, 6, 14, 0, tzinfo=UTC),
        expires_at=datetime(2026, 7, 7, 13, 30, tzinfo=UTC),
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
