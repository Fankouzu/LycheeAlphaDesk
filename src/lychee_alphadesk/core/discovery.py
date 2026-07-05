import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.config import AlphaDeskConfig, load_config
from lychee_alphadesk.core.live_data import build_cached_data_snapshot
from lychee_alphadesk.core.llm import JsonPoster, request_chat_json

DISCOVERY_CACHE_FILENAME = "discovery-today.json"
DEFAULT_MARKETS = ["US", "HK", "CN"]
LLM_REQUIRED_MESSAGE = (
    "LLM provider is not configured. Run "
    "`lychee setup llm set <base_url> <api_key> MODEL_NAME` before Today Discovery."
)


class DiscoveryLLMRequiredError(RuntimeError):
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
        raise ValueError(f"Unsupported discovery market: {', '.join(invalid)}")
    return normalized or DEFAULT_MARKETS.copy()


def build_today_discovery_report(
    markets: list[str] | None = None,
    config: AlphaDeskConfig | None = None,
    output_dir: Path | None = None,
    post_json: JsonPoster | None = None,
) -> DiscoveryReport:
    active_config = config or load_config()
    require_active_llm(active_config)
    selected_markets = markets or DEFAULT_MARKETS.copy()
    context = _build_llm_context(selected_markets, output_dir)
    payload = request_chat_json(
        active_config,
        messages=_build_discovery_messages(context),
        post_json=post_json,
    )
    themes = _parse_themes(payload)
    candidates = _parse_candidates(payload)
    warnings = _optional_string_list(payload, "warnings")
    warnings.append("Candidates are research targets only and are not buy/sell recommendations.")
    return DiscoveryReport(
        mode="llm-synthesized",
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        markets=selected_markets,
        sources=[
            DiscoverySource(
                provider="openai-compatible-llm",
                market=market,
                description=(
                    "Model synthesized Today Discovery from starter context and "
                    "available local live-data cache."
                ),
            )
            for market in selected_markets
        ],
        themes=themes,
        candidates=candidates,
        warnings=warnings,
        next_actions=_required_string_list(payload, "next_actions"),
        disclaimer="Not investment advice. Use this report to decide what to research next.",
    )


def require_active_llm(config: AlphaDeskConfig) -> None:
    llm = config.llm.openai_compatible
    if llm.base_url and llm.api_key and llm.model:
        return
    raise DiscoveryLLMRequiredError(LLM_REQUIRED_MESSAGE)


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
    lines = ["Today Discovery", f"Mode: {report.mode}"]
    lines.append(f"Markets: {', '.join(report.markets)}")
    if output_path is not None:
        lines.append(f"Cache: {output_path}")
    lines.append("")
    lines.append(report.disclaimer)
    lines.append("")
    lines.append("Themes")
    for theme in report.themes[:3]:
        lines.append(f"- {theme.name}: {theme.summary}")
    lines.append("")
    lines.append("Watch candidates")
    for candidate in report.candidates[:5]:
        symbol = f" ({candidate.symbol})" if candidate.symbol else ""
        lines.append(
            f"- {candidate.display_name}{symbol} [{candidate.market}]: "
            f"{candidate.why_watch}"
        )
    if report.warnings:
        lines.append("")
        lines.append("Warnings")
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
        "You are Lychee AlphaDesk's market discovery analyst. "
        "Return one valid JSON object only, with no markdown fences. "
        "Do not give buy/sell advice, target prices, or allocation advice. "
        "Use watch/research/drill-down language only. "
        "Every theme and candidate must cite evidence from the provided context."
    )
    user_prompt = (
        "Create Today Discovery for a beginner investor from this context.\n\n"
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
        raise DiscoveryLLMRequiredError("LLM discovery response did not include themes")
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
        raise DiscoveryLLMRequiredError("LLM discovery response did not include candidates")
    return candidates


def _required_dict_list(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise DiscoveryLLMRequiredError(f"LLM discovery response missing list field: {key}")
    items: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise DiscoveryLLMRequiredError(
                f"LLM discovery response field {key} must contain objects"
            )
        items.append(dict(item))
    return items


def _required_string(payload: dict[str, object], key: str) -> str:
    raw = payload.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise DiscoveryLLMRequiredError(f"LLM discovery response missing string field: {key}")
    return raw.strip()


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise DiscoveryLLMRequiredError(
            f"LLM discovery response field {key} must be string or null"
        )
    return raw.strip() or None


def _required_string_list(payload: dict[str, object], key: str) -> list[str]:
    values = _optional_string_list(payload, key)
    if not values:
        raise DiscoveryLLMRequiredError(f"LLM discovery response missing string list field: {key}")
    return values


def _optional_string_list(payload: dict[str, object], key: str) -> list[str]:
    raw = payload.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DiscoveryLLMRequiredError(f"LLM discovery response field {key} must be a list")
    values = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise DiscoveryLLMRequiredError(
                f"LLM discovery response field {key} must contain strings"
            )
        values.append(item.strip())
    return values


def _reject_advice_recommendation(value: str) -> None:
    normalized = value.strip().lower()
    if normalized in {"buy", "sell", "hold", "strong_buy", "strong_sell"}:
        raise DiscoveryLLMRequiredError(
            "LLM discovery response used investment-advice recommendation language"
        )


def _starter_themes() -> list[DiscoveryTheme]:
    return [
        DiscoveryTheme(
            name="AI infrastructure watch",
            markets=["US", "HK", "CN"],
            summary=(
                "Track compute, semiconductors, cloud, and data-center supply chains "
                "before choosing individual stocks."
            ),
            evidence=[
                "Starter universe includes US mega-cap AI infrastructure names.",
                "HK and CN markets often express the same theme through platform, "
                "hardware, and equipment supply chains.",
            ],
            sectors=["Semiconductors", "Cloud infrastructure", "Data centers"],
            risk_flags=[
                "High valuation sensitivity",
                "Export-control and supply-chain policy risk",
            ],
            confidence="medium",
        ),
        DiscoveryTheme(
            name="China policy and consumption watch",
            markets=["HK", "CN"],
            summary=(
                "Track policy-linked sectors and consumer demand signals across HK-listed "
                "China companies and A-share sector boards."
            ),
            evidence=[
                "HK and CN markets can react together when policy, currency, "
                "or consumer data changes.",
            ],
            sectors=["Consumer", "Internet platforms", "Financials"],
            risk_flags=[
                "Policy headlines can reverse quickly",
                "Currency and liquidity conditions matter",
            ],
            confidence="medium",
        ),
        DiscoveryTheme(
            name="Rates and broad-market risk watch",
            markets=["US", "HK"],
            summary=(
                "Use broad indexes and rates-sensitive sectors as context before drilling "
                "into single-name risk."
            ),
            evidence=[
                "US rate expectations affect equity duration risk.",
                "HKD-linked financial conditions can influence Hong Kong market liquidity.",
            ],
            sectors=["Indexes", "Financials", "Real estate"],
            risk_flags=["Macro data can dominate company fundamentals in the short run"],
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
            related_theme="AI infrastructure watch",
            why_watch="Anchor candidate for AI compute demand and semiconductor sentiment.",
            evidence=["Starter US AI infrastructure universe"],
            risk_flags=["Valuation sensitivity", "Supply-chain concentration"],
            next_actions=[
                "Pull market prices",
                "Pull SEC filings",
                "Compare AI semiconductor peers",
            ],
            confidence="medium",
        ),
        DiscoveryCandidate(
            display_name="Invesco QQQ Trust",
            symbol="QQQ",
            market="US",
            asset_type="ETF",
            related_theme="Rates and broad-market risk watch",
            why_watch=(
                "Broad technology ETF for checking whether a theme is single-name "
                "or market-wide."
            ),
            evidence=["Starter broad-market ETF context"],
            risk_flags=["Concentrated mega-cap exposure"],
            next_actions=["Pull ETF prices", "Compare against SPY"],
            confidence="medium",
        ),
        DiscoveryCandidate(
            display_name="Tencent",
            symbol="0700.HK",
            market="HK",
            asset_type="stock",
            related_theme="China policy and consumption watch",
            why_watch=(
                "Large HK-listed China platform company useful for cross-market "
                "sentiment checks."
            ),
            evidence=["Starter HK China-platform watch universe"],
            risk_flags=["Policy risk", "China consumption and gaming-cycle exposure"],
            next_actions=[
                "Pull HK price data",
                "Collect HKEX announcements",
                "Compare platform peers",
            ],
            confidence="medium",
        ),
        DiscoveryCandidate(
            display_name="Hang Seng Tech ETF proxy",
            symbol="3067.HK",
            market="HK",
            asset_type="ETF",
            related_theme="AI infrastructure watch",
            why_watch=(
                "ETF-style proxy for checking HK technology sentiment before "
                "picking single names."
            ),
            evidence=["Starter HK technology-market context"],
            risk_flags=["ETF constituents and liquidity should be verified before use"],
            next_actions=["Verify ETF details", "Compare Hang Seng Tech index direction"],
            confidence="low",
        ),
        DiscoveryCandidate(
            display_name="CSI 300 ETF proxy",
            symbol="510300.SH",
            market="CN",
            asset_type="ETF",
            related_theme="China policy and consumption watch",
            why_watch="Broad A-share proxy for separating market beta from sector-specific ideas.",
            evidence=["Starter A-share broad-market context"],
            risk_flags=["Tracking error and local-market liquidity should be checked"],
            next_actions=["Pull A-share ETF prices", "Compare CSI 300 and sector boards"],
            confidence="low",
        ),
        DiscoveryCandidate(
            display_name="China semiconductor equipment watch",
            symbol=None,
            market="CN",
            asset_type="sector",
            related_theme="AI infrastructure watch",
            why_watch=(
                "Sector candidate for mapping AI infrastructure themes into "
                "A-share supply chains."
            ),
            evidence=["Starter CN sector watch universe"],
            risk_flags=["Policy support and export controls can both affect the theme"],
            next_actions=[
                "Use AkShare/Tushare sector data",
                "Map sector constituents",
                "Check announcements",
            ],
            confidence="low",
        ),
    ]
