import json
from pathlib import Path

import pytest

import lychee_alphadesk.core.forecast as forecast
from lychee_alphadesk.core.forecast import (
    ForecastProviderError,
    generate_timesfm_forecasts,
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
