from dataclasses import dataclass

from lychee_alphadesk.core.research_db import ResearchQueueItem


@dataclass(frozen=True)
class SymbolMappingProposal:
    symbol: str
    display_name: str
    market: str
    asset_type: str
    proxy_type: str
    confidence: str
    reason: str
    evidence_ids: list[str]
    requires_review: bool = True


def suggest_symbol_mappings(item: ResearchQueueItem) -> list[SymbolMappingProposal]:
    """Return auditable proxy mappings for theme/index candidates without symbols."""
    if item.symbol:
        return []

    text = " ".join(
        [
            item.display_name,
            item.market,
            item.asset_type,
            item.related_theme,
            item.why_watch,
        ]
    ).lower()
    evidence_ids = item.evidence[:3]
    proposals: list[SymbolMappingProposal] = []

    if item.market == "HK":
        if "恒生指数" in text or item.asset_type.lower() == "index":
            proposals.append(
                SymbolMappingProposal(
                    symbol="2800.HK",
                    display_name="盈富基金",
                    market="HK",
                    asset_type="ETF",
                    proxy_type="tradable_index_proxy",
                    confidence="medium",
                    reason="用于把恒生指数压力主题映射到可交易的港股宽基 ETF 代理观察。",
                    evidence_ids=evidence_ids,
                )
            )
        if "科技" in text or "恒生科技" in text:
            proposals.append(
                SymbolMappingProposal(
                    symbol="3033.HK",
                    display_name="南方恒生科技",
                    market="HK",
                    asset_type="ETF",
                    proxy_type="tradable_sector_proxy",
                    confidence="low",
                    reason="用于把港股科技板块主题映射到可交易 ETF 代理观察，成分/费用需补资料。",
                    evidence_ids=evidence_ids,
                )
            )

    if item.market == "CN":
        if any(keyword in text for keyword in ("ai", "人工智能", "数据中心", "算力")):
            proposals.extend(
                [
                    SymbolMappingProposal(
                        symbol="159819.SZ",
                        display_name="人工智能 ETF",
                        market="CN",
                        asset_type="ETF",
                        proxy_type="tradable_theme_proxy",
                        confidence="low",
                        reason=(
                            "用于把 AI 主题映射到 A 股人工智能 ETF 代理观察，"
                            "成分/费用需补资料。"
                        ),
                        evidence_ids=evidence_ids,
                    ),
                    SymbolMappingProposal(
                        symbol="515050.SH",
                        display_name="5G 通信 ETF",
                        market="CN",
                        asset_type="ETF",
                        proxy_type="tradable_supply_chain_proxy",
                        confidence="low",
                        reason="用于观察 AI 数据中心可能关联的通信设备链条，需验证主题相关性。",
                        evidence_ids=evidence_ids,
                    ),
                ]
            )
        if any(keyword in text for keyword in ("半导体", "芯片", "服务器", "高科技")):
            proposals.append(
                SymbolMappingProposal(
                    symbol="512480.SH",
                    display_name="半导体 ETF",
                    market="CN",
                    asset_type="ETF",
                    proxy_type="tradable_supply_chain_proxy",
                    confidence="low",
                    reason="用于把半导体或硬件供应链主题映射到 A 股 ETF 代理观察。",
                    evidence_ids=evidence_ids,
                )
            )
        if item.asset_type.lower() == "index" and not proposals:
            proposals.append(
                SymbolMappingProposal(
                    symbol="510300.SH",
                    display_name="沪深 300 ETF",
                    market="CN",
                    asset_type="ETF",
                    proxy_type="tradable_index_proxy",
                    confidence="low",
                    reason="用于把 A 股宽基指数主题映射到可交易 ETF 代理观察。",
                    evidence_ids=evidence_ids,
                )
            )

    return _unique_proposals(proposals)


def _unique_proposals(
    proposals: list[SymbolMappingProposal],
) -> list[SymbolMappingProposal]:
    seen: set[str] = set()
    unique: list[SymbolMappingProposal] = []
    for proposal in proposals:
        if proposal.symbol in seen:
            continue
        seen.add(proposal.symbol)
        unique.append(proposal)
    return unique
