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
    """# Lychee AlphaDesk 演示日报

> 本报告使用演示数据。非投资建议。

## 今日结论

不采取操作。演示组合通过 v0.1 保守策略门槛，任何真实世界决策前都需要人工确认。

## 组合快照

| 代码 | 名称 | 数量 | 目标权重 | 资产类型 |
| --- | --- | ---: | ---: | --- |
{% for position in positions -%}
| {{ position.symbol }} | {{ position.name }} | {{ "%.2f"|format(position.quantity) }} | {{ "%.0f%%"|format(position.target_weight * 100) }} | {{ position.asset_type }} |
{% endfor %}

## 投资政策检查

{% for item in policy_result.passes -%}
- 通过: {{ item }}
{% endfor -%}
{% for item in policy_result.warnings -%}
- 警告: {{ item }}
{% endfor -%}
{% for item in policy_result.errors -%}
- 错误: {{ item }}
{% endfor %}

## 行情数据

| 代码 | 日期 | 收盘价 | 成交量 | 货币 |
| --- | --- | ---: | ---: | --- |
{% for row in prices -%}
| {{ row.symbol }} | {{ row.date }} | {{ "%.2f"|format(row.close) }} | {{ row.volume }} | {{ row.currency }} |
{% endfor %}

## 数据质量状态

| 检查项 | 状态 | 数据源 | 说明 |
| --- | --- | --- | --- |
{% for check in quality_checks -%}
| {{ check.name }} | {{ check.status }} | {{ check.provider }} | {{ check.message }} |
{% endfor %}

## 新闻与事件

{% for event in events -%}
- **{{ event.headline }}** ({{ event.timestamp }})
  {{ event.summary }}
  相关代码: {{ ", ".join(event.symbols) }}
  来源: {{ event.source_url }}
{% endfor %}

## 公告摘要

{% for filing in filings -%}
- **{{ filing.company }} {{ filing.form }}** ({{ filing.date }})
  {{ filing.summary }}
  来源: {{ filing.source_url }}
{% endfor %}

## 预测摘要

模拟预测区间只是基线占位，不是交易信号。

| 代码 | 周期 | 下界 | 中位值 | 上界 | 方法 |
| --- | ---: | ---: | ---: | ---: | --- |
{% for forecast in forecasts.values() -%}
| {{ forecast.symbol }} | {{ forecast.horizon_days }}d | {{ "%.2f"|format(forecast.lower) }} | {{ "%.2f"|format(forecast.midpoint) }} | {{ "%.2f"|format(forecast.upper) }} | {{ forecast.method }} |
{% endfor %}

## 反方审查

- 演示数据是合成且不完整的。
- 预测区间只是占位，后续必须与真实基线比较。
- 必须由人工确认数据来源、税务、费用、账户限制和适当性。

## 审计元数据

- 模式: demo
- 数据源: {{ ", ".join(provider_names) }}
- 报告 ID: daily-report-demo
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
