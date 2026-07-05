from lychee_alphadesk.core.config import default_config
from lychee_alphadesk.core.discovery import build_today_discovery_report


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
