import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

DISCOVERY_CACHE_FILENAME = "discovery-today.json"
DEFAULT_MARKETS = ["US", "HK", "CN"]


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


def build_today_discovery_report(markets: list[str] | None = None) -> DiscoveryReport:
    selected_markets = markets or DEFAULT_MARKETS.copy()
    themes = [
        theme
        for theme in _fallback_themes()
        if set(theme.markets).intersection(selected_markets)
    ]
    candidates = [
        candidate
        for candidate in _fallback_candidates()
        if candidate.market in selected_markets
    ]
    return DiscoveryReport(
        mode="fallback",
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        markets=selected_markets,
        sources=[
            DiscoverySource(
                provider="fallback-starter-universe",
                market=market,
                description=(
                    "Starter discovery scaffold for first-run research; live provider "
                    "synthesis will replace this as integrations mature."
                ),
            )
            for market in selected_markets
        ],
        themes=themes,
        candidates=candidates,
        warnings=[
            "LLM synthesis is not active in this first discovery slice; "
            "using a deterministic fallback report.",
            "Candidates are research targets only and are not buy/sell recommendations.",
        ],
        next_actions=[
            "Configure no-key and key-based data providers in lychee setup.",
            "Select a theme or candidate, then drill down into prices, "
            "news, filings, and financials.",
            "Record evidence and counterarguments before any manual investment action.",
        ],
        disclaimer="Not investment advice. Use this report to decide what to research next.",
    )


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


def _fallback_themes() -> list[DiscoveryTheme]:
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


def _fallback_candidates() -> list[DiscoveryCandidate]:
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
