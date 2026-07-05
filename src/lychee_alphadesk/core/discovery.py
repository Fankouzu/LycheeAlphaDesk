import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.config import AlphaDeskConfig, load_config
from lychee_alphadesk.core.live_data import build_cached_data_snapshot, pull_news_events
from lychee_alphadesk.core.llm import JsonPoster, request_chat_json

DISCOVERY_CACHE_FILENAME = "discovery-today.json"
DEFAULT_MARKETS = ["US", "HK", "CN"]
LLM_REQUIRED_MESSAGE = (
    "LLM 尚未配置。请先运行 "
    "`lychee setup llm set <base_url> <api_key> MODEL_NAME` 后再使用今日市场发现。"
)


class DiscoveryLLMRequiredError(RuntimeError):
    pass


class DiscoveryDataRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoverySource:
    provider: str
    market: str
    description: str


@dataclass(frozen=True)
class DiscoveryTheme:
    name: str
    markets: list[str]
    summary: str
    evidence: list[str]
    sectors: list[str]
    risk_flags: list[str]
    confidence: str


@dataclass(frozen=True)
class DiscoveryCandidate:
    display_name: str
    symbol: str | None
    market: str
    asset_type: str
    related_theme: str
    why_watch: str
    evidence: list[str]
    risk_flags: list[str]
    next_actions: list[str]
    confidence: str
    recommendation: str = "research"


@dataclass(frozen=True)
class DiscoveryReport:
    mode: str
    created_at: str
    markets: list[str]
    sources: list[DiscoverySource]
    themes: list[DiscoveryTheme]
    candidates: list[DiscoveryCandidate]
    warnings: list[str]
    next_actions: list[str]
    disclaimer: str


def parse_markets(value: str) -> list[str]:
    markets = [item.strip().upper() for item in value.split(",") if item.strip()]
    aliases = {"USA": "US", "HKEX": "HK", "HONGKONG": "HK", "CHINA": "CN"}
    normalized = [aliases.get(market, market) for market in markets]
    allowed = {"US", "HK", "CN"}
    invalid = sorted(set(normalized).difference(allowed))
    if invalid:
        raise ValueError(f"不支持的发现市场: {', '.join(invalid)}")
    return normalized or DEFAULT_MARKETS.copy()


def build_today_discovery_report(
    markets: list[str] | None = None,
    config: AlphaDeskConfig | None = None,
    output_dir: Path | None = None,
    post_json: JsonPoster | None = None,
    force_refresh: bool = False,
) -> DiscoveryReport:
    active_config = config or load_config()
    require_active_llm(active_config)
    selected_markets = markets or DEFAULT_MARKETS.copy()
    _prepare_discovery_inputs(
        config=active_config,
        output_dir=output_dir,
        force_refresh=force_refresh,
    )
    context = _build_llm_context(selected_markets, output_dir)
    payload = request_chat_json(
        active_config,
        messages=_build_discovery_messages(context),
        post_json=post_json,
    )
    themes = _parse_themes(payload)
    candidates = _parse_candidates(payload)
    warnings = _optional_string_list(payload, "warnings")
    warnings.append("候选仅用于研究和观察，不是买入或卖出建议。")
    return DiscoveryReport(
        mode="llm-synthesized",
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        markets=selected_markets,
        sources=[
            DiscoverySource(
                provider="openai-compatible-llm",
                market=market,
                description=(
                    "模型基于起始语境和本地实时数据缓存生成今日市场发现。"
                ),
            )
            for market in selected_markets
        ],
        themes=themes,
        candidates=candidates,
        warnings=warnings,
        next_actions=_required_string_list(payload, "next_actions"),
        disclaimer="非投资建议。请把这份报告用于决定下一步研究什么。",
    )


def require_active_llm(config: AlphaDeskConfig) -> None:
    llm = config.llm.openai_compatible
    if llm.base_url and llm.api_key and llm.model:
        return
    raise DiscoveryLLMRequiredError(LLM_REQUIRED_MESSAGE)


def _prepare_discovery_inputs(
    *,
    config: AlphaDeskConfig,
    output_dir: Path | None,
    force_refresh: bool,
) -> None:
    if output_dir is None:
        return
    try:
        pull_news_events(
            symbols=[],
            config=config,
            output_dir=output_dir,
            provider_id="auto",
            force=force_refresh,
        )
    except (RuntimeError, ValueError) as error:
        raise DiscoveryDataRequiredError(
            f"市场级新闻准备失败: {error}"
        ) from error


def write_discovery_report(report: DiscoveryReport, output_dir: Path) -> Path:
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / DISCOVERY_CACHE_FILENAME
    output_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def discovery_report_summary(report: DiscoveryReport, output_path: Path | None = None) -> str:
    lines = ["今日市场发现", f"模式: {report.mode}"]
    lines.append(f"市场: {', '.join(report.markets)}")
    if output_path is not None:
        lines.append(f"缓存: {output_path}")
    lines.append("")
    lines.append(report.disclaimer)
    lines.append("")
    lines.append("主题")
    for theme in report.themes[:3]:
        lines.append(f"- {theme.name}: {theme.summary}")
    lines.append("")
    lines.append("关注候选")
    for candidate in report.candidates[:5]:
        symbol = f" ({candidate.symbol})" if candidate.symbol else ""
        lines.append(
            f"- {candidate.display_name}{symbol} [{candidate.market}]: "
            f"{candidate.why_watch}"
        )
    if report.warnings:
        lines.append("")
        lines.append("风险提示")
        for warning in report.warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _build_llm_context(markets: list[str], output_dir: Path | None) -> dict[str, object]:
    starter_themes = [
        asdict(theme)
        for theme in _starter_themes()
        if set(theme.markets).intersection(markets)
    ]
    starter_candidates = [
        asdict(candidate)
        for candidate in _starter_candidates()
        if candidate.market in markets
    ]
    context: dict[str, object] = {
        "markets": markets,
        "starter_themes": starter_themes,
        "starter_candidates": starter_candidates,
        "local_live_cache": {"available": False},
    }
    if output_dir is not None:
        snapshot = build_cached_data_snapshot(output_dir)
        context["local_live_cache"] = {
            "available": True,
            "counts": snapshot.counts,
            "providers": snapshot.provider_names,
            "prices": [asdict(price) for price in snapshot.prices[:20]],
            "news_events": [asdict(event) for event in snapshot.news_events[:30]],
            "filings": [asdict(filing) for filing in snapshot.filings[:20]],
        }
    return context


def _build_discovery_messages(context: dict[str, object]) -> list[dict[str, str]]:
    schema = {
        "themes": [
            {
                "name": "string",
                "markets": ["US"],
                "summary": "string",
                "evidence": ["string"],
                "sectors": ["string"],
                "risk_flags": ["string"],
                "confidence": "low|medium|high",
            }
        ],
        "candidates": [
            {
                "display_name": "string",
                "symbol": "string or null",
                "market": "US|HK|CN",
                "asset_type": "stock|ETF|sector|index|other",
                "related_theme": "string",
                "why_watch": "string",
                "evidence": ["string"],
                "risk_flags": ["string"],
                "next_actions": ["string"],
                "confidence": "low|medium|high",
                "recommendation": "research",
            }
        ],
        "warnings": ["string"],
        "next_actions": ["string"],
    }
    system_prompt = (
        "你是 Lychee AlphaDesk 的市场发现分析员。"
        "Return one valid JSON object only, with no markdown fences. "
        "All user-facing string values must be written in Simplified Chinese. "
        "Do not give buy/sell advice, target prices, or allocation advice. "
        "Use watch/research/drill-down language only. "
        "Return 最多 3 个主题 and 最多 5 个关注候选. "
        "Keep every string concise and evidence-focused. "
        "Every theme and candidate must cite evidence from the provided context."
    )
    user_prompt = (
        "请为股市初学者生成今日市场发现。\n\n"
        f"Required JSON schema example:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Context JSON:\n{json.dumps(context, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _parse_themes(payload: dict[str, object]) -> list[DiscoveryTheme]:
    themes = []
    for item in _required_dict_list(payload, "themes"):
        themes.append(
            DiscoveryTheme(
                name=_required_string(item, "name"),
                markets=_required_string_list(item, "markets"),
                summary=_required_string(item, "summary"),
                evidence=_required_string_list(item, "evidence"),
                sectors=_required_string_list(item, "sectors"),
                risk_flags=_required_string_list(item, "risk_flags"),
                confidence=_required_string(item, "confidence"),
            )
        )
    if not themes:
        raise DiscoveryLLMRequiredError("LLM 返回内容缺少主题列表")
    return themes


def _parse_candidates(payload: dict[str, object]) -> list[DiscoveryCandidate]:
    candidates = []
    for item in _required_dict_list(payload, "candidates"):
        recommendation = _required_string(item, "recommendation")
        _reject_advice_recommendation(recommendation)
        candidates.append(
            DiscoveryCandidate(
                display_name=_required_string(item, "display_name"),
                symbol=_optional_string(item, "symbol"),
                market=_required_string(item, "market"),
                asset_type=_required_string(item, "asset_type"),
                related_theme=_required_string(item, "related_theme"),
                why_watch=_required_string(item, "why_watch"),
                evidence=_required_string_list(item, "evidence"),
                risk_flags=_required_string_list(item, "risk_flags"),
                next_actions=_required_string_list(item, "next_actions"),
                confidence=_required_string(item, "confidence"),
                recommendation=recommendation,
            )
        )
    if not candidates:
        raise DiscoveryLLMRequiredError("LLM 返回内容缺少关注候选列表")
    return candidates


def _required_dict_list(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise DiscoveryLLMRequiredError(f"LLM 返回内容缺少列表字段: {key}")
    items: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise DiscoveryLLMRequiredError(
                f"LLM 返回字段 {key} 必须包含对象"
            )
        items.append(dict(item))
    return items


def _required_string(payload: dict[str, object], key: str) -> str:
    raw = payload.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise DiscoveryLLMRequiredError(f"LLM 返回内容缺少文本字段: {key}")
    return raw.strip()


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise DiscoveryLLMRequiredError(
            f"LLM 返回字段 {key} 必须是文本或 null"
        )
    return raw.strip() or None


def _required_string_list(payload: dict[str, object], key: str) -> list[str]:
    values = _optional_string_list(payload, key)
    if not values:
        raise DiscoveryLLMRequiredError(f"LLM 返回内容缺少文本列表字段: {key}")
    return values


def _optional_string_list(payload: dict[str, object], key: str) -> list[str]:
    raw = payload.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DiscoveryLLMRequiredError(f"LLM 返回字段 {key} 必须是列表")
    values = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise DiscoveryLLMRequiredError(
                f"LLM 返回字段 {key} 必须只包含文本"
            )
        values.append(item.strip())
    return values


def _reject_advice_recommendation(value: str) -> None:
    normalized = value.strip().lower()
    if normalized in {"buy", "sell", "hold", "strong_buy", "strong_sell"}:
        raise DiscoveryLLMRequiredError(
            "LLM 返回内容使用了买入、卖出或持有等投资建议语言"
        )


def _starter_themes() -> list[DiscoveryTheme]:
    return [
        DiscoveryTheme(
            name="AI 基础设施观察",
            markets=["US", "HK", "CN"],
            summary=(
                "先观察算力、半导体、云服务和数据中心供应链，再决定是否研究个股。"
            ),
            evidence=[
                "起始观察池包含美国大型 AI 基础设施公司。",
                "港股和 A 股常通过平台公司、硬件公司和设备供应链体现同一主题。",
            ],
            sectors=["半导体", "云基础设施", "数据中心"],
            risk_flags=[
                "估值对预期变化敏感",
                "出口管制和供应链政策风险",
            ],
            confidence="medium",
        ),
        DiscoveryTheme(
            name="中国政策与消费观察",
            markets=["HK", "CN"],
            summary=(
                "观察港股中国公司和 A 股行业板块中的政策关联行业与消费需求信号。"
            ),
            evidence=[
                "政策、汇率或消费数据变化时，港股和 A 股可能出现联动反应。",
            ],
            sectors=["消费", "互联网平台", "金融"],
            risk_flags=[
                "政策消息可能快速反转",
                "汇率和流动性环境会影响定价",
            ],
            confidence="medium",
        ),
        DiscoveryTheme(
            name="利率与大盘风险观察",
            markets=["US", "HK"],
            summary=(
                "先用大盘指数和利率敏感行业建立背景，再下钻单一个股风险。"
            ),
            evidence=[
                "美国利率预期会影响权益资产久期风险。",
                "港币联系汇率相关金融条件会影响香港市场流动性。",
            ],
            sectors=["指数", "金融", "地产"],
            risk_flags=["短期内宏观数据可能压过公司基本面"],
            confidence="medium",
        ),
    ]


def _starter_candidates() -> list[DiscoveryCandidate]:
    return [
        DiscoveryCandidate(
            display_name="NVIDIA",
            symbol="NVDA",
            market="US",
            asset_type="stock",
            related_theme="AI 基础设施观察",
            why_watch="用于观察 AI 算力需求和半导体情绪的锚点公司。",
            evidence=["起始美股 AI 基础设施观察池"],
            risk_flags=["估值敏感", "供应链集中度风险"],
            next_actions=[
                "拉取行情",
                "拉取 SEC 公告",
                "对比 AI 半导体同业",
            ],
            confidence="medium",
        ),
        DiscoveryCandidate(
            display_name="Invesco QQQ Trust",
            symbol="QQQ",
            market="US",
            asset_type="ETF",
            related_theme="利率与大盘风险观察",
            why_watch=(
                "用于判断科技主题是单一个股驱动，还是整个市场共同驱动。"
            ),
            evidence=["起始大盘 ETF 语境"],
            risk_flags=["大型科技股权重集中"],
            next_actions=["拉取 ETF 行情", "与 SPY 对比"],
            confidence="medium",
        ),
        DiscoveryCandidate(
            display_name="Tencent",
            symbol="0700.HK",
            market="HK",
            asset_type="stock",
            related_theme="中国政策与消费观察",
            why_watch=(
                "大型港股中国平台公司，可用于观察跨市场情绪。"
            ),
            evidence=["起始港股中国平台公司观察池"],
            risk_flags=["政策风险", "中国消费和游戏周期敞口"],
            next_actions=[
                "拉取港股行情",
                "收集港交所公告",
                "对比平台公司同业",
            ],
            confidence="medium",
        ),
        DiscoveryCandidate(
            display_name="恒生科技 ETF 代理观察",
            symbol="3067.HK",
            market="HK",
            asset_type="ETF",
            related_theme="AI 基础设施观察",
            why_watch=(
                "在选择单一个股前，用 ETF 代理观察港股科技板块情绪。"
            ),
            evidence=["起始港股科技市场语境"],
            risk_flags=["使用前需要确认 ETF 成分和流动性"],
            next_actions=["核对 ETF 详情", "对比恒生科技指数方向"],
            confidence="low",
        ),
        DiscoveryCandidate(
            display_name="沪深 300 ETF 代理观察",
            symbol="510300.SH",
            market="CN",
            asset_type="ETF",
            related_theme="中国政策与消费观察",
            why_watch="用宽基 A 股代理区分市场 beta 和行业特定线索。",
            evidence=["起始 A 股宽基市场语境"],
            risk_flags=["需要检查跟踪误差和本地市场流动性"],
            next_actions=["拉取 A 股 ETF 行情", "对比沪深 300 和行业板块"],
            confidence="low",
        ),
        DiscoveryCandidate(
            display_name="中国半导体设备观察",
            symbol=None,
            market="CN",
            asset_type="sector",
            related_theme="AI 基础设施观察",
            why_watch=(
                "用于把 AI 基础设施主题映射到 A 股供应链。"
            ),
            evidence=["起始 A 股行业观察池"],
            risk_flags=["政策支持和出口管制都可能影响该主题"],
            next_actions=[
                "使用 AkShare/Tushare 行业数据",
                "映射行业成分股",
                "检查公告",
            ],
            confidence="low",
        ),
    ]
