"""Optional local forecasting adapters.

Forecasts are research evidence only.  This module deliberately fails when the
optional TimesFM runtime is unavailable instead of silently substituting a
different model.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lychee_alphadesk.providers.demo import ForecastInterval


class ForecastProviderError(RuntimeError):
    """Raised when a forecast provider cannot produce an auditable result."""


@dataclass(frozen=True)
class ForecastRun:
    provider: str
    count: int
    output_path: Path
    forecasts: list[ForecastInterval]


def run_timesfm_forecast(
    *,
    symbol: str,
    values: Sequence[float],
    horizon_days: int,
    model_name: str = "google/timesfm-2.5-200m-pytorch",
    timesfm_module: Any | None = None,
    numpy_module: Any | None = None,
    model: Any | None = None,
) -> ForecastInterval:
    """Run TimesFM 2.5 on one close-price series and return its endpoint interval."""
    if len(values) < 32:
        raise ForecastProviderError(
            f"{symbol} 历史行情只有 {len(values)} 个交易日，至少需要 32 个交易日才能运行预测。"
        )
    if horizon_days < 1 or horizon_days > 256:
        raise ForecastProviderError("预测 horizon 必须在 1 到 256 个交易日之间。")
    if model is None:
        timesfm_module = timesfm_module or _load_timesfm_module()
        numpy_module = numpy_module or _load_numpy_module()
        model_class = getattr(timesfm_module, "TimesFM_2p5_200M_torch", None)
        if model_class is None:
            raise ForecastProviderError(
                "当前 TimesFM 安装不包含 2.5 PyTorch 模型；请安装 timesfm[torch]>=2.0.2。"
            )
        try:
            model = model_class.from_pretrained(model_name)
            model.compile(
                timesfm_module.ForecastConfig(
                    max_context=min(max(len(values), 32), 16384),
                    max_horizon=max(horizon_days, 256),
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                    force_flip_invariance=True,
                    infer_is_positive=True,
                    fix_quantile_crossing=True,
                )
            )
        except Exception as error:  # third-party model boundary
            raise ForecastProviderError(f"TimesFM 模型加载或编译失败: {error}") from error
    if numpy_module is None:
        numpy_module = _load_numpy_module()
    try:
        inputs = [numpy_module.asarray(list(values), dtype=float)]
        point_forecast, quantile_forecast = model.forecast(
            horizon=horizon_days,
            inputs=inputs,
        )
        point = _nested_number(point_forecast, 0, -1)
        lower = _nested_number(quantile_forecast, 0, -1, 1)
        upper = _nested_number(quantile_forecast, 0, -1, -1)
    except Exception as error:  # third-party model boundary
        raise ForecastProviderError(f"TimesFM 预测执行失败: {error}") from error
    return ForecastInterval(
        symbol=symbol.upper(),
        horizon_days=horizon_days,
        lower=lower,
        midpoint=point,
        upper=upper,
        method="timesfm-2.5-200m-pytorch",
    )


def generate_timesfm_forecasts(
    *,
    output_dir: Path,
    symbols: list[str],
    horizon_days: int,
    model_name: str = "google/timesfm-2.5-200m-pytorch",
) -> ForecastRun:
    history_path = output_dir / "data" / "market-history.json"
    if not history_path.exists():
        raise ForecastProviderError(
            "没有历史行情缓存；请先运行 `lychee data pull history --symbols ...`。"
        )
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ForecastProviderError(f"历史行情缓存不是有效 JSON: {history_path}") from error
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ForecastProviderError("历史行情缓存缺少 rows，无法运行预测。")
    normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    forecasts: list[ForecastInterval] = []
    for symbol in normalized_symbols:
        series = sorted(
            (
                (str(row.get("date")), float(row["close"]))
                for row in rows
                if isinstance(row, dict)
                and str(row.get("symbol") or "").upper() == symbol
                and row.get("date")
                and row.get("close") is not None
            ),
            key=lambda item: item[0],
        )
        if not series:
            raise ForecastProviderError(f"没有找到 {symbol} 的历史行情。")
        forecasts.append(
            run_timesfm_forecast(
                symbol=symbol,
                values=[close for _, close in series],
                horizon_days=horizon_days,
                model_name=model_name,
            )
        )
    output_dir.joinpath("data").mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "data" / "forecasts.json"
    output_path.write_text(
        json.dumps(
            {
                "provider": "timesfm",
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "horizon_days": horizon_days,
                "model": model_name,
                "warnings": [],
                "rows": [asdict(forecast) for forecast in forecasts],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return ForecastRun("timesfm", len(forecasts), output_path, forecasts)


def _load_timesfm_module() -> Any:
    try:
        return importlib.import_module("timesfm")
    except ImportError as error:
        raise ForecastProviderError(
            "TimesFM 未安装。请运行 `uv pip install 'timesfm[torch]>=2.0.2'`，"
            "再运行预测命令。"
        ) from error


def _load_numpy_module() -> Any:
    try:
        return importlib.import_module("numpy")
    except ImportError as error:
        raise ForecastProviderError(
            "TimesFM 运行时缺少 numpy；请重新安装 `timesfm[torch]`。"
        ) from error


def _nested_number(value: Any, *indexes: int) -> float:
    current = value
    for index in indexes:
        current = current[index]
    return float(current)
