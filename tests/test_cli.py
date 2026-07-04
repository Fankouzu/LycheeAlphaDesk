from pathlib import Path

from typer.testing import CliRunner

from lychee_alphadesk.cli.app import app

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
