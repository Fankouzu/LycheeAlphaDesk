import csv
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.live_data import build_cached_data_snapshot
from lychee_alphadesk.core.policy import (
    PolicyValidationResult,
    load_policy,
    validate_policy,
)


@dataclass(frozen=True)
class PortfolioTarget:
    symbol: str
    name: str
    quantity: float
    target_weight: float
    asset_type: str
    currency: str = ""


@dataclass(frozen=True)
class PortfolioCheckResult:
    portfolio_path: Path
    policy_path: Path
    created_at: str
    targets: list[PortfolioTarget]
    policy_result: PolicyValidationResult
    base_currency: str
    currencies: list[str]
    foreign_currency_symbols: list[str]
    total_target_weight: float
    cash_target_weight: float
    experimental_target_weight: float
    missing_price_symbols: list[str]
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors and self.policy_result.ok

    @property
    def status_label(self) -> str:
        if not self.ok:
            return "需要修正"
        if self.missing_price_symbols:
            return "政策通过，等待行情"
        if self.foreign_currency_symbols:
            return "政策通过，等待 FX"
        return "可继续模拟练习"


def check_portfolio(
    *,
    portfolio_path: Path,
    policy_path: Path,
    output_dir: Path | None = None,
    now: datetime | None = None,
) -> PortfolioCheckResult:
    targets = load_portfolio_targets(portfolio_path)
    policy = load_policy(policy_path)
    policy_result = validate_policy(policy)
    errors: list[str] = []
    warnings: list[str] = []

    symbols = [target.symbol for target in targets]
    duplicates = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicates:
        errors.append("组合文件包含重复代码: " + ", ".join(duplicates))

    total_weight = sum(target.target_weight for target in targets)
    if abs(total_weight - 1.0) > 0.005:
        errors.append(f"目标权重合计为 {total_weight:.2%}，必须接近 100%。")

    cash_weight = sum(
        target.target_weight
        for target in targets
        if target.asset_type.casefold() == "cash" or target.symbol == "CASH"
    )
    if cash_weight < policy.risk_limits.min_cash_weight:
        errors.append(
            f"现金目标比例 {cash_weight:.2%} 低于政策最低值 "
            f"{policy.risk_limits.min_cash_weight:.2%}。"
        )

    experimental_weight = sum(
        target.target_weight
        for target in targets
        if target.asset_type.casefold() == "experimental"
    )
    if experimental_weight > policy.risk_limits.max_experimental_weight:
        errors.append(
            f"实验性资产目标比例 {experimental_weight:.2%} 超过政策上限 "
            f"{policy.risk_limits.max_experimental_weight:.2%}。"
        )

    for target in targets:
        normalized_type = target.asset_type.casefold()
        if normalized_type in policy.blocked_products:
            errors.append(
                f"{target.symbol} 使用了政策禁止的产品类型: {target.asset_type}。"
            )
        if (
            target.symbol != "CASH"
            and target.target_weight > policy.risk_limits.max_single_asset_weight
        ):
            errors.append(
                f"{target.symbol} 目标比例 {target.target_weight:.2%} 超过单项上限 "
                f"{policy.risk_limits.max_single_asset_weight:.2%}。"
            )

    missing_price_symbols: list[str] = []
    currencies: set[str] = {policy.base_currency}
    foreign_currency_symbols: list[str] = []
    if output_dir is not None:
        snapshot = build_cached_data_snapshot(output_dir)
        prices = {price.symbol.upper(): price for price in snapshot.prices}
        cached_symbols = set(prices)
        missing_price_symbols = [
            target.symbol
            for target in targets
            if target.symbol != "CASH" and target.symbol not in cached_symbols
        ]
        if missing_price_symbols:
            warnings.append(
                "本地行情缓存未覆盖: " + ", ".join(missing_price_symbols)
                + "；先补行情后再做组合估值。"
            )
        for target in targets:
            if target.symbol == "CASH":
                continue
            currency = target.currency or (
                prices[target.symbol].currency if target.symbol in prices else ""
            )
            if currency:
                normalized_currency = currency.upper()
                currencies.add(normalized_currency)
                if normalized_currency != policy.base_currency:
                    foreign_currency_symbols.append(target.symbol)
    else:
        for target in targets:
            if target.currency:
                normalized_currency = target.currency.upper()
                currencies.add(normalized_currency)
                if normalized_currency != policy.base_currency and target.symbol != "CASH":
                    foreign_currency_symbols.append(target.symbol)
    if foreign_currency_symbols:
        warnings.append(
            "识别到非基础货币代码: "
            + ", ".join(foreign_currency_symbols)
            + f"；基础货币为 {policy.base_currency}，缺少 FX provider，暂不计算跨币种总值。"
        )

    return PortfolioCheckResult(
        portfolio_path=portfolio_path,
        policy_path=policy_path,
        created_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        targets=targets,
        policy_result=policy_result,
        base_currency=policy.base_currency,
        currencies=sorted(currencies),
        foreign_currency_symbols=foreign_currency_symbols,
        total_target_weight=total_weight,
        cash_target_weight=cash_weight,
        experimental_target_weight=experimental_weight,
        missing_price_symbols=missing_price_symbols,
        errors=errors,
        warnings=warnings,
    )


def load_portfolio_targets(path: Path) -> list[PortfolioTarget]:
    if not path.exists():
        raise ValueError(f"组合文件不存在: {path}")
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        required = {"symbol", "name", "quantity", "target_weight", "asset_type"}
        missing = sorted(required.difference(reader.fieldnames or []))
        if missing:
            raise ValueError("组合文件缺少字段: " + ", ".join(missing))
        targets: list[PortfolioTarget] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                symbol = str(row["symbol"] or "").strip().upper()
                name = str(row["name"] or "").strip()
                quantity = float(row["quantity"] or "")
                target_weight = float(row["target_weight"] or "")
                asset_type = str(row["asset_type"] or "").strip().lower()
                currency = str(row.get("currency") or "").strip().upper()
            except (TypeError, ValueError) as error:
                raise ValueError(f"组合文件第 {line_number} 行包含无效数字。") from error
            if not symbol or not name or not asset_type:
                raise ValueError(f"组合文件第 {line_number} 行缺少必填字段。")
            if quantity < 0 or not 0 <= target_weight <= 1:
                raise ValueError(f"组合文件第 {line_number} 行数量或目标比例无效。")
            targets.append(
                PortfolioTarget(symbol, name, quantity, target_weight, asset_type, currency)
            )
    if not targets:
        raise ValueError("组合文件没有任何目标项。")
    return targets


def write_portfolio_check_artifact(result: PortfolioCheckResult, output_dir: Path) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    timestamp = result.created_at.replace("-", "").replace(":", "").replace("+00:00", "Z")
    path = research_dir / f"portfolio-check-{timestamp}.json"
    if path.exists():
        for index in range(1, 1000):
            candidate = research_dir / f"portfolio-check-{timestamp}~{index:02d}.json"
            if not candidate.exists():
                path = candidate
                break
    payload = asdict(result)
    payload["portfolio_path"] = str(result.portfolio_path)
    payload["policy_path"] = str(result.policy_path)
    payload["policy_result"] = result.policy_result.model_dump()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
