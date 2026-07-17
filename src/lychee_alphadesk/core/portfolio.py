import csv
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.fx import read_cached_fx_rates
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
class PortfolioPosition:
    symbol: str
    name: str
    quantity: float
    avg_cost: float
    currency: str
    asset_type: str
    as_of: str
    fees_paid: float | None
    taxes_paid: float | None
    corporate_action_note: str
    account_id: str = ""


@dataclass(frozen=True)
class PortfolioImportResult:
    source: str
    imported_at: str
    positions: list[PortfolioPosition]
    output_path: Path
    audit_path: Path
    audit_gaps: list[str]


@dataclass(frozen=True)
class PortfolioValuation:
    symbol: str
    name: str
    asset_type: str
    currency: str
    price: float | None
    price_date: str
    fx_as_of: str
    value_base: float
    target_weight: float
    actual_weight: float
    drift: float


@dataclass(frozen=True)
class PortfolioCheckResult:
    portfolio_path: Path
    policy_path: Path
    positions_path: Path | None
    position_source: str
    position_audit_gaps: list[str]
    created_at: str
    targets: list[PortfolioTarget]
    policy_result: PolicyValidationResult
    base_currency: str
    currencies: list[str]
    foreign_currency_symbols: list[str]
    missing_fx_currencies: list[str]
    valuations: list[PortfolioValuation]
    valuation_gaps: list[str]
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
        if self.missing_fx_currencies:
            return "政策通过，等待 FX"
        if self.valuation_gaps:
            return "政策通过，等待估值数据"
        if self.valuations:
            return "政策通过，已生成估值快照"
        if self.foreign_currency_symbols:
            return "政策通过，FX 已缓存"
        return "可继续模拟练习"


def check_portfolio(
    *,
    portfolio_path: Path,
    policy_path: Path,
    output_dir: Path | None = None,
    positions_path: Path | None = None,
    now: datetime | None = None,
) -> PortfolioCheckResult:
    targets = load_portfolio_targets(portfolio_path)
    policy = load_policy(policy_path)
    policy_result = validate_policy(policy)
    errors: list[str] = []
    warnings: list[str] = []
    symbols = [target.symbol for target in targets]
    imported_positions: dict[str, PortfolioPosition] = {}
    position_source = ""
    position_audit_gaps: list[str] = []
    if positions_path is not None:
        position_source, imported_positions, position_audit_gaps = load_imported_positions(
            positions_path
        )
        if position_audit_gaps:
            warnings.extend("持仓导入审计缺口: " + gap for gap in position_audit_gaps)
        missing_positions = [
            target.symbol for target in targets if target.symbol not in imported_positions
        ]
        if missing_positions:
            warnings.append(
                "导入持仓未覆盖目标代码: " + ", ".join(missing_positions)
            )
        extra_positions = sorted(set(imported_positions).difference(symbols))
        if extra_positions:
            warnings.append(
                "导入持仓包含未配置目标代码，当前不会计入目标偏离: "
                + ", ".join(extra_positions)
            )

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
    foreign_currencies: set[str] = set()
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
            imported = imported_positions.get(target.symbol)
            currency = (imported.currency if imported else "") or target.currency or (
                prices[target.symbol].currency if target.symbol in prices else ""
            )
            if currency:
                normalized_currency = currency.upper()
                currencies.add(normalized_currency)
                if normalized_currency != policy.base_currency:
                    foreign_currency_symbols.append(target.symbol)
                    foreign_currencies.add(normalized_currency)
    else:
        for target in targets:
            imported = imported_positions.get(target.symbol)
            if target.currency:
                normalized_currency = target.currency.upper()
            elif imported is not None:
                normalized_currency = imported.currency.upper()
            else:
                continue
            currencies.add(normalized_currency)
            if normalized_currency != policy.base_currency and target.symbol != "CASH":
                foreign_currency_symbols.append(target.symbol)
                foreign_currencies.add(normalized_currency)
    missing_fx_currencies = sorted(foreign_currencies)
    if output_dir is not None and missing_fx_currencies:
        cached_fx = read_cached_fx_rates(
            output_dir=output_dir,
            base_currency=policy.base_currency,
            quote_currencies=sorted(foreign_currencies),
            now=now,
        )
        cached_quotes = {rate.quote_currency for rate in cached_fx}
        missing_fx_currencies = sorted(foreign_currencies.difference(cached_quotes))
    if foreign_currency_symbols and missing_fx_currencies:
        warnings.append(
            "识别到非基础货币代码: "
            + ", ".join(foreign_currency_symbols)
            + f"；基础货币为 {policy.base_currency}，缺少 FX provider，暂不计算跨币种总值。"
        )
    elif foreign_currency_symbols:
        warnings.append(
            "FX 缓存已覆盖非基础货币代码，将生成带日期的只读估值快照；"
            "结果不等于券商结算价值。"
        )

    valuations: list[PortfolioValuation] = []
    valuation_gaps: list[str] = []
    if output_dir is not None:
        snapshot = build_cached_data_snapshot(output_dir)
        prices = {price.symbol.upper(): price for price in snapshot.prices}
        cached_fx = read_cached_fx_rates(
            output_dir=output_dir,
            base_currency=policy.base_currency,
            quote_currencies=sorted(foreign_currencies),
            now=now,
        )
        fx_by_quote = {rate.quote_currency: rate for rate in cached_fx}
        pending_values: list[
            tuple[PortfolioTarget, str, float | None, str, str, float]
        ] = []
        for target in targets:
            imported = imported_positions.get(target.symbol)
            if positions_path is not None and imported is None:
                valuation_gaps.append(f"{target.symbol}: 导入持仓没有该代码。")
                continue
            if target.symbol == "CASH":
                currency = (
                    (imported.currency if imported else "")
                    or target.currency
                    or policy.base_currency
                )
                price = None
                price_date = ""
                native_value = imported.quantity if imported else target.quantity
            else:
                price_row = prices.get(target.symbol)
                if price_row is None:
                    valuation_gaps.append(f"{target.symbol}: 缺少行情，无法计算当前价值。")
                    continue
                currency = (
                    (imported.currency if imported else "")
                    or target.currency
                    or price_row.currency.upper()
                )
                if target.currency and target.currency != price_row.currency.upper():
                    valuation_gaps.append(
                        f"{target.symbol}: CSV 币种 {target.currency} 与行情币种 "
                        f"{price_row.currency.upper()} 不一致。"
                    )
                    continue
                price = price_row.close
                price_date = price_row.date
                native_value = (imported.quantity if imported else target.quantity) * price
            if currency == policy.base_currency:
                base_value = native_value
                fx_as_of = ""
            else:
                fx = fx_by_quote.get(currency)
                if fx is None:
                    valuation_gaps.append(
                        f"{target.symbol}: 缺少 {policy.base_currency}/{currency} FX。"
                    )
                    continue
                base_value = native_value / fx.rate
                fx_as_of = fx.as_of
            pending_values.append(
                (target, currency, price, price_date, fx_as_of, base_value)
            )
        total_value = sum(item[-1] for item in pending_values)
        if valuation_gaps:
            warnings.append("当前价值快照不完整: " + "；".join(valuation_gaps))
        elif total_value <= 0:
            valuation_gaps.append("当前价值合计不大于 0，无法计算实际比例。")
        else:
            valuations = [
                PortfolioValuation(
                    symbol=target.symbol,
                    name=target.name,
                    asset_type=target.asset_type,
                    currency=currency,
                    price=price,
                    price_date=price_date,
                    fx_as_of=fx_as_of,
                    value_base=base_value,
                    target_weight=target.target_weight,
                    actual_weight=base_value / total_value,
                    drift=base_value / total_value - target.target_weight,
                )
                for target, currency, price, price_date, fx_as_of, base_value in pending_values
            ]

    return PortfolioCheckResult(
        portfolio_path=portfolio_path,
        policy_path=policy_path,
        positions_path=positions_path,
        position_source=position_source,
        position_audit_gaps=position_audit_gaps,
        created_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        targets=targets,
        policy_result=policy_result,
        base_currency=policy.base_currency,
        currencies=sorted(currencies),
        foreign_currency_symbols=foreign_currency_symbols,
        missing_fx_currencies=missing_fx_currencies,
        valuations=valuations,
        valuation_gaps=valuation_gaps,
        total_target_weight=total_weight,
        cash_target_weight=cash_weight,
        experimental_target_weight=experimental_weight,
        missing_price_symbols=missing_price_symbols,
        errors=errors,
        warnings=warnings,
    )


def import_portfolio_positions(
    *,
    positions_path: Path,
    output_dir: Path,
    source: str,
    now: datetime | None = None,
) -> PortfolioImportResult:
    normalized_source = source.strip()
    if not normalized_source:
        raise ValueError("持仓导入必须提供来源名称。")
    positions = load_portfolio_positions(positions_path)
    imported_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    audit_gaps: list[str] = []
    if any(position.fees_paid is None for position in positions):
        audit_gaps.append("导出文件没有完整提供已支付费用。")
    if any(position.taxes_paid is None for position in positions):
        audit_gaps.append("导出文件没有完整提供已支付税费。")
    if any(not position.corporate_action_note for position in positions):
        audit_gaps.append("导出文件没有公司行动核对说明。")
    if any(not position.account_id for position in positions):
        audit_gaps.append("导出文件没有账户标识，无法区分多账户持仓。")

    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "portfolio-positions.json"
    payload = {
        "provider": normalized_source,
        "source": normalized_source,
        "imported_at": imported_at,
        "rows": [asdict(position) for position in positions],
        "audit_gaps": audit_gaps,
        "disclaimer": "只读持仓导入；不执行交易，不代表券商账单已完成对账。",
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    audit_dir = output_dir / "research"
    audit_dir.mkdir(parents=True, exist_ok=True)
    timestamp = imported_at.replace("-", "").replace(":", "").replace("+00:00", "Z")
    audit_path = audit_dir / f"portfolio-import-{timestamp}.json"
    audit_path.write_text(
        json.dumps(
            {
                "source": normalized_source,
                "imported_at": imported_at,
                "input_path": str(positions_path),
                "output_path": str(output_path),
                "position_count": len(positions),
                "audit_gaps": audit_gaps,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return PortfolioImportResult(
        normalized_source,
        imported_at,
        positions,
        output_path,
        audit_path,
        audit_gaps,
    )


def load_portfolio_positions(path: Path) -> list[PortfolioPosition]:
    if not path.exists():
        raise ValueError(f"持仓文件不存在: {path}")
    required = {
        "symbol",
        "name",
        "quantity",
        "avg_cost",
        "currency",
        "asset_type",
        "as_of",
    }
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        missing = sorted(required.difference(reader.fieldnames or []))
        if missing:
            raise ValueError("持仓文件缺少字段: " + ", ".join(missing))
        positions: list[PortfolioPosition] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                symbol = str(row["symbol"] or "").strip().upper()
                name = str(row["name"] or "").strip()
                quantity = float(row["quantity"] or "")
                avg_cost = float(row["avg_cost"] or "")
                currency = str(row["currency"] or "").strip().upper()
                asset_type = str(row["asset_type"] or "").strip().lower()
                as_of = str(row["as_of"] or "").strip()
                fees_paid = _optional_nonnegative_float(row.get("fees_paid"))
                taxes_paid = _optional_nonnegative_float(row.get("taxes_paid"))
                corporate_action_note = str(row.get("corporate_action_note") or "").strip()
                account_id = str(row.get("account_id") or "").strip()
            except (TypeError, ValueError) as error:
                raise ValueError(f"持仓文件第 {line_number} 行包含无效数字。") from error
            if not symbol or not name or not currency or not asset_type or not as_of:
                raise ValueError(f"持仓文件第 {line_number} 行缺少必填字段。")
            if quantity < 0 or avg_cost < 0:
                raise ValueError(f"持仓文件第 {line_number} 行数量或成本无效。")
            try:
                datetime.fromisoformat(as_of)
            except ValueError as error:
                raise ValueError(
                    f"持仓文件第 {line_number} 行 as_of 必须是 ISO 日期或时间。"
                ) from error
            positions.append(
                PortfolioPosition(
                    symbol,
                    name,
                    quantity,
                    avg_cost,
                    currency,
                    asset_type,
                    as_of,
                    fees_paid,
                    taxes_paid,
                    corporate_action_note,
                    account_id,
                )
            )
    if not positions:
        raise ValueError("持仓文件没有任何持仓行。")
    return positions


def load_imported_positions(
    path: Path,
) -> tuple[str, dict[str, PortfolioPosition], list[str]]:
    if not path.exists():
        raise ValueError(f"导入持仓快照不存在: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"导入持仓快照不是有效 JSON: {path}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise ValueError("导入持仓快照缺少 rows。")
    audit_gaps = [
        str(item)
        for item in payload.get("audit_gaps", [])
        if isinstance(item, str) and item.strip()
    ]
    positions: dict[str, PortfolioPosition] = {}
    for row in payload["rows"]:
        if not isinstance(row, dict):
            raise ValueError("导入持仓快照包含无效行。")
        try:
            position = PortfolioPosition(
                symbol=str(row["symbol"]),
                name=str(row["name"]),
                quantity=float(row["quantity"]),
                avg_cost=float(row["avg_cost"]),
                currency=str(row["currency"]).upper(),
                asset_type=str(row["asset_type"]),
                as_of=str(row["as_of"]),
                fees_paid=(
                    float(row["fees_paid"])
                    if row.get("fees_paid") is not None
                    else None
                ),
                taxes_paid=(
                    float(row["taxes_paid"])
                    if row.get("taxes_paid") is not None
                    else None
                ),
                corporate_action_note=str(row.get("corporate_action_note") or ""),
                account_id=str(row.get("account_id") or ""),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("导入持仓快照包含无法解析的行。") from error
        if position.symbol not in positions:
            positions[position.symbol] = position
            continue
        positions[position.symbol] = _merge_imported_positions(
            positions[position.symbol],
            position,
        )
        audit_gaps.append(
            f"{position.symbol} 出现在多个账户，当前按数量合并，需人工核对账户、费用和公司行动。"
        )
    source = str(payload.get("source") or payload.get("provider") or "")
    return source, positions, audit_gaps


def _merge_imported_positions(
    first: PortfolioPosition,
    second: PortfolioPosition,
) -> PortfolioPosition:
    quantity = first.quantity + second.quantity
    if quantity:
        avg_cost = (
            first.avg_cost * first.quantity + second.avg_cost * second.quantity
        ) / quantity
    else:
        avg_cost = 0.0
    account_ids = [
        account_id
        for account_id in [first.account_id, second.account_id]
        if account_id
    ]
    fees_paid = (
        first.fees_paid + second.fees_paid
        if first.fees_paid is not None and second.fees_paid is not None
        else None
    )
    taxes_paid = (
        first.taxes_paid + second.taxes_paid
        if first.taxes_paid is not None and second.taxes_paid is not None
        else None
    )
    notes = [
        note
        for note in [first.corporate_action_note, second.corporate_action_note]
        if note
    ]
    return PortfolioPosition(
        symbol=first.symbol,
        name=first.name or second.name,
        quantity=quantity,
        avg_cost=avg_cost,
        currency=first.currency,
        asset_type=first.asset_type,
        as_of=max(first.as_of, second.as_of),
        fees_paid=fees_paid,
        taxes_paid=taxes_paid,
        corporate_action_note="；".join(dict.fromkeys(notes)),
        account_id=";".join(dict.fromkeys(account_ids)),
    )


def _optional_nonnegative_float(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    number = float(text)
    if number < 0:
        raise ValueError("费用或税费不能为负数。")
    return number


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
    payload["status_label"] = result.status_label
    payload["positions_path"] = (
        str(result.positions_path) if result.positions_path is not None else None
    )
    payload["policy_result"] = result.policy_result.model_dump()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
