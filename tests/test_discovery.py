import json
from pathlib import Path

import pytest

import lychee_alphadesk.core.discovery as discovery_module
from lychee_alphadesk.core.config import default_config
from lychee_alphadesk.core.discovery import (
    build_today_discovery_report,
    discovery_report_summary,
)
from lychee_alphadesk.core.live_data import PullResult


def test_today_discovery_uses_llm_json_response() -> None:
    config = default_config()
    config.llm.openai_compatible.base_url = "https://llm.example.com/v1"
    config.llm.openai_compatible.api_key = "sk-demo-secret"
    config.llm.openai_compatible.model = "demo-model"

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        assert "chat/completions" in url
        prompt = str(body["messages"])
        assert '"markets": ["US"]' in prompt
        assert "AI 基础设施观察" in prompt
        assert "最多 3 个主题" in prompt
        assert "最多 5 个关注候选" in prompt
        return {
            "choices": [
                {
                    "message": {
                        "content": """
                        {
                          "themes": [
                            {
                              "name": "模型生成的 AI 主题",
                              "markets": ["US"],
                              "summary": "模型选择了 AI 观察主题。",
                              "evidence": ["LLM 检查了提供的起始语境。"],
                              "sectors": ["半导体"],
                              "risk_flags": ["估值风险"],
                              "confidence": "medium"
                            }
                          ],
                          "candidates": [
                            {
                              "display_name": "模型生成的 NVIDIA 候选",
                              "symbol": "NVDA",
                              "market": "US",
                              "asset_type": "stock",
                              "related_theme": "模型生成的 AI 主题",
                              "why_watch": "模型把 NVIDIA 和 AI 基础设施联系起来。",
                              "evidence": ["起始语境提到了 AI 算力。"],
                              "risk_flags": ["交易拥挤风险"],
                              "next_actions": ["拉取 SEC 公告"],
                              "confidence": "medium",
                              "recommendation": "research"
                            }
                          ],
                          "warnings": ["模型风险提示"],
                          "next_actions": ["继续查看公告"]
                        }
                        """
                    }
                }
            ]
        }

    report = build_today_discovery_report(["US"], config=config, post_json=fake_post)

    assert report.mode == "llm-synthesized"
    assert report.markets == ["US"]
    assert report.themes[0].name == "模型生成的 AI 主题"
    assert report.candidates[0].display_name == "模型生成的 NVIDIA 候选"
    assert report.warnings == [
        "模型风险提示",
        "候选仅用于研究和观察，不是买入或卖出建议。",
    ]


def test_today_discovery_prepares_market_news_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = default_config()
    config.llm.openai_compatible.base_url = "https://llm.example.com/v1"
    config.llm.openai_compatible.api_key = "sk-demo-secret"
    config.llm.openai_compatible.model = "demo-model"

    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == []
        assert kwargs["provider_id"] == "auto"
        assert kwargs["output_dir"] == tmp_path
        assert kwargs["config"] == config
        assert kwargs["force"] is False
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        news_path = data_dir / "news-events.json"
        news_path.write_text(
            json.dumps(
                {
                    "provider": "newsapi",
                    "created_at": "2026-07-06T10:00:00+00:00",
                    "warnings": [],
                    "rows": [
                        {
                            "timestamp": "2026-07-06T10:00:00+00:00",
                            "headline": "Market headline for discovery",
                            "summary": "Market news should reach the LLM context.",
                            "symbols": ["MARKET"],
                            "source_url": "https://example.com/market-news",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", 1, news_path, [])

    monkeypatch.setattr(
        discovery_module,
        "pull_news_events",
        fake_pull_news_events,
        raising=False,
    )

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        prompt = str(body["messages"])
        assert "Market headline for discovery" in prompt
        assert "news_001" in prompt
        assert "evidence_pack" in prompt
        return _llm_payload()

    report = build_today_discovery_report(
        ["US"],
        config=config,
        output_dir=tmp_path,
        post_json=fake_post,
    )

    assert report.themes[0].name == "市场新闻驱动主题"


def test_discovery_summary_expands_news_evidence_ids(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-03T07:59:56Z",
                        "headline": "The AI infrastructure boom is sending these stocks soaring",
                        "summary": "Data center and chip demand are driving market attention.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/ai-infra",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = _report_with_news_evidence_id()

    summary = discovery_report_summary(report, output_dir=tmp_path)

    assert "证据来源" in summary
    assert "news_001" in summary
    assert "The AI infrastructure boom is sending these stocks soaring" in summary
    assert "https://example.com/ai-infra" in summary


def test_today_discovery_rejects_non_id_evidence_when_pack_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _llm_config()
    _patch_market_news(monkeypatch, tmp_path, config)

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        return _llm_payload_with_evidence(["Market headline for discovery"])

    with pytest.raises(RuntimeError, match="证据 ID"):
        build_today_discovery_report(
            ["US"],
            config=config,
            output_dir=tmp_path,
            post_json=fake_post,
        )


def test_today_discovery_rejects_unknown_evidence_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _llm_config()
    _patch_market_news(monkeypatch, tmp_path, config)

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        return _llm_payload_with_evidence(["news_999"])

    with pytest.raises(RuntimeError, match="未知证据 ID"):
        build_today_discovery_report(
            ["US"],
            config=config,
            output_dir=tmp_path,
            post_json=fake_post,
        )


def test_today_discovery_stops_when_market_news_preparation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = default_config()
    config.llm.openai_compatible.base_url = "https://llm.example.com/v1"
    config.llm.openai_compatible.api_key = "sk-demo-secret"
    config.llm.openai_compatible.model = "demo-model"

    def fake_pull_news_events(**kwargs: object) -> PullResult:
        raise ValueError("尚未配置新闻数据源")

    monkeypatch.setattr(
        discovery_module,
        "pull_news_events",
        fake_pull_news_events,
        raising=False,
    )

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        raise AssertionError("LLM should not run when market news preparation fails")

    with pytest.raises(RuntimeError, match="市场级新闻准备失败"):
        build_today_discovery_report(
            ["US"],
            config=config,
            output_dir=tmp_path,
            post_json=fake_post,
        )


def _llm_payload() -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": """
                    {
                      "themes": [
                        {
                          "name": "市场新闻驱动主题",
                          "markets": ["US"],
                          "summary": "模型基于市场新闻生成主题。",
                          "evidence": ["news_001"],
                          "sectors": ["Technology"],
                          "risk_flags": ["新闻样本有限"],
                          "confidence": "medium"
                        }
                      ],
                      "candidates": [
                        {
                          "display_name": "市场新闻候选",
                          "symbol": null,
                          "market": "US",
                          "asset_type": "sector",
                          "related_theme": "市场新闻驱动主题",
                          "why_watch": "模型引用了市场新闻。",
                          "evidence": ["news_001"],
                          "risk_flags": ["需要继续钻取"],
                          "next_actions": ["拉取相关个股新闻"],
                          "confidence": "medium",
                          "recommendation": "research"
                        }
                      ],
                      "warnings": [],
                      "next_actions": ["继续查看新闻来源"]
                    }
                    """
                }
            }
        ]
    }


def _llm_payload_with_evidence(evidence: list[str]) -> dict[str, object]:
    content = {
        "themes": [
            {
                "name": "测试主题",
                "markets": ["US"],
                "summary": "测试摘要。",
                "evidence": evidence,
                "sectors": ["Technology"],
                "risk_flags": ["测试风险"],
                "confidence": "medium",
            }
        ],
        "candidates": [
            {
                "display_name": "测试候选",
                "symbol": None,
                "market": "US",
                "asset_type": "sector",
                "related_theme": "测试主题",
                "why_watch": "测试原因。",
                "evidence": evidence,
                "risk_flags": ["测试风险"],
                "next_actions": ["继续研究"],
                "confidence": "medium",
                "recommendation": "research",
            }
        ],
        "warnings": [],
        "next_actions": ["继续研究"],
    }
    return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}


def _llm_config() -> object:
    config = default_config()
    config.llm.openai_compatible.base_url = "https://llm.example.com/v1"
    config.llm.openai_compatible.api_key = "sk-demo-secret"
    config.llm.openai_compatible.model = "demo-model"
    return config


def _patch_market_news(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: object,
) -> None:
    def fake_pull_news_events(**kwargs: object) -> PullResult:
        assert kwargs["symbols"] == []
        assert kwargs["config"] == config
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        news_path = data_dir / "news-events.json"
        news_path.write_text(
            json.dumps(
                {
                    "provider": "newsapi",
                    "rows": [
                        {
                            "timestamp": "2026-07-06T10:00:00+00:00",
                            "headline": "Market headline for discovery",
                            "summary": "Market news should reach the LLM context.",
                            "symbols": ["MARKET"],
                            "source_url": "https://example.com/market-news",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PullResult("news", "newsapi", 1, news_path, [])

    monkeypatch.setattr(
        discovery_module,
        "pull_news_events",
        fake_pull_news_events,
        raising=False,
    )


def _report_with_news_evidence_id() -> discovery_module.DiscoveryReport:
    return discovery_module.DiscoveryReport(
        mode="llm-synthesized",
        created_at="2026-07-05T00:00:00+00:00",
        markets=["US"],
        sources=[
            discovery_module.DiscoverySource(
                provider="openai-compatible-llm",
                market="US",
                description="test",
            )
        ],
        themes=[
            discovery_module.DiscoveryTheme(
                name="AI 基础设施主题",
                markets=["US"],
                summary="测试主题。",
                evidence=["news_001"],
                sectors=["Technology"],
                risk_flags=["样本有限"],
                confidence="medium",
            )
        ],
        candidates=[
            discovery_module.DiscoveryCandidate(
                display_name="AI 基础设施候选",
                symbol=None,
                market="US",
                asset_type="sector",
                related_theme="AI 基础设施主题",
                why_watch="测试原因。",
                evidence=["news_001"],
                risk_flags=["需要验证"],
                next_actions=["继续研究"],
                confidence="medium",
                recommendation="research",
            )
        ],
        warnings=[],
        next_actions=["继续研究"],
        disclaimer="非投资建议。",
    )
