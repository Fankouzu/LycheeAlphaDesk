import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import cast

from lychee_alphadesk.core.config import AlphaDeskConfig

ChatMessage = dict[str, str]
JsonPoster = Callable[[str, dict[str, str], dict[str, object]], object]


class LLMProviderError(RuntimeError):
    pass


def request_chat_json(
    config: AlphaDeskConfig,
    *,
    messages: list[ChatMessage],
    post_json: JsonPoster | None = None,
) -> dict[str, object]:
    llm = config.llm.openai_compatible
    if not llm.base_url or not llm.api_key or not llm.model:
        raise LLMProviderError("LLM provider is not configured")

    url = f"{llm.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {llm.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body: dict[str, object] = {
        "model": llm.model,
        "messages": messages,
        "temperature": 0.2,
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
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = _read_http_error(error)
        raise LLMProviderError(
            f"LLM request failed with HTTP {error.code}: {_sanitize_secret_text(detail)}"
        ) from error
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        message = _sanitize_secret_text(str(error))
        raise LLMProviderError(f"LLM request failed: {message}") from error


def _extract_chat_content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise LLMProviderError("LLM response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError("LLM response does not contain choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LLMProviderError("LLM response choice must be an object")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LLMProviderError("LLM response choice does not contain a message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMProviderError("LLM response message content is empty")
    return content


def _parse_json_content(content: str) -> dict[str, object]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise LLMProviderError("LLM response content is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise LLMProviderError("LLM response JSON must be an object")
    return cast(dict[str, object], parsed)


def _read_http_error(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8")
    except OSError:
        return str(error)


def _sanitize_secret_text(text: str) -> str:
    sanitized = text
    for marker in ("api_key", "apikey", "token", "Authorization", "Bearer"):
        sanitized = sanitized.replace(marker, "***")
    return sanitized
