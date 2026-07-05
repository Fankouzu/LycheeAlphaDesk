import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.live_data import (
    PullResult,
    pull_market_prices,
    pull_sec_filings,
)
from lychee_alphadesk.core.research import (
    ResearchDeepenResult,
    ResearchGapFillResult,
    ResearchPacket,
    deepen_research_queue,
    fill_research_data_gaps,
)

PullMarket = Callable[..., PullResult]
PullFilings = Callable[..., PullResult]


@dataclass(frozen=True)
class WorkbenchGate:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class CandidateCheck:
    display_name: str
    market: str
    symbol: str | None
    proxy_symbols: list[str]
    evidence_count: int
    gap_count: int
    data_gaps: list[str]
    status: str
    explanation: str
    beginner_question: str
    why_it_matters: str
    observation_entry: str
    what_to_check: str
    next_step: str
    priority: str
    evidence_status: str


@dataclass(frozen=True)
class WorkbenchCheckResult:
    created_at: str
    status: str
    packets_checked: int
    ready_count: int
    blocked_count: int
    proxy_price_count: int
    proxy_total: int
    gates: list[WorkbenchGate]
    candidates: list[CandidateCheck]
    beginner_brief: str
    artifact_path: Path | None
    fill_result: ResearchGapFillResult
    deepen_result: ResearchDeepenResult

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


def run_workbench_check(
    *,
    output_dir: Path,
    status: str | None = "new",
    limit: int = 5,
    force: bool = False,
    now: datetime | None = None,
    pull_market: PullMarket | None = None,
    pull_filings: PullFilings | None = None,
) -> WorkbenchCheckResult:
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    fill_result = fill_research_data_gaps(
        output_dir=output_dir,
        status=status,
        limit=limit,
        force=force,
        pull_market=pull_market or pull_market_prices,
        pull_filings=pull_filings or pull_sec_filings,
    )
    deepen_result = deepen_research_queue(
        output_dir=output_dir,
        status=status,
        limit=limit,
        now=now,
    )
    candidates = _candidate_checks(deepen_result.packets)
    proxy_price_count, proxy_total = _proxy_price_coverage(deepen_result.packets)
    gates = _workbench_gates(candidates, proxy_price_count, proxy_total)
    result_status = "blocked" if any(gate.status == "fail" for gate in gates) else "ready"
    beginner_brief = _beginner_brief(result_status, candidates)
    artifact_path = _write_workbench_check_artifact(
        output_dir=output_dir,
        created_at=created_at,
        status=result_status,
        candidates=candidates,
        gates=gates,
        proxy_price_count=proxy_price_count,
        proxy_total=proxy_total,
        beginner_brief=beginner_brief,
    )
    return WorkbenchCheckResult(
        created_at=created_at,
        status=result_status,
        packets_checked=len(deepen_result.packets),
        ready_count=sum(1 for candidate in candidates if candidate.status == "ready"),
        blocked_count=sum(1 for candidate in candidates if candidate.status == "blocked"),
        proxy_price_count=proxy_price_count,
        proxy_total=proxy_total,
        gates=gates,
        candidates=candidates,
        beginner_brief=beginner_brief,
        artifact_path=artifact_path,
        fill_result=fill_result,
        deepen_result=deepen_result,
    )


def beginner_research_brief(packets: list[ResearchPacket]) -> str:
    candidates = _candidate_checks(packets)
    status = "blocked" if any(candidate.data_gaps for candidate in candidates) else "ready"
    return _beginner_brief(status, candidates)


def _candidate_checks(packets: list[ResearchPacket]) -> list[CandidateCheck]:
    checks: list[CandidateCheck] = []
    for packet in packets:
        payload = packet.packet
        data_gaps = _text_list(payload.get("data_gaps"))
        evidence_ids = _text_list(payload.get("evidence_ids"))
        proxy_symbols = _proxy_symbols(payload)
        candidate = _dict_value(payload.get("candidate"))
        asset_type = _string_value(candidate.get("asset_type"))
        related_theme = _string_value(candidate.get("related_theme"))
        why_watch = _string_value(candidate.get("why_watch"))
        next_actions = _text_list(payload.get("next_actions"))
        status = "blocked" if data_gaps else "ready"
        if packet.symbol:
            explanation = why_watch
        elif proxy_symbols:
            explanation = (
                f"这不是单一股票，先用这些代理观察工具看方向。{why_watch}"
            )
        else:
            explanation = f"还没有可研究入口。{why_watch}"
        checks.append(
            CandidateCheck(
                display_name=packet.display_name,
                market=packet.market,
                symbol=packet.symbol,
                proxy_symbols=proxy_symbols,
                evidence_count=len(evidence_ids),
                gap_count=len(data_gaps),
                data_gaps=data_gaps,
                status=status,
                explanation=explanation,
                beginner_question=_beginner_question(
                    display_name=packet.display_name,
                    symbol=packet.symbol,
                    market=packet.market,
                    asset_type=asset_type,
                    related_theme=related_theme,
                ),
                why_it_matters=_why_it_matters(
                    symbol=packet.symbol,
                    proxy_symbols=proxy_symbols,
                    asset_type=asset_type,
                    why_watch=why_watch,
                ),
                observation_entry=_observation_entry(packet.symbol, proxy_symbols),
                what_to_check=_what_to_check(
                    market=packet.market,
                    asset_type=asset_type,
                    symbol=packet.symbol,
                    proxy_symbols=proxy_symbols,
                ),
                next_step=_next_step(next_actions, data_gaps),
                priority=_priority(
                    status=status,
                    symbol=packet.symbol,
                    proxy_symbols=proxy_symbols,
                    evidence_count=len(evidence_ids),
                    gap_count=len(data_gaps),
                ),
                evidence_status=_evidence_status(
                    evidence_count=len(evidence_ids),
                    gap_count=len(data_gaps),
                    proxy_symbols=proxy_symbols,
                ),
            )
        )
    return checks


def _workbench_gates(
    candidates: list[CandidateCheck],
    proxy_price_count: int,
    proxy_total: int,
) -> list[WorkbenchGate]:
    gates: list[WorkbenchGate] = []
    if candidates:
        gates.append(WorkbenchGate("研究队列", "pass", f"已检查 {len(candidates)} 个候选。"))
    else:
        gates.append(WorkbenchGate("研究队列", "fail", "没有研究候选，请先运行市场发现。"))

    missing_evidence = [
        candidate.display_name
        for candidate in candidates
        if candidate.evidence_count == 0
    ]
    gates.append(
        WorkbenchGate(
            "证据",
            "fail" if missing_evidence else "pass",
            "缺少证据: " + ", ".join(missing_evidence)
            if missing_evidence
            else "每个候选都有本地证据 ID。",
        )
    )

    missing_entry = [
        candidate.display_name
        for candidate in candidates
        if not candidate.symbol and not candidate.proxy_symbols
    ]
    gates.append(
        WorkbenchGate(
            "研究入口",
            "fail" if missing_entry else "pass",
            "缺少直接代码或代理标的: " + ", ".join(missing_entry)
            if missing_entry
            else "每个候选都有直接代码或代理标的。",
        )
    )

    proxy_status = "pass" if proxy_price_count == proxy_total else "fail"
    if proxy_total == 0:
        proxy_detail = "当前候选不需要代理行情。"
    else:
        proxy_detail = f"代理行情覆盖 {proxy_price_count}/{proxy_total}。"
    gates.append(WorkbenchGate("代理行情", proxy_status, proxy_detail))

    blocked = [candidate for candidate in candidates if candidate.data_gaps]
    if blocked:
        detail = "；".join(
            f"{candidate.display_name}: {', '.join(candidate.data_gaps)}"
            for candidate in blocked
        )
        gates.append(WorkbenchGate("数据缺口", "fail", detail))
    else:
        gates.append(WorkbenchGate("数据缺口", "pass", "当前深挖包没有未补齐缺口。"))
    return gates


def _beginner_brief(status: str, candidates: list[CandidateCheck]) -> str:
    ready = [candidate for candidate in candidates if candidate.status == "ready"]
    blocked = [candidate for candidate in candidates if candidate.status == "blocked"]
    lines = [
        "AlphaDesk 研究工作台",
        "边界: 研究任务台，不给买卖建议。",
        (
            f"状态: {_brief_status(status)} | "
            f"可执行 {len(ready)} | 阻塞 {len(blocked)} | 总任务 {len(candidates)}"
        ),
    ]

    lines.append("")
    lines.append("今日研究任务")
    if ready:
        for candidate in ready:
            lines.append(
                f"- {candidate.display_name} "
                f"[{candidate.market}] | 入口: {candidate.observation_entry} | "
                f"优先级: {candidate.priority} | 证据状态: {candidate.evidence_status}"
            )
            lines.append(f"  研究问题: {candidate.beginner_question}")
            lines.append(f"  关键核验: {candidate.what_to_check}")
            lines.append(f"  下一步: {candidate.next_step}")
            if candidate.proxy_symbols:
                lines.append("  代理核验: 核对成分、费用、流动性和是否可交易。")
    else:
        lines.append("- 无可执行任务。")

    lines.append("")
    lines.append("下一步队列")
    if ready:
        for candidate in ready:
            lines.append(f"- {candidate.display_name}: {candidate.next_step}")
    else:
        lines.append("- 先解除阻塞任务。")

    lines.append("")
    lines.append("阻塞任务")
    if blocked:
        for candidate in blocked:
            lines.append(
                f"- {candidate.display_name} "
                f"[{candidate.market}] | 入口: {candidate.observation_entry}"
            )
            gap_text = _gap_text(candidate.data_gaps)
            lines.append(f"  优先级: {candidate.priority}")
            lines.append(f"  研究问题: {candidate.beginner_question}")
            lines.append(f"  缺口: {gap_text}。")
            lines.append(f"  处理动作: 先补齐 {gap_text}。")
    else:
        lines.append("- 无。")
    return "\n".join(lines)


def _brief_status(status: str) -> str:
    return "可执行研究" if status == "ready" else "存在阻塞"


def _priority(
    *,
    status: str,
    symbol: str | None,
    proxy_symbols: list[str],
    evidence_count: int,
    gap_count: int,
) -> str:
    if status == "blocked" or gap_count:
        return "P0 待补数据"
    if symbol and evidence_count >= 2:
        return "P1 直接下钻"
    if symbol:
        return "P2 待增强证据"
    if proxy_symbols:
        return "P2 代理核验"
    return "P3 待映射"


def _evidence_status(
    *,
    evidence_count: int,
    gap_count: int,
    proxy_symbols: list[str],
) -> str:
    parts = [f"证据 {evidence_count} 条", f"缺口 {gap_count} 个"]
    if proxy_symbols:
        parts.append("代理已映射")
    return "；".join(parts)


def _gap_text(data_gaps: list[str]) -> str:
    return "；".join(gap.rstrip("。；;,.， ") for gap in data_gaps if gap.strip())


def _beginner_question(
    *,
    display_name: str,
    symbol: str | None,
    market: str,
    asset_type: str,
    related_theme: str,
) -> str:
    text = " ".join(
        item.lower()
        for item in [display_name, symbol or "", market, asset_type, related_theme]
        if item
    )
    if "qqq" in text or "nasdaq" in text or "纳斯达克" in text:
        return "美股科技股现在是独立主线，还是只是跟着大盘一起反弹？"
    if "baba" in text or "alibaba" in text or "阿里" in text:
        return "阿里 AI 云线索是公司自身变化，还是只是 AI 概念带动？"
    if "nvda" in text or "nvidia" in text or "英伟达" in text:
        return "AI 算力需求是在继续扩散，还是只集中在最热门龙头？"
    if "tsla" in text or "tesla" in text or "特斯拉" in text:
        return "电动车、储能、自动驾驶和机器人叙事是否互相支持？"
    if "tencent" in text or "腾讯" in text:
        return "港股中国平台公司是基本面改善，还是短期情绪反弹？"
    if "半导体" in text or "数据中心" in text:
        return "AI 数据中心主题是否已经扩散到可观察的供应链？"
    if "恒生" in text or "港股" in text or market.upper() == "HK":
        return "港股变化是整个市场的问题，还是只集中在某个板块？"
    if market.upper() == "CN":
        return "A 股这条线索是整体市场变化，还是行业自身出现变化？"
    if related_theme:
        return f"{display_name} 对应的主题“{related_theme}”是真变化，还是只有新闻热度？"
    return f"{display_name} 这条线索是否值得继续花时间研究？"


def _why_it_matters(
    *,
    symbol: str | None,
    proxy_symbols: list[str],
    asset_type: str,
    why_watch: str,
) -> str:
    if proxy_symbols:
        proxy_text = ", ".join(proxy_symbols)
        base = (
            f"这不是单一股票，所以先用 {proxy_text} 这些代理观察工具，"
            "把大方向、成交量和市场情绪看清楚。"
        )
    elif symbol:
        kind = " ETF/指数入口" if asset_type.lower() in {"etf", "index"} else "证券代码"
        base = f"{symbol} 是这条线索当前最直接的{kind}，可用于验证主题是否反映到市场数据里。"
    else:
        base = "这条线索还缺少可直接观察的入口，所以只能先当作主题线索。"
    if why_watch:
        return f"{base}{why_watch}"
    return base


def _observation_entry(symbol: str | None, proxy_symbols: list[str]) -> str:
    if symbol:
        return symbol
    if proxy_symbols:
        return ", ".join(proxy_symbols)
    return "暂无，需要先映射到可观察的股票、ETF 或指数。"


def _what_to_check(
    *,
    market: str,
    asset_type: str,
    symbol: str | None,
    proxy_symbols: list[str],
) -> str:
    if proxy_symbols:
        return (
            "代理 ETF/指数方向、成交量、成分覆盖度和相关新闻要互相支持；"
            "只看一天涨跌没有意义。"
        )
    if asset_type.lower() in {"etf", "index"}:
        return (
            "把它和更宽的市场基准对比，看方向、成交量、前十大成分和相关新闻是否一致。"
        )
    if symbol and market.upper() == "US":
        return "行情方向、成交量、相关新闻、SEC 公告和财报线索要能互相印证。"
    if symbol:
        return "行情方向、成交量、公告、财报和行业新闻要能互相印证。"
    return "先找到可观察入口，再检查行情、新闻、公告和成分是否能互相印证。"


def _next_step(next_actions: list[str], data_gaps: list[str]) -> str:
    if data_gaps:
        return f"下一步先补齐 {'；'.join(data_gaps)}。"
    if next_actions:
        cleaned_actions = [
            action.rstrip("。；;,.， ")
            for action in next_actions[:2]
            if action.strip()
        ]
        return "；".join(cleaned_actions)
    return "先查看观察入口的最近行情、成交量、相关新闻和公开资料。"


def _write_workbench_check_artifact(
    *,
    output_dir: Path,
    created_at: str,
    status: str,
    candidates: list[CandidateCheck],
    gates: list[WorkbenchGate],
    proxy_price_count: int,
    proxy_total: int,
    beginner_brief: str,
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = research_dir / f"workbench-check-{_safe_timestamp(created_at)}.json"
    output_path.write_text(
        json.dumps(
            {
                "created_at": created_at,
                "status": status,
                "candidate_count": len(candidates),
                "ready_count": sum(
                    1 for candidate in candidates if candidate.status == "ready"
                ),
                "blocked_count": sum(
                    1 for candidate in candidates if candidate.status == "blocked"
                ),
                "proxy_price_coverage": f"{proxy_price_count}/{proxy_total}",
                "gates": [asdict(gate) for gate in gates],
                "candidates": [asdict(candidate) for candidate in candidates],
                "beginner_brief": beginner_brief,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _proxy_price_coverage(packets: list[ResearchPacket]) -> tuple[int, int]:
    priced = 0
    total = 0
    for packet in packets:
        for row in _symbol_mapping_rows(packet.packet):
            total += 1
            if isinstance(row.get("latest_price"), dict):
                priced += 1
    return priced, total


def _proxy_symbols(payload: dict[str, object]) -> list[str]:
    symbols: list[str] = []
    for row in _symbol_mapping_rows(payload):
        symbol = row.get("symbol")
        if isinstance(symbol, str):
            symbols.append(symbol)
    return symbols


def _symbol_mapping_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    local_data = _dict_value(payload.get("local_data"))
    symbol_mapping = local_data.get("symbol_mapping")
    if not isinstance(symbol_mapping, list):
        return []
    return [row for row in symbol_mapping if isinstance(row, dict)]


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _safe_timestamp(value: str) -> str:
    normalized = value.replace("+00:00", "Z")
    return (
        normalized.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace(".", "")
        .replace("T", "-")
    )
