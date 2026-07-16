import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

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


class OpenAICompatibleLLMConfig(BaseModel):
    name: str = "OpenAI 兼容自定义端点"
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class LLMSettings(BaseModel):
    openai_compatible: OpenAICompatibleLLMConfig = Field(
        default_factory=OpenAICompatibleLLMConfig
    )


class NewsProviderPluginConfig(BaseModel):
    enabled: bool = True
    settings: dict[str, str] = Field(default_factory=dict)


class AlphaDeskConfig(BaseModel):
    version: int = 1
    providers: dict[str, ProviderSetupInfo]
    provider_plugins: dict[str, NewsProviderPluginConfig] = Field(default_factory=dict)
    llm: LLMSettings = Field(default_factory=LLMSettings)


PROVIDER_SETUP_REGISTRY: tuple[ProviderSetupInfo, ...] = (
    ProviderSetupInfo(
        provider_id="yfinance",
        name="yfinance",
        domain="美股、港股和全球日线行情",
        registration="无需正式申请",
        registration_url="https://github.com/ranaroussi/yfinance",
        config_field="none",
        priority=1,
        notes="非官方 Yahoo Finance 访问方式，适合本地研究和演示。",
    ),
    ProviderSetupInfo(
        provider_id="akshare",
        name="AkShare",
        domain="A 股、港股、美股和宏观数据",
        registration="通常无需 API Key",
        registration_url="https://github.com/akfamily/akshare",
        config_field="none",
        priority=1,
        notes="覆盖中国市场的优先开源选择。",
    ),
    ProviderSetupInfo(
        provider_id="gdelt",
        name="GDELT",
        domain="全球新闻和事件数据",
        registration="无需 API Key",
        registration_url="https://www.gdeltproject.org/data.html",
        config_field="none",
        priority=1,
        notes="开放全球新闻源，后续需要做证券代码和实体映射。",
    ),
    ProviderSetupInfo(
        provider_id="sec_edgar",
        name="SEC EDGAR",
        domain="美股公告和 XBRL 财务事实数据",
        registration="无需 API Key",
        registration_url=(
            "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
        ),
        config_field="none",
        priority=1,
        notes="本地研究无需用户配置；访问时需遵守 SEC fair-access 指引。",
    ),
    ProviderSetupInfo(
        provider_id="hkma",
        name="HKMA Open API",
        domain="香港宏观和金融统计数据",
        registration="无需注册",
        registration_url="https://apidocs.hkma.gov.hk/",
        config_field="none",
        priority=1,
        notes="适合补充香港利率和宏观环境。",
    ),
    ProviderSetupInfo(
        provider_id="tushare",
        name="Tushare Pro",
        domain="A 股行情、基本面和交易日历",
        registration="账号 + Token",
        registration_url="https://tushare.pro/document/1?doc_id=39",
        config_field="token",
        priority=2,
        notes="部分数据集可能需要积分或权限。",
    ),
    ProviderSetupInfo(
        provider_id="alpha_vantage",
        name="Alpha Vantage",
        domain="全球行情、基本面、技术指标和宏观数据",
        registration="免费 API Key",
        registration_url="https://www.alphavantage.co/support/#api-key",
        config_field="api_key",
        priority=2,
        notes="对新手友好；免费额度有频率限制。",
    ),
    ProviderSetupInfo(
        provider_id="finnhub",
        name="Finnhub",
        domain="行情、基本面、公告和新闻",
        registration="免费 API Key",
        registration_url="https://finnhub.io/register",
        config_field="api_key",
        priority=2,
        notes="适合获取证券代码关联的市场新闻和公司数据。",
    ),
    ProviderSetupInfo(
        provider_id="fmp",
        name="Financial Modeling Prep",
        domain="行情、基本面、财报和新闻稿",
        registration="API Key",
        registration_url="https://site.financialmodelingprep.com/register",
        config_field="api_key",
        priority=2,
        notes="重新分发或商业使用前请确认授权限制。",
        show_in_wizard=False,
    ),
    ProviderSetupInfo(
        provider_id="fred",
        name="FRED",
        domain="美国宏观数据",
        registration="免费 API Key",
        registration_url="https://fred.stlouisfed.org/docs/api/fred/",
        config_field="api_key",
        priority=2,
        notes="美国宏观数据的优先选择。",
    ),
    ProviderSetupInfo(
        provider_id="marketaux",
        name="Marketaux",
        domain="金融新闻和情绪数据",
        registration="免费 API Key",
        registration_url="https://www.marketaux.com/documentation",
        config_field="api_key",
        priority=2,
        notes="当 GDELT 的证券代码匹配噪声较大时可作为补充。",
    ),
    ProviderSetupInfo(
        provider_id="newsapi",
        name="NewsAPI",
        domain="通用新闻",
        registration="免费开发 API Key",
        registration_url="https://newsapi.org/docs",
        config_field="api_key",
        priority=2,
        notes="请确认套餐限制和商业使用限制。",
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
    normalized.llm = config.llm
    normalized.provider_plugins = config.provider_plugins
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
        raise KeyError(f"未知数据源 '{provider_id}'。可用数据源: {known}")
    provider = config.providers[provider_id]
    if not provider.requires_value:
        raise ValueError(f"数据源 '{provider_id}' 不需要配置 API Key 或 Token")
    provider.value = value
    config.providers[provider_id] = provider
    return save_config(config, target)


def set_news_provider_plugin_value(
    provider_id: str,
    setting_key: str,
    value: str,
    path: Path | None = None,
) -> Path:
    normalized_provider_id = provider_id.strip()
    normalized_setting_key = setting_key.strip()
    normalized_value = value.strip()
    if not normalized_provider_id:
        raise ValueError("新闻插件 ID 不能为空")
    if not normalized_setting_key:
        raise ValueError("新闻插件设置项不能为空")
    if not normalized_value:
        raise ValueError("新闻插件设置值不能为空")

    target = path or config_file_path()
    config = load_config(target)
    plugin_config = config.provider_plugins.get(
        normalized_provider_id,
        NewsProviderPluginConfig(),
    )
    plugin_config.settings[normalized_setting_key] = normalized_value
    config.provider_plugins[normalized_provider_id] = plugin_config
    return save_config(config, target)


def set_openai_compatible_llm(
    base_url: str,
    api_key: str,
    model: str | None = None,
    path: Path | None = None,
) -> Path:
    cleaned_base_url = base_url.strip()
    cleaned_api_key = api_key.strip()
    cleaned_model = model.strip() if model else None
    if not cleaned_base_url:
        raise ValueError("Base URL 不能为空")
    if not cleaned_base_url.startswith(("http://", "https://")):
        raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
    if not cleaned_api_key:
        raise ValueError("API Key 不能为空")

    target = path or config_file_path()
    config = load_config(target)
    config.llm.openai_compatible.base_url = cleaned_base_url
    config.llm.openai_compatible.api_key = cleaned_api_key
    config.llm.openai_compatible.model = cleaned_model
    return save_config(config, target)
