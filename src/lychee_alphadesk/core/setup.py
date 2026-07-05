import json
import urllib.error
import urllib.request

from lychee_alphadesk.core.config import AlphaDeskConfig, ProviderSetupInfo


def providers_requiring_values(config: AlphaDeskConfig) -> list[ProviderSetupInfo]:
    return [
        provider
        for provider in sorted(
            config.providers.values(), key=lambda item: (item.priority, item.provider_id)
        )
        if provider.requires_value and provider.show_in_wizard
    ]


def data_provider_status(config: AlphaDeskConfig) -> str:
    providers = providers_requiring_values(config)
    configured_count = sum(
        1 for provider in providers if provider.value and provider.value.strip()
    )
    return f"已配置 {configured_count}/{len(providers)}"


def llm_provider_status(config: AlphaDeskConfig) -> str:
    provider = config.llm.openai_compatible
    if provider.base_url and provider.api_key and provider.model:
        return f"已配置: {provider.model}"
    if provider.base_url or provider.api_key or provider.model:
        return "部分配置"
    return "未配置"


def provider_config_status(provider: ProviderSetupInfo) -> str:
    if provider.value and provider.value.strip():
        return f"已配置: {mask_config_value(provider.value.strip())}"
    return "未配置"


def provider_menu_label(provider: ProviderSetupInfo) -> str:
    return f"{provider.name:<30} {provider_config_status(provider)}"


def mask_config_value(value: str) -> str:
    if len(value) <= 1:
        return "***"
    if len(value) <= 4:
        return f"{value[0]}***{value[-1]}"
    return f"{value[:4]}***{value[-4:]}"


def provider_registration_summary(provider: ProviderSetupInfo) -> str:
    if provider.config_field == "user_agent":
        return "无需 API Key；Lychee AlphaDesk 会在内部处理请求标识。"
    return provider.registration


def provider_notes(provider: ProviderSetupInfo) -> str:
    if provider.config_field == "user_agent":
        return "用于合规访问 SEC；普通用户无需配置。"
    return provider.notes


def provider_value_prompt(provider: ProviderSetupInfo) -> str:
    if provider.config_field == "api_key":
        return f"粘贴 {provider.name} API Key"
    if provider.config_field == "token":
        return f"粘贴 {provider.name} Token"
    return f"粘贴 {provider.name} 配置值"


def provider_detail_text(provider: ProviderSetupInfo) -> str:
    lines = [
        provider.name,
        f"用途: {provider.domain}",
        f"申请方式: {provider_registration_summary(provider)}",
        f"申请地址: {provider.registration_url}",
        f"当前状态: {provider_config_status(provider)}",
    ]
    notes = provider_notes(provider)
    if notes:
        lines.append(f"说明: {notes}")
    return "\n".join(lines)


def fetch_openai_compatible_models(base_url: str, api_key: str) -> list[str]:
    endpoint = f"{base_url.rstrip('/')}/models"
    request = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []
    return parse_openai_compatible_models(payload)


def parse_openai_compatible_models(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    models: list[str] = []
    seen: set[str] = set()
    for item in data:
        model_id: str | None = None
        if isinstance(item, dict):
            value = item.get("id")
            if isinstance(value, str):
                model_id = value.strip()
        elif isinstance(item, str):
            model_id = item.strip()

        if model_id and model_id not in seen:
            models.append(model_id)
            seen.add(model_id)
    return models
