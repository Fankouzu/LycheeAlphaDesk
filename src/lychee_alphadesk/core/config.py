import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

ConfigField = Literal["api_key", "token", "user_agent", "none"]


class ProviderSetupInfo(BaseModel):
    provider_id: str
    name: str
    domain: str
    registration: str
    registration_url: str
    config_field: ConfigField
    priority: int
    notes: str
    show_in_wizard: bool = True
    value: str | None = None

    @property
    def requires_value(self) -> bool:
        return self.config_field != "none"


class AlphaDeskConfig(BaseModel):
    version: int = 1
    providers: dict[str, ProviderSetupInfo]


PROVIDER_SETUP_REGISTRY: tuple[ProviderSetupInfo, ...] = (
    ProviderSetupInfo(
        provider_id="yfinance",
        name="yfinance",
        domain="US/HK/global daily prices",
        registration="No formal signup",
        registration_url="https://github.com/ranaroussi/yfinance",
        config_field="none",
        priority=1,
        notes="Unofficial Yahoo Finance access; useful for local research demos.",
    ),
    ProviderSetupInfo(
        provider_id="akshare",
        name="AkShare",
        domain="China A-shares, HK/US data, macro datasets",
        registration="Usually no API key",
        registration_url="https://github.com/akfamily/akshare",
        config_field="none",
        priority=1,
        notes="Best first open-source option for China-market coverage.",
    ),
    ProviderSetupInfo(
        provider_id="gdelt",
        name="GDELT",
        domain="Global news and events",
        registration="No API key",
        registration_url="https://www.gdeltproject.org/data.html",
        config_field="none",
        priority=1,
        notes="Open global news source; needs ticker/entity mapping downstream.",
    ),
    ProviderSetupInfo(
        provider_id="sec_edgar",
        name="SEC EDGAR",
        domain="US filings and XBRL facts",
        registration="No API key",
        registration_url=(
            "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
        ),
        config_field="none",
        priority=1,
        notes="No user setup is required for local research use; follow SEC fair-access guidance.",
    ),
    ProviderSetupInfo(
        provider_id="hkma",
        name="HKMA Open API",
        domain="Hong Kong macro and financial statistics",
        registration="No registration",
        registration_url="https://apidocs.hkma.gov.hk/",
        config_field="none",
        priority=1,
        notes="Useful for HK rates and macro context.",
    ),
    ProviderSetupInfo(
        provider_id="tushare",
        name="Tushare Pro",
        domain="China A-share prices, fundamentals, calendars",
        registration="Account + token",
        registration_url="https://tushare.pro/document/1?doc_id=39",
        config_field="token",
        priority=2,
        notes="Some datasets may require points or permissions.",
    ),
    ProviderSetupInfo(
        provider_id="alpha_vantage",
        name="Alpha Vantage",
        domain="Global prices, fundamentals, indicators, macro",
        registration="Free API key",
        registration_url="https://www.alphavantage.co/support/#api-key",
        config_field="api_key",
        priority=2,
        notes="Beginner-friendly API; free tier is rate-limited.",
    ),
    ProviderSetupInfo(
        provider_id="finnhub",
        name="Finnhub",
        domain="Market data, fundamentals, filings, news",
        registration="Free API key",
        registration_url="https://finnhub.io/register",
        config_field="api_key",
        priority=2,
        notes="Useful for ticker-linked market news and company data.",
    ),
    ProviderSetupInfo(
        provider_id="fmp",
        name="Financial Modeling Prep",
        domain="Prices, fundamentals, statements, press releases",
        registration="API key",
        registration_url="https://site.financialmodelingprep.com/register",
        config_field="api_key",
        priority=2,
        notes="Check licensing before redistribution or commercial use.",
        show_in_wizard=False,
    ),
    ProviderSetupInfo(
        provider_id="fred",
        name="FRED",
        domain="US macro data",
        registration="Free API key",
        registration_url="https://fred.stlouisfed.org/docs/api/fred/",
        config_field="api_key",
        priority=2,
        notes="Best first US macro provider.",
    ),
    ProviderSetupInfo(
        provider_id="marketaux",
        name="Marketaux",
        domain="Financial news and sentiment",
        registration="Free API key",
        registration_url="https://www.marketaux.com/documentation",
        config_field="api_key",
        priority=2,
        notes="Useful if GDELT ticker matching is too noisy.",
    ),
    ProviderSetupInfo(
        provider_id="newsapi",
        name="NewsAPI",
        domain="General news",
        registration="Free development API key",
        registration_url="https://newsapi.org/docs",
        config_field="api_key",
        priority=2,
        notes="Check plan limits and commercial-use restrictions.",
    ),
)


def config_dir() -> Path:
    xdg_config_home = Path.home() / ".config"

    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        xdg_config_home = Path(configured).expanduser()
    return xdg_config_home / "lychee-alphadesk"


def config_file_path() -> Path:
    return config_dir() / "config.yaml"


def default_config() -> AlphaDeskConfig:
    return AlphaDeskConfig(
        providers={info.provider_id: info.model_copy() for info in PROVIDER_SETUP_REGISTRY}
    )


def ensure_config_file(path: Path | None = None) -> Path:
    target = path or config_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    if not target.exists():
        save_config(default_config(), target)
    else:
        target.chmod(0o600)
    return target


def load_config(path: Path | None = None) -> AlphaDeskConfig:
    target = path or config_file_path()
    if not target.exists():
        ensure_config_file(target)
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return normalize_config(AlphaDeskConfig.model_validate(raw))


def normalize_config(config: AlphaDeskConfig) -> AlphaDeskConfig:
    normalized = default_config()
    for provider_id, existing_provider in config.providers.items():
        if provider_id not in normalized.providers:
            normalized.providers[provider_id] = existing_provider
            continue

        provider = normalized.providers[provider_id]
        if provider.requires_value and existing_provider.value:
            provider.value = existing_provider.value
        normalized.providers[provider_id] = provider
    return normalized


def save_config(config: AlphaDeskConfig, path: Path | None = None) -> Path:
    target = path or config_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config.model_dump(), sort_keys=False), encoding="utf-8")
    target.chmod(0o600)
    return target


def set_provider_value(provider_id: str, value: str, path: Path | None = None) -> Path:
    target = path or config_file_path()
    config = load_config(target)
    if provider_id not in config.providers:
        known = ", ".join(sorted(config.providers))
        raise KeyError(f"Unknown provider '{provider_id}'. Known providers: {known}")
    provider = config.providers[provider_id]
    if not provider.requires_value:
        raise ValueError(f"Provider '{provider_id}' does not require an API key or token")
    provider.value = value
    config.providers[provider_id] = provider
    return save_config(config, target)
