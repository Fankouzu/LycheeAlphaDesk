import json
from pathlib import Path

import pytest

import lychee_alphadesk.core.forecast as forecast
from lychee_alphadesk.core.forecast import (
    ForecastProviderError,
    backtest_forecast_rows,
    generate_timesfm_forecasts,
    run_forecast_backtest,
    run_timesfm_forecast,
)
from lychee_alphadesk.providers.demo import ForecastInterval


class FakeNumpy:
    @staticmethod
    def asarray(values: list[float], dtype: object) -> list[float]:
        return values


class FakeModel:
    def __init__(self) -> None:
        self.compiled = False

    def forecast(self, *, horizon: int, inputs: list[list[float]]) -> tuple[object, object]:
        assert horizon == 5
        assert len(inputs[0]) == 32
        return [[101.0, 102.0, 103.0, 104.0, 105.0]], [
            [[0.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0, 125.0, 130.0]]
        ]


def test_timesfm_adapter_maps_endpoint_and_quantile_interval() -> None:
    result = run_timesfm_forecast(
        symbol="QQQ",
        values=[100.0] * 32,
        horizon_days=5,
        numpy_module=FakeNumpy(),
        model=FakeModel(),
    )

    assert result.symbol == "QQQ"
    assert result.midpoint == 105.0
    assert result.lower == 90.0
    assert result.upper == 130.0
    assert result.method == "timesfm-2.5-200m-pytorch"


def test_timesfm_adapter_rejects_short_history() -> None:
    with pytest.raises(ForecastProviderError, match="至少需要 32"):
        run_timesfm_forecast(
            symbol="QQQ",
            values=[100.0] * 31,
            horizon_days=5,
            model=FakeModel(),
            numpy_module=FakeNumpy(),
        )


def test_timesfm_adapter_reports_missing_optional_runtime() -> None:
    with pytest.raises(ForecastProviderError, match="2.5 PyTorch"):
        run_timesfm_forecast(
            symbol="QQQ",
            values=[100.0] * 32,
            horizon_days=5,
            timesfm_module=object(),
            numpy_module=FakeNumpy(),
        )


def test_generate_timesfm_forecasts_reads_history_and_writes_auditable_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-history.json").write_text(
        json.dumps(
            {
                "rows": [
                    {"symbol": "QQQ", "date": "2026-07-01", "close": 500.0},
                    {"symbol": "QQQ", "date": "2026-07-02", "close": 501.0},
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_run_timesfm_forecast(**kwargs: object) -> ForecastInterval:
        assert kwargs["symbol"] == "QQQ"
        assert kwargs["values"] == [500.0, 501.0]
        return ForecastInterval("QQQ", 5, 490.0, 505.0, 520.0, "fake-timesfm")

    monkeypatch.setattr(forecast, "run_timesfm_forecast", fake_run_timesfm_forecast)
    result = generate_timesfm_forecasts(
        output_dir=tmp_path,
        symbols=["QQQ"],
        horizon_days=5,
    )

    assert result.count == 1
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["provider"] == "timesfm"
    assert payload["rows"][0]["midpoint"] == 505.0


def test_generate_timesfm_forecasts_supports_walk_forward_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = [
        {"symbol": "QQQ", "date": f"2026-07-{index + 1:02d}", "close": 500.0 + index}
        for index in range(60)
    ]
    (data_dir / "market-history.json").write_text(
        json.dumps({"rows": rows}),
        encoding="utf-8",
    )
    calls: list[int] = []

    def fake_run_timesfm_forecast(**kwargs: object) -> ForecastInterval:
        values = kwargs["values"]
        assert isinstance(values, list)
        calls.append(len(values))
        return ForecastInterval("QQQ", 5, 490.0, 505.0, 520.0, "fake-timesfm")

    monkeypatch.setattr(forecast, "run_timesfm_forecast", fake_run_timesfm_forecast)
    monkeypatch.setattr(forecast, "_load_timesfm_runtime", lambda **_: (object(), FakeNumpy()))

    result = generate_timesfm_forecasts(
        output_dir=tmp_path,
        symbols=["QQQ"],
        horizon_days=5,
        windows=2,
        stride=10,
    )

    assert result.count == 2
    assert calls == [45, 55]
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert [row["input_points"] for row in payload["rows"]] == [45, 55]


def test_generate_timesfm_forecasts_rejects_insufficient_walk_forward_history(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = [
        {"symbol": "QQQ", "date": f"2026-07-{index + 1:02d}", "close": 500.0 + index}
        for index in range(40)
    ]
    (data_dir / "market-history.json").write_text(
        json.dumps({"rows": rows}),
        encoding="utf-8",
    )

    with pytest.raises(ForecastProviderError, match="至少需要 52 个"):
        generate_timesfm_forecasts(
            output_dir=tmp_path,
            symbols=["QQQ"],
            horizon_days=5,
            windows=3,
            stride=10,
        )


def test_backtest_forecast_rows_compares_model_with_last_value_baseline() -> None:
    result = backtest_forecast_rows(
        history_rows=[
            {"symbol": "QQQ", "date": "2026-07-01", "close": 100.0},
            {"symbol": "QQQ", "date": "2026-07-02", "close": 101.0},
            {"symbol": "QQQ", "date": "2026-07-03", "close": 102.0},
            {"symbol": "QQQ", "date": "2026-07-04", "close": 110.0},
            {"symbol": "QQQ", "date": "2026-07-05", "close": 112.0},
        ],
        forecast_rows=[
            {
                "symbol": "QQQ",
                "input_end_date": "2026-07-03",
                "horizon_days": 2,
                "lower": 105.0,
                "midpoint": 105.0,
                "upper": 115.0,
            }
        ],
    )

    assert result.symbol == "QQQ"
    assert result.samples == 1
    assert result.mae == 7.0
    assert result.baseline_mae == 10.0
    assert result.interval_coverage == 1.0


def test_backtest_forecast_rows_reports_no_samples_without_future_actual() -> None:
    result = backtest_forecast_rows(
        history_rows=[
            {"symbol": "QQQ", "date": "2026-07-01", "close": 100.0},
            {"symbol": "QQQ", "date": "2026-07-02", "close": 101.0},
        ],
        forecast_rows=[
            {
                "symbol": "QQQ",
                "input_end_date": "2026-07-02",
                "horizon_days": 2,
                "lower": 90.0,
                "midpoint": 105.0,
                "upper": 110.0,
            }
        ],
    )

    assert result.samples == 0
    assert result.mae is None
    assert result.baseline_mae is None
    assert result.interval_coverage is None


def test_run_forecast_backtest_writes_result_artifact(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-history.json").write_text(
        json.dumps(
            {
                "rows": [
                    {"symbol": "QQQ", "date": "2026-07-01", "close": 100.0},
                    {"symbol": "QQQ", "date": "2026-07-02", "close": 101.0},
                    {"symbol": "QQQ", "date": "2026-07-03", "close": 102.0},
                    {"symbol": "QQQ", "date": "2026-07-04", "close": 110.0},
                    {"symbol": "QQQ", "date": "2026-07-05", "close": 112.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "forecasts.json").write_text(
        json.dumps(
            {
                "provider": "timesfm",
                "rows": [
                    {
                        "symbol": "QQQ",
                        "input_end_date": "2026-07-03",
                        "horizon_days": 2,
                        "lower": 105.0,
                        "midpoint": 105.0,
                        "upper": 115.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_forecast_backtest(output_dir=tmp_path, symbols=["QQQ"])

    assert result.count == 1
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["results"][0]["samples"] == 1
    assert "交易建议" in payload["boundary"]
