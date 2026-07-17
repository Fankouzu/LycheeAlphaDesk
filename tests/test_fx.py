from datetime import UTC, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.fx import pull_ecb_fx_rates

ECB_CSV = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE
D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-07-16,0.90
D.HKD.EUR.SP00.A,D,HKD,EUR,SP00,A,2026-07-16,9.00
D.CNY.EUR.SP00.A,D,CNY,EUR,SP00,A,2026-07-16,7.20
"""


def test_pull_ecb_fx_rates_derives_cross_rates_and_records_cache(tmp_path: Path) -> None:
    urls: list[str] = []

    def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
        del headers
        urls.append(url)
        return ECB_CSV

    result = pull_ecb_fx_rates(
        base_currency="USD",
        quote_currencies=["HKD", "CNY"],
        output_dir=tmp_path,
        fetch_text=fetch_text,
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )

    assert result.count == 2
    assert result.rates[0].quote_currency == "CNY"
    assert result.rates[0].rate == 8.0
    assert result.rates[1].quote_currency == "HKD"
    assert result.rates[1].rate == 10.0
    assert "data-api.ecb.europa.eu" in urls[0]
    assert (tmp_path / "data" / "fx-rates.json").exists()


def test_pull_ecb_fx_rates_reuses_fresh_cache(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
        del headers
        calls.append(url)
        return ECB_CSV

    first = pull_ecb_fx_rates(
        base_currency="USD",
        quote_currencies=["HKD"],
        output_dir=tmp_path,
        fetch_text=fetch_text,
        now=datetime(2026, 7, 17, tzinfo=UTC),
    )
    second = pull_ecb_fx_rates(
        base_currency="USD",
        quote_currencies=["HKD"],
        output_dir=tmp_path,
        fetch_text=lambda *_: (_ for _ in ()).throw(AssertionError("不应访问网络")),
        now=datetime(2026, 7, 17, tzinfo=UTC) + timedelta(hours=1),
    )

    assert first.refreshed is True
    assert second.refreshed is False
    assert second.warnings == ["FX 缓存仍在保质期内，跳过刷新。"]
    assert len(calls) == 1
