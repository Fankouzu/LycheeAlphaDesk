from pathlib import Path

import pytest
from pydantic import ValidationError

from lychee_alphadesk.core.policy import load_policy, validate_policy


def test_demo_policy_passes_required_safety_rules() -> None:
    policy = load_policy(Path("examples/demo/policy.yaml"))

    result = validate_policy(policy)

    assert result.errors == []
    assert "Live trading is disabled" in result.passes
    assert "Human approval is required" in result.passes
    assert "Minimum cash weight is 30%" in result.passes


def test_policy_rejects_live_trading_without_human_approval(tmp_path: Path) -> None:
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        """
base_currency: USD
live_trading: true
risk_limits:
  min_cash_weight: 0.1
  max_single_asset_weight: 0.5
  max_experimental_weight: 0.1
blocked_products:
  - margin
decision_requires:
  - data_quality_check
""".strip(),
        encoding="utf-8",
    )

    policy = load_policy(policy_file)
    result = validate_policy(policy)

    assert "Live trading is not allowed in v0.1" in result.errors
    assert "human_approval is required" in result.errors


def test_policy_requires_supported_base_currency(tmp_path: Path) -> None:
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        """
base_currency: DOGE
live_trading: false
risk_limits:
  min_cash_weight: 0.3
  max_single_asset_weight: 0.25
  max_experimental_weight: 0.0
blocked_products:
  - margin
decision_requires:
  - data_quality_check
  - source_links
  - counterargument
  - human_approval
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_policy(policy_file)
