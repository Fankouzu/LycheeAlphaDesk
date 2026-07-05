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
        return {
            "choices": [
                {
                    "message": {
                        "content": """
                        {
                          "themes": [
                            {
                              "name": "Model generated AI theme",
                              "markets": ["US"],
                              "summary": "The model selected an AI watch theme.",
                              "evidence": ["LLM inspected the provided starter context."],
                              "sectors": ["Semiconductors"],
                              "risk_flags": ["Valuation risk"],
                              "confidence": "medium"
                            }
                          ],
                          "candidates": [
                            {
                              "display_name": "Model NVIDIA candidate",
                              "symbol": "NVDA",
                              "market": "US",
                              "asset_type": "stock",
                              "related_theme": "Model generated AI theme",
                              "why_watch": "The model linked NVIDIA to AI infrastructure.",
                              "evidence": ["Starter context mentioned AI compute."],
                              "risk_flags": ["Crowded trade risk"],
                              "next_actions": ["Pull SEC filings"],
                              "confidence": "medium",
                              "recommendation": "research"
                            }
                          ],
                          "warnings": ["Model warning"],
                          "next_actions": ["Drill down into filings"]
                        }
                        """
                    }
                }
            ]
        }

    report = build_today_discovery_report(["US"], config=config, post_json=fake_post)

    assert report.mode == "llm-synthesized"
    assert report.markets == ["US"]
    assert report.themes[0].name == "Model generated AI theme"
    assert report.candidates[0].display_name == "Model NVIDIA candidate"
    assert report.warnings == [
        "Model warning",
        "Candidates are research targets only and are not buy/sell recommendations.",
    ]
