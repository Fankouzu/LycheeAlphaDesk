import json
from pathlib import Path

from typer.testing import CliRunner

import lychee_alphadesk.cli.app as cli_app
from lychee_alphadesk.cli.app import app
from lychee_alphadesk.core.live_data import PullResult

runner = CliRunner()


def test_demo_command_reports_available_demo_files() -> None:
    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 0
    assert "Demo workspace ready" in result.stdout
    assert "examples/demo/policy.yaml" in result.stdout


def test_policy_check_command_prints_passes() -> None:
    result = runner.invoke(app, ["policy", "check", "examples/demo/policy.yaml"])

    assert result.exit_code == 0
    assert "Policy check passed" in result.stdout
    assert "Live trading is disabled" in result.stdout


def test_report_demo_generates_markdown_report(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report", "--demo", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Report written:" in result.stdout

    report_path = tmp_path / "daily-report-demo.md"
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "# Lychee AlphaDesk Demo Daily Report" in report
    assert "This report uses demo data" in report
    assert "## Data Quality Status" in report
    assert "market-data-present" in report
    assert "Not investment advice" in report


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
    assert "Data snapshot written:" in result.stdout
    assert "Prices: 3" in result.stdout
    assert (tmp_path / "data-snapshot-demo.json").exists()


def test_data_health_command_shows_provider_quality() -> None:
    result = runner.invoke(app, ["data", "health", "--demo"])

    assert result.exit_code == 0
    assert "demo-market-data" in result.stdout
    assert "market-data-present" in result.stdout
    assert "pass" in result.stdout


def test_discover_today_command_writes_fallback_report(tmp_path: Path) -> None:
    result = runner.invoke(app, ["discover", "today", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Today Discovery written:" in result.stdout
    assert "Not investment advice" in result.stdout
    assert "US" in result.stdout
    assert "HK" in result.stdout
    assert "CN" in result.stdout

    report_path = tmp_path / "data" / "discovery-today.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "fallback"
    assert report["markets"] == ["US", "HK", "CN"]
    assert report["themes"][0]["name"] == "AI infrastructure watch"
    assert report["candidates"][0]["recommendation"] == "research"


def test_data_pull_market_command_writes_live_cache(monkeypatch, tmp_path: Path) -> None:
    def fake_pull_market_prices(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["AAPL", "TSLA"]
        assert kwargs["output_dir"] == tmp_path
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
    assert "Pulled market rows: 2" in result.stdout
    assert "alpha_vantage" in result.stdout


def test_data_pull_news_command_writes_live_cache(monkeypatch, tmp_path: Path) -> None:
    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == ["AAPL"]
        assert kwargs["provider_id"] == "finnhub"
        assert kwargs["start_date"] == "2026-07-01"
        assert kwargs["end_date"] == "2026-07-03"
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
    assert "Pulled news events: 1" in result.stdout


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
    assert "Data snapshot written:" in result.stdout
    assert "Mode: live" in result.stdout
    assert (tmp_path / "data-snapshot-live.json").exists()
