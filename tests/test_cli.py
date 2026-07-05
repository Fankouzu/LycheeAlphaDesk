import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

import lychee_alphadesk.cli.app as cli_app
from lychee_alphadesk.cli.app import app
from lychee_alphadesk.core.cache_freshness import record_cache_entry
from lychee_alphadesk.core.config import set_openai_compatible_llm
from lychee_alphadesk.core.live_data import PullResult

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
                              "evidence": ["LLM response"],
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
                              "evidence": ["LLM response"],
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
