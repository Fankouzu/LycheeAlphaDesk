from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Literal, Protocol, cast

from lychee_alphadesk.providers.demo import NewsEvent

NEWS_PROVIDER_ENTRY_POINT_GROUP = "lychee_alphadesk.news_providers"
NewsProviderCapability = Literal["entity_news", "topic_news", "market_news"]
_KNOWN_CAPABILITIES = frozenset({"entity_news", "topic_news", "market_news"})


@dataclass(frozen=True)
class NewsProviderSetting:
    key: str
    label: str
    description: str
    secret: bool = True
    required: bool = True


@dataclass(frozen=True)
class NewsProviderMetadata:
    provider_id: str
    display_name: str
    description: str
    capabilities: frozenset[NewsProviderCapability]
    markets: tuple[str, ...] = ()
    priority: int = 10
    settings: tuple[NewsProviderSetting, ...] = ()


@dataclass(frozen=True)
class NewsProviderRequest:
    symbols: tuple[str, ...]
    query: str | None
    start_date: str
    end_date: str
    settings: dict[str, str]


class NewsProviderPlugin(Protocol):
    metadata: NewsProviderMetadata

    def pull_news(self, request: NewsProviderRequest) -> Sequence[NewsEvent]: ...


@dataclass(frozen=True)
class NewsProviderRegistry:
    providers: dict[str, NewsProviderPlugin]
    diagnostics: tuple[str, ...]


def discover_news_provider_plugins() -> NewsProviderRegistry:
    providers: dict[str, NewsProviderPlugin] = {}
    diagnostics: list[str] = []
    for entry_point in entry_points(group=NEWS_PROVIDER_ENTRY_POINT_GROUP):
        try:
            plugin = _load_news_provider(entry_point.load())
            _validate_news_provider(plugin)
        except (TypeError, ValueError) as error:
            diagnostics.append(f"新闻插件入口 '{entry_point.name}' 无效: {error}")
            continue
        except Exception as error:  # pragma: no cover - third-party plugin boundary
            diagnostics.append(f"新闻插件入口 '{entry_point.name}' 加载失败: {error}")
            continue

        provider_id = plugin.metadata.provider_id
        if provider_id in providers:
            diagnostics.append(
                f"新闻插件 '{provider_id}' 重复注册，已忽略入口 '{entry_point.name}'"
            )
            continue
        providers[provider_id] = plugin
    return NewsProviderRegistry(providers=providers, diagnostics=tuple(diagnostics))


def missing_required_settings(
    plugin: NewsProviderPlugin,
    settings: dict[str, str],
) -> tuple[str, ...]:
    return tuple(
        setting.key
        for setting in plugin.metadata.settings
        if setting.required and not settings.get(setting.key, "").strip()
    )


def pull_plugin_news(
    plugin: NewsProviderPlugin,
    request: NewsProviderRequest,
) -> list[NewsEvent]:
    rows = plugin.pull_news(request)
    if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
        raise ValueError("新闻插件必须返回 NewsEvent 列表。")
    normalized_rows = list(rows)
    for row in normalized_rows:
        if not isinstance(row, NewsEvent):
            raise ValueError("新闻插件返回的每一项都必须是 NewsEvent。")
        if not row.timestamp.strip() or not row.headline.strip() or not row.source_url.strip():
            raise ValueError("新闻插件返回的 NewsEvent 必须包含时间、标题和来源 URL。")
    return normalized_rows


def _load_news_provider(loaded: object) -> NewsProviderPlugin:
    candidate: object = loaded
    if isinstance(loaded, type):
        candidate = loaded()
    elif callable(loaded) and not _has_provider_shape(loaded):
        candidate = cast(Callable[[], object], loaded)()
    if not _has_provider_shape(candidate):
        raise TypeError("入口必须提供带 metadata 和 pull_news 的插件实例或零参数工厂。")
    return cast(NewsProviderPlugin, candidate)


def _has_provider_shape(candidate: object) -> bool:
    return hasattr(candidate, "metadata") and callable(getattr(candidate, "pull_news", None))


def _validate_news_provider(plugin: NewsProviderPlugin) -> None:
    metadata = plugin.metadata
    if not isinstance(metadata, NewsProviderMetadata):
        raise TypeError("metadata 必须是 NewsProviderMetadata。")
    if not metadata.provider_id.strip():
        raise ValueError("provider_id 不能为空。")
    if not metadata.display_name.strip():
        raise ValueError("display_name 不能为空。")
    if not metadata.capabilities:
        raise ValueError("至少需要声明一项新闻能力。")
    unknown = metadata.capabilities - _KNOWN_CAPABILITIES
    if unknown:
        raise ValueError(f"包含未知能力: {', '.join(sorted(unknown))}")
    setting_keys = [setting.key for setting in metadata.settings]
    if any(not key.strip() for key in setting_keys):
        raise ValueError("设置项 key 不能为空。")
    if len(setting_keys) != len(set(setting_keys)):
        raise ValueError("设置项 key 不能重复。")
