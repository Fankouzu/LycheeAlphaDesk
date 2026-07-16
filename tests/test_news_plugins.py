from dataclasses import dataclass

import pytest

from lychee_alphadesk.providers.demo import NewsEvent


@dataclass(frozen=True)
class FakeEntryPoint:
    name: str
    loaded: object

    def load(self) -> object:
        return self.loaded


def test_discovery_loads_only_valid_installed_news_provider_entry_points(
    monkeypatch,
) -> None:
    from lychee_alphadesk.providers import news_plugins

    class AuditedEntityNews:
        metadata = news_plugins.NewsProviderMetadata(
            provider_id="audited_entity",
            display_name="审计实体新闻",
            description="按证券实体返回可审计新闻。",
            capabilities=frozenset({"entity_news"}),
            settings=(
                news_plugins.NewsProviderSetting(
                    key="api_key",
                    label="API Key",
                    description="用于访问已授权新闻源。",
                ),
            ),
        )

        def pull_news(
            self, request: news_plugins.NewsProviderRequest
        ) -> list[NewsEvent]:
            return []

    monkeypatch.setattr(
        news_plugins,
        "entry_points",
        lambda *, group: [
            FakeEntryPoint("audited", AuditedEntityNews),
            FakeEntryPoint("broken", object()),
        ]
        if group == "lychee_alphadesk.news_providers"
        else [],
    )

    registry = news_plugins.discover_news_provider_plugins()

    assert registry.providers["audited_entity"].metadata.display_name == "审计实体新闻"
    assert len(registry.diagnostics) == 1
    assert "broken" in registry.diagnostics[0]


def test_plugin_required_settings_control_auto_eligibility() -> None:
    from lychee_alphadesk.providers import news_plugins

    plugin = type(
        "ConfiguredPlugin",
        (),
        {
            "metadata": news_plugins.NewsProviderMetadata(
                provider_id="issuer_wire",
                display_name="公司公告新闻",
                description="公司实体新闻。",
                capabilities=frozenset({"entity_news"}),
                settings=(
                    news_plugins.NewsProviderSetting(
                        key="token",
                        label="访问令牌",
                        description="插件访问令牌。",
                    ),
                ),
            ),
            "pull_news": lambda self, request: [],
        },
    )()

    assert news_plugins.missing_required_settings(plugin, {}) == ("token",)
    assert news_plugins.missing_required_settings(plugin, {"token": "configured"}) == ()


def test_plugin_results_must_preserve_auditable_news_event_fields() -> None:
    from lychee_alphadesk.providers import news_plugins

    plugin = type(
        "BadRowsPlugin",
        (),
        {
            "metadata": news_plugins.NewsProviderMetadata(
                provider_id="bad_rows",
                display_name="错误新闻插件",
                description="用于验证边界。",
                capabilities=frozenset({"entity_news"}),
            ),
            "pull_news": lambda self, request: [object()],
        },
    )()

    with pytest.raises(ValueError, match="NewsEvent"):
        news_plugins.pull_plugin_news(
            plugin,
            news_plugins.NewsProviderRequest(
                symbols=("AAPL",),
                query=None,
                start_date="2026-07-01",
                end_date="2026-07-02",
                settings={},
            ),
        )
