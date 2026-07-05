import pytest

from lychee_alphadesk.core.config import default_config
from lychee_alphadesk.core.llm import LLMProviderError, _post_json, request_chat_json


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
    assert calls[0][1]["Accept"] == "text/event-stream"
    assert calls[0][2]["model"] == "demo-model"
    assert calls[0][2]["messages"] == [{"role": "user", "content": "Return JSON."}]
    assert calls[0][2]["stream"] is True


def test_openai_compatible_chat_completion_reads_streaming_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeStreamingResponse:
        headers = {"Content-Type": "text/event-stream; charset=utf-8"}

        def __enter__(self) -> "FakeStreamingResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def readline(self) -> bytes:
            lines = [
                b'data: {"choices":[{"delta":{"content":"{\\"themes\\":"}}]}\n',
                b'data: {"choices":[{"delta":{"content":" [], \\"candidates\\": []"}}]}\n',
                b'data: {"choices":[{"delta":{"content":", \\"next_actions\\": []}"}}]}\n',
                b"data: [DONE]\n",
            ]
            if not hasattr(self, "_line_index"):
                self._line_index = 0
            if self._line_index >= len(lines):
                return b""
            line = lines[self._line_index]
            self._line_index += 1
            return line

    def fake_urlopen(request: object, timeout: int) -> FakeStreamingResponse:
        captured["timeout"] = timeout
        captured["data"] = request.data  # type: ignore[attr-defined]
        return FakeStreamingResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    payload = _post_json(
        "https://llm.example.com/v1/chat/completions",
        {"Authorization": "Bearer sk-demo-secret", "Content-Type": "application/json"},
        {"model": "demo-model", "messages": [], "stream": True},
    )

    assert captured["timeout"] == 180
    assert b'"stream": true' in captured["data"]  # type: ignore[operator]
    assert payload == {
        "choices": [
            {
                "message": {
                    "content": '{"themes": [], "candidates": [], "next_actions": []}'
                }
            }
        ]
    }


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

    with pytest.raises(LLMProviderError, match="有效 JSON"):
        request_chat_json(
            config,
            messages=[{"role": "user", "content": "Return JSON."}],
            post_json=fake_post,
        )
