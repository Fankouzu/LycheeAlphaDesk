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
        errors.append("Live trading is not allowed in v0.1")
    else:
        passes.append("Live trading is disabled")

    if "human_approval" not in policy.decision_requires:
        errors.append("human_approval is required")
    else:
        passes.append("Human approval is required")

    if policy.risk_limits.min_cash_weight >= 0.30:
        passes.append("Minimum cash weight is 30%")
    else:
        warnings.append("Minimum cash weight is below the conservative 30% demo default")

    if policy.risk_limits.max_single_asset_weight <= 0.25:
        passes.append("Single asset weight is capped at 25%")
    else:
        warnings.append("Single asset weight exceeds the conservative 25% demo default")

    missing_blocks = sorted(REQUIRED_BLOCKED_PRODUCTS.difference(policy.blocked_products))
    if missing_blocks:
        warnings.append("Some risky products are not blocked: " + ", ".join(missing_blocks))
    else:
        passes.append("Risky products are blocked")

    missing_gates = sorted(REQUIRED_DECISION_GATES.difference(policy.decision_requires))
    if missing_gates:
        errors.append("Missing decision gates: " + ", ".join(missing_gates))
    else:
        passes.append("Required decision gates are enabled")

    return PolicyValidationResult(errors=errors, warnings=warnings, passes=passes)
