from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from lychee_alphadesk.core.cache_freshness import (
    evaluate_research_metrics_cache,
    record_research_metrics_cache,
)
from lychee_alphadesk.core.config import load_config
from lychee_alphadesk.core.live_data import (
    PullResult,
    ResearchMetric,
    _merge_research_metric_cache_rows,
    _read_cache,
    _write_cache,
)

JsonFetcher = Callable[[str, dict[str, str] | None], object]


class MacroProviderError(RuntimeError):
    pass


def pull_macro_series(
    *,
    provider_id: str,
    series: list[str],
    output_dir: Path,
    config_path: Path | None = None,
    fetch_json: JsonFetcher | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> PullResult:
    provider = provider_id.strip().lower()
    normalized_series = [item.strip() for item in series if item.strip()]
    if provider not in {"fred", "hkma"}:
        raise ValueError("宏观数据源只能选择 fred 或 hkma。")
    if not normalized_series:
        raise ValueError("请至少提供一个宏观序列。")
    symbols = [f"MACRO:{provider}:{item.upper()}" for item in normalized_series]
    freshness = evaluate_research_metrics_cache(
        output_dir=output_dir,
        provider=provider,
        symbols=symbols,
        now=now,
        force=force,
    )
    if not freshness.should_refresh and freshness.entry is not None:
        rows = [
            row
            for row in _read_cache(output_dir, "research-metrics.json").rows
            if str(row.get("provider") or "").strip().lower() == provider
            and str(row.get("domain") or "").strip().lower() == "macro"
        ]
        return PullResult(
            "macro",
            freshness.entry.provider,
            len(rows),
            freshness.entry.artifact_path,
            [freshness.reason],
            refreshed=False,
        )

    fetcher = fetch_json or _fetch_json
    config = load_config(config_path)
    rows: list[ResearchMetric] = []
    warnings: list[str] = []
    for series_id in normalized_series:
        try:
            if provider == "fred":
                rows.append(_pull_fred_latest(series_id, config, fetcher))
            else:
                rows.extend(_pull_hkma_latest(series_id, fetcher))
        except (MacroProviderError, ValueError) as error:
            warnings.append(str(error))

    cache_rows = _merge_research_metric_cache_rows(
        output_dir,
        [asdict(row) for row in rows],
    )
    output_path = _write_cache(
        output_dir=output_dir,
        filename="research-metrics.json",
        provider=provider,
        rows=cache_rows,
        warnings=warnings,
        now=now,
    )
    record_research_metrics_cache(
        output_dir=output_dir,
        provider=provider,
        symbols=symbols,
        artifact_path=output_path,
        row_count=len(rows),
        now=now,
        forced=force,
    )
    if not rows and warnings:
        raise MacroProviderError("宏观数据拉取失败: " + "；".join(warnings))
    return PullResult("macro", provider, len(rows), output_path, warnings)


def _pull_fred_latest(
    series_id: str,
    config: object,
    fetcher: JsonFetcher,
) -> ResearchMetric:
    provider = getattr(config, "providers", {}).get("fred")
    api_key = getattr(provider, "value", None)
    if not isinstance(api_key, str) or not api_key.strip():
        raise MacroProviderError(
            "FRED 未配置 API key；请运行 `lychee setup set fred <API_KEY>`。"
        )
    source_url = f"https://api.stlouisfed.org/fred/series/observations?series_id={urllib.parse.quote(series_id.upper())}"
    payload = fetcher(
        f"{source_url}&api_key={urllib.parse.quote(api_key.strip())}&file_type=json&sort_order=desc&limit=1",
        None,
    )
    observations = _dict_value(payload).get("observations")
    if not isinstance(observations, list):
        raise MacroProviderError(f"FRED {series_id} 返回中没有 observations。")
    latest = next(
        (
            item
            for item in observations
            if isinstance(item, dict)
            and _is_numeric(item.get("value"))
            and isinstance(item.get("date"), str)
        ),
        None,
    )
    if latest is None:
        raise MacroProviderError(f"FRED {series_id} 没有可用的最新观测值。")
    return ResearchMetric(
        symbol=f"MACRO:FRED:{series_id.upper()}",
        domain="macro",
        name=f"FRED {series_id.upper()}",
        value=str(latest["value"]),
        as_of=str(latest["date"]),
        source_url=source_url,
        note="FRED 官方序列最新观测；数值单位和修订规则以序列元数据为准。",
        provider="fred",
    )


def _pull_hkma_latest(series_id: str, fetcher: JsonFetcher) -> list[ResearchMetric]:
    segment = series_id.strip() or "hibor.fixing"
    if segment not in {"hibor.fixing", "hibor", "honia"}:
        raise ValueError("HKMA 当前支持 hibor.fixing、hibor 或 honia。")
    endpoint = (
        "https://api.hkma.gov.hk/public/market-data-and-statistics/"
        "monthly-statistical-bulletin/er-ir/hk-interbank-ir-daily"
    )
    source_url = f"{endpoint}?segment={urllib.parse.quote(segment)}"
    payload = fetcher(source_url, None)
    result = _dict_value(_dict_value(payload).get("result"))
    records = result.get("records")
    if not isinstance(records, list):
        raise MacroProviderError(f"HKMA {segment} 返回中没有 records。")
    latest = next(
        (item for item in records if isinstance(item, dict) and item.get("end_of_day")),
        None,
    )
    if latest is None:
        raise MacroProviderError(f"HKMA {segment} 没有可用的最新记录。")
    as_of = str(latest["end_of_day"])
    rows: list[ResearchMetric] = []
    for key, value in latest.items():
        if key == "end_of_day" or not _is_numeric(value):
            continue
        rows.append(
            ResearchMetric(
                symbol=f"MACRO:HKMA:{segment.upper()}",
                domain="macro",
                name=f"HKMA {segment} {key}",
                value=str(value),
                as_of=as_of,
                source_url=source_url,
                note="HKMA 官方宏观序列最新记录。",
                provider="hkma",
            )
        )
    if not rows:
        raise MacroProviderError(f"HKMA {segment} 最新记录没有数值字段。")
    return rows


def _fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise MacroProviderError(f"无法从 {url} 获取宏观 JSON: {error}") from error


def _dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _is_numeric(value: object) -> bool:
    if isinstance(value, int | float):
        return True
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            return False
        return value.strip() != "."
    return False
