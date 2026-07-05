from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

BaseCurrency = Literal["USD", "HKD", "CNY"]


class RiskLimits(BaseModel):
    min_cash_weight: float = Field(ge=0, le=1)
    max_single_asset_weight: float = Field(gt=0, le=1)
    max_experimental_weight: float = Field(ge=0, le=1)


class InvestmentPolicy(BaseModel):
    base_currency: BaseCurrency
    live_trading: bool
    risk_limits: RiskLimits
    blocked_products: list[str]
    decision_requires: list[str]


class PolicyValidationResult(BaseModel):
    errors: list[str]
    warnings: list[str]
    passes: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


REQUIRED_BLOCKED_PRODUCTS = {
    "margin",
    "options",
    "futures",
    "leveraged_etf",
    "inverse_etf",
    "crypto",
}

REQUIRED_DECISION_GATES = {
    "data_quality_check",
    "source_links",
    "counterargument",
    "human_approval",
}


def load_policy(path: Path) -> InvestmentPolicy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return InvestmentPolicy.model_validate(raw)


def validate_policy(policy: InvestmentPolicy) -> PolicyValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    passes: list[str] = []

    if policy.live_trading:
        errors.append("v0.1 不允许开启实盘交易")
    else:
        passes.append("实盘交易已关闭")

    if "human_approval" not in policy.decision_requires:
        errors.append("必须启用人工确认")
    else:
        passes.append("已要求人工确认")

    if policy.risk_limits.min_cash_weight >= 0.30:
        passes.append("最低现金比例不低于 30%")
    else:
        warnings.append("最低现金比例低于演示策略的保守默认值 30%")

    if policy.risk_limits.max_single_asset_weight <= 0.25:
        passes.append("单一资产权重上限不高于 25%")
    else:
        warnings.append("单一资产权重超过演示策略的保守默认值 25%")

    missing_blocks = sorted(REQUIRED_BLOCKED_PRODUCTS.difference(policy.blocked_products))
    if missing_blocks:
        warnings.append("以下高风险产品尚未屏蔽: " + ", ".join(missing_blocks))
    else:
        passes.append("高风险产品已屏蔽")

    missing_gates = sorted(REQUIRED_DECISION_GATES.difference(policy.decision_requires))
    if missing_gates:
        errors.append("缺少决策门槛: " + ", ".join(missing_gates))
    else:
        passes.append("必要决策门槛已启用")

    return PolicyValidationResult(errors=errors, warnings=warnings, passes=passes)
