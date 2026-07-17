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


@dataclass(frozen=True)
class ForecastBacktest:
    symbol: str
    samples: int
    horizon_days: int | None
    mae: float | None
    baseline_mae: float | None
    interval_coverage: float | None


@dataclass(frozen=True)
class ForecastBacktestRun:
    count: int
    output_path: Path
    results: list[ForecastBacktest]


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
        model, numpy_module = _load_timesfm_runtime(
            model_name=model_name,
            max_context=len(values),
            max_horizon=horizon_days,
            timesfm_module=timesfm_module,
            numpy_module=numpy_module,
        )
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
    windows: int = 1,
    stride: int | None = None,
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
    if windows < 1 or windows > 20:
        raise ForecastProviderError("walk-forward 窗口数必须在 1 到 20 之间。")
    if stride is not None and stride < 1:
        raise ForecastProviderError("walk-forward 步长必须大于 0。")
    normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    forecasts: list[ForecastInterval] = []
    forecast_metadata: list[dict[str, object]] = []
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
        step = stride or horizon_days
        latest_cutoff = len(series) if windows == 1 else len(series) - horizon_days
        required_points = 32 + step * (windows - 1)
        if windows > 1 and latest_cutoff < required_points:
            raise ForecastProviderError(
                f"{symbol} 历史行情不足以生成 {windows} 个 walk-forward 窗口；"
                f"至少需要 {required_points} 个上下文交易日和未来 horizon 数据。"
            )
        cutoffs = (
            [latest_cutoff]
            if windows == 1
            else _walk_forward_cutoffs(latest_cutoff, windows, step)
        )
        runtime_model: Any | None = None
        runtime_numpy: Any | None = None
        if windows > 1:
            runtime_model, runtime_numpy = _load_timesfm_runtime(
                model_name=model_name,
                max_context=max(cutoff for cutoff in cutoffs),
                max_horizon=horizon_days,
            )
        for cutoff in cutoffs:
            forecast = run_timesfm_forecast(
                symbol=symbol,
                values=[close for _, close in series[:cutoff]],
                horizon_days=horizon_days,
                model_name=model_name,
                model=runtime_model,
                numpy_module=runtime_numpy,
            )
            forecasts.append(forecast)
            forecast_metadata.append(
                {
                    **asdict(forecast),
                    "input_end_date": series[cutoff - 1][0],
                    "input_points": cutoff,
                }
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
                "windows": windows,
                "stride": stride or horizon_days,
                "rows": forecast_metadata,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return ForecastRun("timesfm", len(forecasts), output_path, forecasts)


def _walk_forward_cutoffs(latest_cutoff: int, windows: int, stride: int) -> list[int]:
    earliest = latest_cutoff - stride * (windows - 1)
    return [earliest + stride * index for index in range(windows)]


def backtest_forecast_rows(
    *,
    history_rows: list[dict[str, object]],
    forecast_rows: list[dict[str, object]],
) -> ForecastBacktest:
    symbols = sorted(
        {
            str(row.get("symbol") or "").strip().upper()
            for row in forecast_rows
            if str(row.get("symbol") or "").strip()
        }
    )
    if not symbols:
        return ForecastBacktest("", 0, None, None, None, None)
    symbol = symbols[0]
    history = sorted(
        [
            row
            for row in history_rows
            if str(row.get("symbol") or "").strip().upper() == symbol
            and isinstance(row.get("date"), str)
            and _number(row.get("close")) is not None
        ],
        key=lambda row: str(row["date"]),
    )
    errors: list[float] = []
    baseline_errors: list[float] = []
    covered = 0
    horizons: list[int] = []
    for forecast in forecast_rows:
        if str(forecast.get("symbol") or "").strip().upper() != symbol:
            continue
        input_end_date = forecast.get("input_end_date")
        horizon = forecast.get("horizon_days")
        midpoint = _number(forecast.get("midpoint"))
        lower = _number(forecast.get("lower"))
        upper = _number(forecast.get("upper"))
        if not isinstance(input_end_date, str) or not isinstance(horizon, int):
            continue
        if midpoint is None or lower is None or upper is None:
            continue
        input_index = next(
            (
                index
                for index, row in enumerate(history)
                if row.get("date") == input_end_date
            ),
            None,
        )
        if input_index is None:
            continue
        target_index = input_index + horizon
        if target_index >= len(history):
            continue
        actual = _number(history[target_index].get("close"))
        baseline = _number(history[input_index].get("close"))
        if actual is None or baseline is None:
            continue
        errors.append(abs(midpoint - actual))
        baseline_errors.append(abs(baseline - actual))
        covered += int(lower <= actual <= upper)
        horizons.append(horizon)
    samples = len(errors)
    return ForecastBacktest(
        symbol=symbol,
        samples=samples,
        horizon_days=horizons[0] if horizons and len(set(horizons)) == 1 else None,
        mae=sum(errors) / samples if samples else None,
        baseline_mae=sum(baseline_errors) / samples if samples else None,
        interval_coverage=covered / samples if samples else None,
    )


def run_forecast_backtest(
    *,
    output_dir: Path,
    symbols: list[str],
) -> ForecastBacktestRun:
    history_path = output_dir / "data" / "market-history.json"
    forecast_path = output_dir / "data" / "forecasts.json"
    if not history_path.exists() or not forecast_path.exists():
        raise ForecastProviderError(
            "回测需要 market-history.json 和 forecasts.json；请先拉取历史行情并运行预测。"
        )
    try:
        history_payload = json.loads(history_path.read_text(encoding="utf-8"))
        forecast_payload = json.loads(forecast_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ForecastProviderError("回测输入缓存不是有效 JSON。") from error
    history_rows = history_payload.get("rows") if isinstance(history_payload, dict) else None
    forecast_rows = forecast_payload.get("rows") if isinstance(forecast_payload, dict) else None
    if not isinstance(history_rows, list) or not isinstance(forecast_rows, list):
        raise ForecastProviderError("回测输入缓存缺少 rows。")
    normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    results = [
        backtest_forecast_rows(
            history_rows=[row for row in history_rows if isinstance(row, dict)],
            forecast_rows=[
                row
                for row in forecast_rows
                if isinstance(row, dict)
                and str(row.get("symbol") or "").strip().upper() == symbol
            ],
        )
        for symbol in normalized_symbols
    ]
    output_dir.joinpath("data").mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "data" / "forecast-backtest.json"
    output_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "provider": str(forecast_payload.get("provider") or "timesfm"),
                "results": [asdict(result) for result in results],
                "boundary": "回测结果只用于评价历史预测误差，不代表未来收益或交易建议。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return ForecastBacktestRun(len(results), output_path, results)


def _number(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _load_timesfm_runtime(
    *,
    model_name: str,
    max_context: int,
    max_horizon: int,
    timesfm_module: Any | None = None,
    numpy_module: Any | None = None,
) -> tuple[Any, Any]:
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
                max_context=min(max(max_context, 32), 16384),
                max_horizon=max(max_horizon, 256),
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )
    except Exception as error:  # third-party model boundary
        raise ForecastProviderError(f"TimesFM 模型加载或编译失败: {error}") from error
    return model, numpy_module


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
