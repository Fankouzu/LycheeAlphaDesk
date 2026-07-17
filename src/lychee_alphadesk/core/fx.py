import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import record_cache_entry

FX_CACHE_TTL_SECONDS = 24 * 60 * 60
ECB_FX_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{currencies}.EUR.SP00.A"
TextFetcher = Callable[[str, dict[str, str] | None], str]


class FXProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class FXRate:
    base_currency: str
    quote_currency: str
    rate: float
    as_of: str
    provider: str
    source_url: str


@dataclass(frozen=True)
class FXPullResult:
    provider: str
    count: int
    output_path: Path
    rates: list[FXRate]
    refreshed: bool
    warnings: list[str]


def read_cached_fx_rates(
    *,
    output_dir: Path,
    base_currency: str,
    quote_currencies: list[str],
    now: datetime | None = None,
) -> list[FXRate]:
    """Read only fresh, source-backed FX rows; never fetch from the network."""
    base = base_currency.strip().upper()
    quotes = sorted({currency.strip().upper() for currency in quote_currencies if currency.strip()})
    payload = _read_cache(output_dir / "data" / "fx-rates.json")
    rates = _cache_rates(payload, base, quotes)
    cached_at = _parse_datetime(payload.get("retrieved_at"))
    current = _aware(now or datetime.now(UTC))
    if (
        not rates
        or cached_at is None
        or current >= cached_at + timedelta(seconds=FX_CACHE_TTL_SECONDS)
    ):
        return []
    return rates


def pull_ecb_fx_rates(
    *,
    base_currency: str,
    quote_currencies: list[str],
    output_dir: Path,
    fetch_text: TextFetcher | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> FXPullResult:
    base = base_currency.strip().upper()
    quotes = sorted({currency.strip().upper() for currency in quote_currencies if currency.strip()})
    if not base or not quotes:
        raise ValueError("请提供基础货币和至少一个目标货币。")
    currencies = sorted({base, *quotes} - {"EUR"})
    cache_key = f"fx:ecb:{base}:{','.join(quotes)}"
    current = _aware(now or datetime.now(UTC))
    cached = _read_cache(output_dir / "data" / "fx-rates.json")
    cached_rates = _cache_rates(cached, base, quotes)
    cached_at = _parse_datetime(cached.get("retrieved_at"))
    if (
        not force
        and cached_rates
        and cached_at
        and current < cached_at + timedelta(seconds=FX_CACHE_TTL_SECONDS)
    ):
        return FXPullResult(
            provider="ecb",
            count=len(cached_rates),
            output_path=output_dir / "data" / "fx-rates.json",
            rates=cached_rates,
            refreshed=False,
            warnings=["FX 缓存仍在保质期内，跳过刷新。"],
        )

    source_url = ECB_FX_URL.format(currencies=urllib.parse.quote_plus("+".join(currencies)))
    source_url += "?format=csvdata&lastNObservations=5"
    fetcher = fetch_text or _fetch_text
    try:
        payload = fetcher(source_url, {"Accept": "text/csv"})
    except (OSError, urllib.error.URLError, RuntimeError) as error:
        raise FXProviderError(f"ECB FX 请求失败: {error}") from error
    euro_rates = _parse_ecb_csv(payload)
    missing = [
        currency
        for currency in currencies
        if currency != "EUR" and currency not in euro_rates
    ]
    if missing:
        raise FXProviderError("ECB FX 缺少货币: " + ", ".join(missing))
    latest_date = max((date for _, date in euro_rates.values()), default=current.date().isoformat())
    rates = [
        FXRate(
            base_currency=base,
            quote_currency=quote,
            rate=_cross_rate(base, quote, euro_rates),
            as_of=latest_date,
            provider="ecb",
            source_url=source_url,
        )
        for quote in quotes
    ]
    output_path = output_dir / "data" / "fx-rates.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "provider": "ecb",
                "retrieved_at": current.isoformat(timespec="seconds"),
                "base_currency": base,
                "quote_currencies": quotes,
                "rows": [asdict(rate) for rate in rates],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    record_cache_entry(
        output_dir=output_dir,
        layer="fx",
        cache_key=cache_key,
        provider="ecb",
        artifact_path=output_path,
        created_at=current,
        expires_at=current + timedelta(seconds=FX_CACHE_TTL_SECONDS),
        ttl_seconds=FX_CACHE_TTL_SECONDS,
        row_count=len(rates),
        market="FX",
        session_state="ttl",
        is_final_for_session=False,
        forced=force,
    )
    return FXPullResult("ecb", len(rates), output_path, rates, True, [])


def _parse_ecb_csv(payload: str) -> dict[str, tuple[float, str]]:
    rows = csv.DictReader(io.StringIO(payload.lstrip("\ufeff")))
    latest: dict[str, tuple[float, str]] = {}
    for row in rows:
        currency = str(row.get("CURRENCY") or "").strip().upper()
        date = str(row.get("TIME_PERIOD") or "").strip()
        raw_value = str(row.get("OBS_VALUE") or "").strip()
        if not currency or not date or not raw_value:
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        previous = latest.get(currency)
        if previous is None or date > previous[1]:
            latest[currency] = (value, date)
    return latest


def _cross_rate(
    base: str,
    quote: str,
    euro_rates: dict[str, tuple[float, str]],
) -> float:
    if base == quote:
        return 1.0
    base_per_eur = 1.0 if base == "EUR" else euro_rates[base][0]
    quote_per_eur = 1.0 if quote == "EUR" else euro_rates[quote][0]
    if base_per_eur == 0:
        raise FXProviderError(f"ECB FX 返回的 {base} 汇率为 0。")
    return quote_per_eur / base_per_eur


def _cache_rates(
    payload: dict[str, object],
    base: str,
    quotes: list[str],
) -> list[FXRate]:
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        return []
    rates: list[FXRate] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        if row.get("base_currency") != base or row.get("quote_currency") not in quotes:
            continue
        try:
            rates.append(
                FXRate(
                    base_currency=str(row["base_currency"]),
                    quote_currency=str(row["quote_currency"]),
                    rate=float(row["rate"]),
                    as_of=str(row["as_of"]),
                    provider=str(row["provider"]),
                    source_url=str(row["source_url"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rates if len(rates) == len(quotes) else []


def _read_cache(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            if not isinstance(body, bytes):
                raise RuntimeError("ECB FX 响应不是文本字节流")
            return body.decode("utf-8")
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError(str(error)) from error
