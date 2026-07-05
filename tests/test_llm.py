import pytest

from lychee_alphadesk.core.config import default_config
from lychee_alphadesk.core.llm import LLMProviderError, request_chat_json


def test_openai_compatible_chat_completion_posts_json_request() -> None:
    config = default_config()
    config.llm.openai_compatible.base_url = "https://llm.example.com/v1/"
    config.llm.openai_compatible.api_key = "sk-demo-secret"
    config.llm.openai_compatible.model = "demo-model"
    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        calls.append((url, headers, body))
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"themes": [], "candidates": [], "next_actions": []}'
                    }
                }
            ]
        }

    result = request_chat_json(
        config,
        messages=[{"role": "user", "content": "Return JSON."}],
        post_json=fake_post,
    )

    assert result == {"themes": [], "candidates": [], "next_actions": []}
    assert calls[0][0] == "https://llm.example.com/v1/chat/completions"
    assert calls[0][1]["Authorization"] == "Bearer sk-demo-secret"
    assert calls[0][1]["Content-Type"] == "application/json"
    assert calls[0][2]["model"] == "demo-model"
    assert calls[0][2]["messages"] == [{"role": "user", "content": "Return JSON."}]


def test_openai_compatible_chat_completion_rejects_invalid_json() -> None:
    config = default_config()
    config.llm.openai_compatible.base_url = "https://llm.example.com/v1"
    config.llm.openai_compatible.api_key = "sk-demo-secret"
    config.llm.openai_compatible.model = "demo-model"

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> object:
        return {"choices": [{"message": {"content": "not json"}}]}

    with pytest.raises(LLMProviderError, match="valid JSON"):
        request_chat_json(
            config,
            messages=[{"role": "user", "content": "Return JSON."}],
            post_json=fake_post,
        )
