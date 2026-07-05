import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol, cast

from lychee_alphadesk.core.config import AlphaDeskConfig

ChatMessage = dict[str, str]
JsonPoster = Callable[[str, dict[str, str], dict[str, object]], object]
LLM_READ_TIMEOUT_SECONDS = 180


class LLMProviderError(RuntimeError):
    pass


class _StreamingResponse(Protocol):
    def readline(self) -> bytes: ...


def request_chat_json(
    config: AlphaDeskConfig,
    *,
    messages: list[ChatMessage],
    post_json: JsonPoster | None = None,
) -> dict[str, object]:
    llm = config.llm.openai_compatible
    if not llm.base_url or not llm.api_key or not llm.model:
        raise LLMProviderError("LLM 服务尚未配置")

    url = f"{llm.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {llm.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body: dict[str, object] = {
        "model": llm.model,
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
    }
    poster = post_json or _post_json
    payload = poster(url, headers, body)
    content = _extract_chat_content(payload)
    return _parse_json_content(content)


def _post_json(url: str, headers: dict[str, str], body: dict[str, object]) -> object:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=LLM_READ_TIMEOUT_SECONDS,
        ) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if body.get("stream") is True and "text/event-stream" in content_type:
                return _read_streaming_chat_response(response)
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = _read_http_error(error)
        raise LLMProviderError(
            f"LLM 请求失败，HTTP {error.code}: {_sanitize_secret_text(detail)}"
        ) from error
    except TimeoutError as error:
        raise LLMProviderError(_timeout_message()) from error
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        message = _sanitize_secret_text(str(error))
        if "timed out" in message.lower():
            raise LLMProviderError(_timeout_message()) from error
        raise LLMProviderError(f"LLM 请求失败: {message}") from error


def _read_streaming_chat_response(response: _StreamingResponse) -> object:
    content_parts: list[str] = []
    while True:
        raw_line = response.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8").strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError as error:
            raise LLMProviderError("LLM 流式响应不是有效 JSON") from error
        content_piece = _stream_event_content(event)
        if content_piece:
            content_parts.append(content_piece)

    content = "".join(content_parts).strip()
    if not content:
        raise LLMProviderError("LLM 流式响应缺少 message content")
    return {"choices": [{"message": {"content": content}}]}


def _stream_event_content(event: object) -> str | None:
    if not isinstance(event, dict):
        return None
    error = event.get("error")
    if error:
        raise LLMProviderError(f"LLM 流式响应返回错误: {_event_error_message(error)}")
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None
    delta = choice.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    text = choice.get("text")
    if isinstance(text, str):
        return text
    return None


def _event_error_message(error: object) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return _sanitize_secret_text(message)
    return _sanitize_secret_text(str(error))


def _extract_chat_content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise LLMProviderError("LLM 响应必须是 JSON 对象")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError("LLM 响应缺少 choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LLMProviderError("LLM 响应的 choice 必须是对象")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LLMProviderError("LLM 响应的 choice 缺少 message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMProviderError("LLM 响应的 message content 为空")
    return content


def _parse_json_content(content: str) -> dict[str, object]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise LLMProviderError("LLM 响应内容不是有效 JSON") from error
    if not isinstance(parsed, dict):
        raise LLMProviderError("LLM 响应 JSON 必须是对象")
    return cast(dict[str, object], parsed)


def _read_http_error(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8")
    except OSError:
        return str(error)


def _timeout_message() -> str:
    return (
        f"LLM 请求超过 {LLM_READ_TIMEOUT_SECONDS} 秒仍未返回。"
        "当前已使用流式请求；请换用更快模型、减少上下文或稍后重试。"
    )


def _sanitize_secret_text(text: str) -> str:
    sanitized = text
    for marker in ("api_key", "apikey", "token", "Authorization", "Bearer"):
        sanitized = sanitized.replace(marker, "***")
    return sanitized
