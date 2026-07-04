# ruff: noqa: E501
import csv
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Template

from lychee_alphadesk.core.audit import AuditRecord, write_audit_record
from lychee_alphadesk.core.data_engine import DataQualityCheck, build_demo_data_snapshot
from lychee_alphadesk.core.paths import DEMO_ROOT
from lychee_alphadesk.core.policy import PolicyValidationResult, load_policy, validate_policy
from lychee_alphadesk.providers.demo import FilingSummary, ForecastInterval, NewsEvent, PriceRow


@dataclass(frozen=True)
class PortfolioPosition:
    symbol: str
    name: str
    quantity: float
    target_weight: float
    asset_type: str


@dataclass(frozen=True)
class DemoReportResult:
    report_path: Path
    audit_record: AuditRecord
    policy_result: PolicyValidationResult


REPORT_TEMPLATE = Template(
    """# Lychee AlphaDesk Demo Daily Report

> This report uses demo data. Not investment advice.

## Daily Conclusion

No action. The demo portfolio passes the conservative v0.1 policy gates, and all outputs require human approval before any real-world decision.

## Portfolio Snapshot

| Symbol | Name | Quantity | Target Weight | Asset Type |
| --- | --- | ---: | ---: | --- |
{% for position in positions -%}
| {{ position.symbol }} | {{ position.name }} | {{ "%.2f"|format(position.quantity) }} | {{ "%.0f%%"|format(position.target_weight * 100) }} | {{ position.asset_type }} |
{% endfor %}

## Policy Check

{% for item in policy_result.passes -%}
- PASS: {{ item }}
{% endfor -%}
{% for item in policy_result.warnings -%}
- WARNING: {{ item }}
{% endfor -%}
{% for item in policy_result.errors -%}
- ERROR: {{ item }}
{% endfor %}

## Market Data

| Symbol | Date | Close | Volume | Currency |
| --- | --- | ---: | ---: | --- |
{% for row in prices -%}
| {{ row.symbol }} | {{ row.date }} | {{ "%.2f"|format(row.close) }} | {{ row.volume }} | {{ row.currency }} |
{% endfor %}

## Data Quality Status

| Check | Status | Provider | Message |
| --- | --- | --- | --- |
{% for check in quality_checks -%}
| {{ check.name }} | {{ check.status }} | {{ check.provider }} | {{ check.message }} |
{% endfor %}

## News And Events

{% for event in events -%}
- **{{ event.headline }}** ({{ event.timestamp }})
  {{ event.summary }}
  Symbols: {{ ", ".join(event.symbols) }}
  Source: {{ event.source_url }}
{% endfor %}

## Filing Notes

{% for filing in filings -%}
- **{{ filing.company }} {{ filing.form }}** ({{ filing.date }})
  {{ filing.summary }}
  Source: {{ filing.source_url }}
{% endfor %}

## Forecast Summary

Mock forecast intervals are baseline placeholders, not trade signals.

| Symbol | Horizon | Lower | Midpoint | Upper | Method |
| --- | ---: | ---: | ---: | ---: | --- |
{% for forecast in forecasts.values() -%}
| {{ forecast.symbol }} | {{ forecast.horizon_days }}d | {{ "%.2f"|format(forecast.lower) }} | {{ "%.2f"|format(forecast.midpoint) }} | {{ "%.2f"|format(forecast.upper) }} | {{ forecast.method }} |
{% endfor %}

## Skeptic Review

- Demo data is synthetic and incomplete.
- Forecast intervals are placeholders and should be compared with real baselines later.
- A human must verify sources, taxes, fees, account restrictions, and suitability.

## Audit Metadata

- Mode: demo
- Providers: {{ ", ".join(provider_names) }}
- Report ID: daily-report-demo
"""
)


def generate_demo_report(output_dir: Path, demo_root: Path = DEMO_ROOT) -> DemoReportResult:
    output_dir.mkdir(parents=True, exist_ok=True)

    policy = load_policy(demo_root / "policy.yaml")
    policy_result = validate_policy(policy)
    positions = load_demo_portfolio(demo_root / "portfolio.csv")
    snapshot = build_demo_data_snapshot(demo_root)

    report_path = output_dir / "daily-report-demo.md"
    report_path.write_text(
        render_demo_report(
            positions=positions,
            prices=snapshot.prices,
            events=snapshot.news_events,
            filings=snapshot.filings,
            forecasts=snapshot.forecasts,
            quality_checks=snapshot.quality_checks,
            policy_result=policy_result,
            provider_names=snapshot.provider_names,
        ),
        encoding="utf-8",
    )

    audit_record = write_audit_record(
        output_dir,
        report_id="daily-report-demo",
        mode="demo",
        report_path=report_path,
        providers=snapshot.provider_names,
        warnings=policy_result.warnings,
        errors=policy_result.errors,
    )
    return DemoReportResult(
        report_path=report_path,
        audit_record=audit_record,
        policy_result=policy_result,
    )


def render_demo_report(
    *,
    positions: list[PortfolioPosition],
    prices: list[PriceRow],
    events: list[NewsEvent],
    filings: list[FilingSummary],
    forecasts: dict[str, ForecastInterval],
    quality_checks: list[DataQualityCheck],
    policy_result: PolicyValidationResult,
    provider_names: list[str],
) -> str:
    return REPORT_TEMPLATE.render(
        positions=positions,
        prices=prices,
        events=events,
        filings=filings,
        forecasts=forecasts,
        quality_checks=quality_checks,
        policy_result=policy_result,
        provider_names=provider_names,
    )


def load_demo_portfolio(path: Path) -> list[PortfolioPosition]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return [
            PortfolioPosition(
                symbol=row["symbol"],
                name=row["name"],
                quantity=float(row["quantity"]),
                target_weight=float(row["target_weight"]),
                asset_type=row["asset_type"],
            )
            for row in reader
        ]
