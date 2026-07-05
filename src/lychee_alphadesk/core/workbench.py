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
        why_watch = _string_value(candidate.get("why_watch"))
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
        "给新手的读法",
        "这不是买入建议，也不是推荐清单；它是帮你决定下一步研究什么的地图。",
    ]
    if status == "ready":
        lines.append(
            "当前状态: 可以继续研究。意思是数据入口已经够用，"
            "可以进入成分、流动性和公告核验。"
        )
    else:
        lines.append("当前状态: 未达标。意思是至少还有关键数据缺口，暂时不要下结论。")

    lines.append("")
    lines.append("可以继续研究的线索")
    if ready:
        for candidate in ready:
            entry = candidate.symbol or ", ".join(candidate.proxy_symbols)
            lines.append(
                f"- {candidate.display_name} [{candidate.market}]: "
                f"观察入口 {entry}。{candidate.explanation}"
            )
    else:
        lines.append("- 暂无。")

    lines.append("")
    lines.append("暂时不要下结论")
    if blocked:
        for candidate in blocked:
            lines.append(
                f"- {candidate.display_name} [{candidate.market}]: "
                f"{'；'.join(candidate.data_gaps)}"
            )
    else:
        lines.append("- 暂无阻塞缺口。")

    lines.append("")
    lines.append("怎么理解代理")
    lines.append(
        "- 代理观察工具不是原主题本身，只是临时用 ETF 或指数入口观察方向、成交量和市场情绪。"
    )
    lines.append("- 代理通过后还要检查成分、费用、流动性和是否真的对应主题。")
    return "\n".join(lines)


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
