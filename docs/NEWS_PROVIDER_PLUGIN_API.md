# News Provider Plugin API

This experimental API lets a separately installed Python distribution provide
auditable entity, topic, or market news. It does not grant a plugin access to
the AlphaDesk configuration file or let a configuration value import code.

## Register an installed provider

Expose a zero-argument factory, class, or plugin instance through the package
entry-point group:

```toml
[project.entry-points."lychee_alphadesk.news_providers"]
issuer_news = "example_issuer_news:provider"
```

AlphaDesk discovers only packages already installed in the active Python
environment. It never resolves code from a path or URL in `config.yaml`.

## Implement the contract

```python
from lychee_alphadesk.providers.demo import NewsEvent
from lychee_alphadesk.providers.news_plugins import (
    NewsProviderMetadata,
    NewsProviderRequest,
    NewsProviderSetting,
)


class IssuerNewsProvider:
    metadata = NewsProviderMetadata(
        provider_id="issuer_news",
        display_name="Issuer News",
        description="Audited issuer announcements and company news.",
        capabilities=frozenset({"entity_news"}),
        settings=(
            NewsProviderSetting(
                key="api_key",
                label="API Key",
                description="API credential from the licensed provider.",
            ),
        ),
    )

    def pull_news(self, request: NewsProviderRequest) -> list[NewsEvent]:
        # Use request.symbols, request.query, request.start_date, request.end_date,
        # and the provider's declared values in request.settings.
        return []


provider = IssuerNewsProvider()
```

`provider_id` must be stable and unique. `display_name`, labels, and
descriptions are human-facing. Supported capabilities are:

- `entity_news`: one or more securities or issuer entities.
- `topic_news`: a keyword or research-theme request.
- `market_news`: an unscoped market-level request.

Each returned `NewsEvent` must include a timestamp, headline, source URL, and
the matching symbols when known. AlphaDesk rejects malformed rows rather than
placing unauditable data in the local cache.

## Configure and use

After installing the package, open `lychee setup` and select `新闻插件`; the
keyboard flow shows the display name, configuration status, and setting labels.
Automation can write one value without opening a menu:

```bash
lychee setup plugin set issuer_news api_key "YOUR_API_KEY"
lychee data pull news --symbols 0700.HK --provider issuer_news
```

With `--provider auto`, AlphaDesk first considers enabled installed plugins
whose capability and required settings match the request. It then tries the
built-in providers. If a plugin fails in `auto`, the diagnostic is sanitized
and the fallback chain continues. An explicitly selected plugin does not
silently change source.

## Boundaries

- Treat an installed third-party plugin as trusted code. Review its source and
  license before installation.
- Do not log API keys or embed them in returned URLs, headlines, or errors.
- Preserve original source URLs and timestamps. Do not manufacture articles,
  claims, or market conclusions.
- This API is for data collection and auditability. It must not place trades or
  emit investment instructions.
