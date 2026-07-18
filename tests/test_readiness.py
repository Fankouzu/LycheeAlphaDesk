import json
from pathlib import Path

from lychee_alphadesk.core.config import default_config, save_config, set_openai_compatible_llm
from lychee_alphadesk.core.discovery import (
    DiscoveryCandidate,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryTheme,
)
from lychee_alphadesk.core.readiness import run_readiness_audit
from lychee_alphadesk.core.research_db import write_discovery_research_run


def test_readiness_audit_is_blocked_without_local_research_prerequisites(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = run_readiness_audit(tmp_path)

    assert result.status == "blocked"
    assert not result.is_ready
    assert any(check.key == "llm" and check.status == "error" for check in result.checks)
    market_check = next(check for check in result.checks if check.key == "market")
    news_check = next(check for check in result.checks if check.key == "news")
    assert "lychee data pull market --symbols AAPL --provider auto" in market_check.detail
    assert "lychee data pull news --symbols AAPL --provider auto" in news_check.detail
    coverage = {
        check.key: check
        for check in result.checks
        if check.key.startswith("market-") and check.key.endswith("-coverage")
    }
    assert "--symbols AAPL" in coverage["market-us-coverage"].detail
    assert "--symbols 0700.HK" in coverage["market-hk-coverage"].detail
    assert "--symbols 510300.SH" in coverage["market-cn-coverage"].detail
    assert result.artifact_path.exists()
    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert "不拉取数据" in payload["boundary"]


def test_readiness_audit_reports_ready_when_ai_research_inputs_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config = default_config()
    save_config(config, config_path)
    set_openai_compatible_llm(
        "https://llm.example/v1",
        "secret",
        "model-name",
        path=config_path,
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache = {
        "provider": "fixture",
        "rows": [
            {"symbol": "AAPL", "date": "2026-07-18"},
            {"symbol": "0700.HK", "date": "2026-07-18"},
            {"symbol": "000001.SZ", "date": "2026-07-18"},
        ],
    }
    (data_dir / "market-prices.json").write_text(json.dumps(cache), encoding="utf-8")
    (data_dir / "news-events.json").write_text(
        json.dumps({"provider": "fixture", "rows": [{"headline": "AI demand"}]}),
        encoding="utf-8",
    )
    report = DiscoveryReport(
        mode="fixture",
        created_at="2026-07-18T00:00:00+00:00",
        markets=["US", "HK", "CN"],
        sources=[DiscoverySource("fixture", "US", "fixture")],
        themes=[
            DiscoveryTheme(
                "AI",
                ["US"],
                "AI theme",
                [],
                ["technology"],
                [],
                "medium",
            )
        ],
        candidates=[
            DiscoveryCandidate(
                "Apple",
                "AAPL",
                "US",
                "stock",
                "AI",
                "research entry",
                [],
                [],
                ["verify"],
                "medium",
            )
        ],
        warnings=[],
        next_actions=[],
        disclaimer="research only",
    )
    write_discovery_research_run(report, tmp_path, tmp_path / "data" / "discovery.json")

    result = run_readiness_audit(tmp_path, config_path=config_path)

    assert result.status == "ready"
    assert result.is_ready
    assert all(
        check.status != "error" for check in result.checks if check.required
    )


def test_readiness_keeps_degraded_cache_as_warning_when_rows_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config = default_config()
    save_config(config, config_path)
    set_openai_compatible_llm(
        "https://llm.example/v1",
        "secret",
        "model-name",
        path=config_path,
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market-prices.json").write_text(
        json.dumps(
            {
                "provider": "eastmoney",
                "rows": [{"symbol": "0700.HK", "date": "2026-07-18"}],
                "warnings": ["Tushare hk_daily 权限不足，已使用 Eastmoney"],
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "news-events.json").write_text(
        json.dumps({"provider": "fixture", "rows": [{"headline": "market"}]}),
        encoding="utf-8",
    )
    write_discovery_research_run(
        DiscoveryReport(
            mode="fixture",
            created_at="2026-07-18T00:00:00+00:00",
            markets=["HK"],
            sources=[DiscoverySource("fixture", "HK", "fixture")],
            themes=[],
            candidates=[
                DiscoveryCandidate(
                    "Tencent",
                    "0700.HK",
                    "HK",
                    "stock",
                    "fixture",
                    "research entry",
                    [],
                    [],
                    ["verify"],
                    "medium",
                )
            ],
            warnings=[],
            next_actions=[],
            disclaimer="research only",
        ),
        tmp_path,
        data_dir / "discovery.json",
    )

    result = run_readiness_audit(tmp_path, config_path=config_path)

    market_check = next(check for check in result.checks if check.key == "market")
    assert market_check.status == "warning"
    assert result.status == "partial"
