# 新闻 Provider 插件 API

这个实验性 API 允许另行安装的 Python 发行包提供可审计的实体、主题或市场级
新闻。插件不会得到 AlphaDesk 配置文件路径，配置值也不能用来导入或执行代码。

## 注册已安装插件

在包入口点组中暴露一个零参数工厂、类或插件实例：

```toml
[project.entry-points."lychee_alphadesk.news_providers"]
issuer_news = "example_issuer_news:provider"
```

AlphaDesk 只发现当前 Python 环境中已经安装的包，绝不会从 `config.yaml` 的路径
或 URL 下载、加载代码。

## 实现契约

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
        display_name="发行人新闻",
        description="可核验的发行人公告与公司新闻。",
        capabilities=frozenset({"entity_news"}),
        settings=(
            NewsProviderSetting(
                key="api_key",
                label="API Key",
                description="授权数据源提供的访问凭据。",
            ),
        ),
    )

    def pull_news(self, request: NewsProviderRequest) -> list[NewsEvent]:
        # 使用 request.symbols、request.query、日期范围和 request.settings。
        return []


provider = IssuerNewsProvider()
```

`provider_id` 必须稳定且全局唯一；`display_name`、设置标签和说明面向普通用户。
支持的能力如下：

- `entity_news`：一个或多个证券代码或发行人实体。
- `topic_news`：关键词或研究主题请求。
- `market_news`：不限定证券的市场级请求。

每条 `NewsEvent` 必须包含时间、标题、来源 URL；已知时还必须包含匹配证券代码。
AlphaDesk 会拒绝格式不完整的数据，不会把不可审计新闻写入本地缓存。

## 配置与使用

安装包后运行 `lychee setup`，选择“新闻插件”；键盘流程会展示显示名称、配置
状态和设置标签。自动化场景可单项写入：

```bash
lychee setup plugin set issuer_news api_key "YOUR_API_KEY"
lychee data pull news --symbols 0700.HK --provider issuer_news
```

使用 `--provider auto` 时，AlphaDesk 会优先考虑已启用、能力匹配且必填配置完整
的已安装插件，随后才尝试内置 provider。自动模式下插件失败会被脱敏并继续回退；
显式选择插件时不会静默换源。

## 边界

- 已安装的第三方插件属于受信任代码，安装前应审查其源码与许可证。
- 不得记录 API key，也不得把它写进 URL、标题或错误信息。
- 必须保留原始来源 URL 和时间，不能制造新闻、事实或市场结论。
- 本 API 只服务数据采集与审计，不能下单或输出投资操作指令。
