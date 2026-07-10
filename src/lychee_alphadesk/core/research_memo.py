import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.config import AlphaDeskConfig, load_config
from lychee_alphadesk.core.llm import JsonPoster, request_chat_json
from lychee_alphadesk.core.research_db import write_research_memo_record
from lychee_alphadesk.core.workbench import (
    CandidateCheck,
    ResearchVerificationResult,
    verify_research_task,
)


@dataclass(frozen=True)
class ResearchMemo:
    summary: str
    working_hypothesis: str
    evidence_reading: str
    support_points: list[str]
    skeptic_review: list[str]
    falsification_checks: list[str]
    missing_evidence: list[str]
    next_data_requests: list[str]
    next_research_steps: list[str]
    confidence: str


@dataclass(frozen=True)
class ResearchMemoResult:
    created_at: str
    memo: ResearchMemo
    candidate: CandidateCheck
    verification: ResearchVerificationResult
    artifact_path: Path
    db_path: Path


def generate_research_memo(
    *,
    output_dir: Path,
    symbol: str | None = None,
    name: str | None = None,
    status: str | None = "new",
    limit: int = 5,
    config: AlphaDeskConfig | None = None,
    post_json: JsonPoster | None = None,
    now: datetime | None = None,
) -> ResearchMemoResult:
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    verification = verify_research_task(
        output_dir=output_dir,
        symbol=symbol,
        name=name,
        status=status,
        limit=limit,
        now=now,
    )
    llm_config = config or load_config()
    payload = request_chat_json(
        llm_config,
        messages=_build_research_memo_messages(verification),
        post_json=post_json,
    )
    memo = _parse_research_memo(payload)
    artifact_payload = _research_memo_payload(
        created_at=created_at,
        memo=memo,
        verification=verification,
    )
    artifact_path = _write_research_memo_artifact(
        output_dir=output_dir,
        created_at=created_at,
        payload=artifact_payload,
    )
    db_path = write_research_memo_record(
        output_dir=output_dir,
        memo_id=f"research-memo:{artifact_path.stem.removeprefix('research-memo-')}",
        created_at=created_at,
        display_name=verification.candidate.display_name,
        symbol=verification.candidate.symbol,
        market=verification.candidate.market,
        confidence=memo.confidence,
        summary=memo.summary,
        support_count=len(memo.support_points),
        skeptic_count=len(memo.skeptic_review),
        missing_count=len(memo.missing_evidence),
        next_step_count=len(memo.next_research_steps),
        memo_path=artifact_path,
        verification_path=verification.artifact_path,
        payload=artifact_payload,
    )
    return ResearchMemoResult(
        created_at=created_at,
        memo=memo,
        candidate=verification.candidate,
        verification=verification,
        artifact_path=artifact_path,
        db_path=db_path,
    )


def _build_research_memo_messages(
    verification: ResearchVerificationResult,
) -> list[dict[str, str]]:
    schema = {
        "summary": "string",
        "working_hypothesis": "string",
        "evidence_reading": "string",
        "support_points": ["string"],
        "skeptic_review": ["string"],
        "falsification_checks": ["string"],
        "missing_evidence": ["string"],
        "next_data_requests": ["string"],
        "next_research_steps": ["string"],
        "confidence": "low|medium|high",
    }
    context = {
        "candidate": asdict(verification.candidate),
        "verification_status": verification.status,
        "verification_label": verification.status_label,
        "checks": [asdict(check) for check in verification.checks],
        "证据板": verification.evidence_board,
        "decision_board": asdict(verification.decision_board),
        "evidence_change": asdict(verification.evidence_change),
        "analyst_readout": asdict(verification.analyst_readout),
        "hypothesis_panel": asdict(verification.hypothesis_panel),
        "conclusion": verification.conclusion,
        "verification_next_actions": verification.next_actions,
    }
    system_prompt = (
        "你是 Lychee AlphaDesk 的二阶段研究备忘录分析员。"
        "Return one valid JSON object only, with no markdown fences. "
        "All user-facing string values must be written in Simplified Chinese. "
        "Use only evidence-backed research workflow language. "
        "Do not give buy/sell/hold advice, target prices, allocation advice, "
        "position sizing, expected return, or trading instructions. "
        "Your job is to summarize the evidence board, list support points, "
        "state one working hypothesis, write a skeptic review, define "
        "falsification checks that would downgrade the clue, identify missing "
        "evidence, request the next data to collect, and propose next research "
        "steps."
    )
    user_prompt = (
        "请基于这条研究任务的下钻核验结果生成研究备忘录。\n\n"
        f"Required JSON schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Context JSON:\n{json.dumps(context, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _parse_research_memo(payload: dict[str, object]) -> ResearchMemo:
    memo = ResearchMemo(
        summary=_required_string(payload, "summary"),
        working_hypothesis=_required_string(payload, "working_hypothesis"),
        evidence_reading=_required_string(payload, "evidence_reading"),
        support_points=_required_string_list(payload, "support_points"),
        skeptic_review=_required_string_list(payload, "skeptic_review"),
        falsification_checks=_required_string_list(payload, "falsification_checks"),
        missing_evidence=_required_string_list(payload, "missing_evidence"),
        next_data_requests=_required_string_list(payload, "next_data_requests"),
        next_research_steps=_required_string_list(payload, "next_research_steps"),
        confidence=_required_string(payload, "confidence"),
    )
    if memo.confidence not in {"low", "medium", "high"}:
        raise ValueError("LLM 研究备忘录 confidence 必须是 low、medium 或 high。")
    _reject_advice_language(asdict(memo))
    return memo


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"LLM 研究备忘录缺少文本字段: {key}")
    return value.strip()


def _required_string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"LLM 研究备忘录缺少文本列表字段: {key}")
    rows: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"LLM 研究备忘录字段 {key} 必须只包含文本。")
        rows.append(item.strip())
    return rows


def _reject_advice_language(value: object) -> None:
    advice_terms = [
        "买入",
        "卖出",
        "持有",
        "目标价",
        "仓位",
        "加仓",
        "减仓",
        "收益预期",
        "预期收益",
        "交易指令",
        "buy",
        "sell",
        "hold",
        "target price",
        "allocation",
        "position size",
        "expected return",
        "trading instruction",
    ]
    strings = _flatten_strings(value)
    for text in strings:
        normalized = text.lower()
        if any(term in normalized for term in advice_terms):
            raise ValueError("LLM 研究备忘录包含买卖或仓位建议语言。")


def _flatten_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        rows: list[str] = []
        for item in value.values():
            rows.extend(_flatten_strings(item))
        return rows
    if isinstance(value, list):
        rows = []
        for item in value:
            rows.extend(_flatten_strings(item))
        return rows
    return []


def _write_research_memo_artifact(
    *,
    output_dir: Path,
    created_at: str,
    payload: dict[str, object],
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    output_path = _unique_research_memo_path(research_dir, created_at)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _unique_research_memo_path(research_dir: Path, created_at: str) -> Path:
    timestamp = _safe_timestamp(created_at)
    output_path = research_dir / f"research-memo-{timestamp}.json"
    if not output_path.exists():
        return output_path
    for index in range(1, 1000):
        candidate = research_dir / f"research-memo-{timestamp}~{index:02d}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法生成唯一研究备忘录文件名。")


def _research_memo_payload(
    *,
    created_at: str,
    memo: ResearchMemo,
    verification: ResearchVerificationResult,
) -> dict[str, object]:
    return {
        "mode": "llm-research-memo",
        "created_at": created_at,
        "candidate": asdict(verification.candidate),
        "verification_path": str(verification.artifact_path),
        "verification": {
            "status": verification.status,
            "status_label": verification.status_label,
            "checks": [asdict(check) for check in verification.checks],
            "evidence_board": verification.evidence_board,
            "decision_board": asdict(verification.decision_board),
            "evidence_change": asdict(verification.evidence_change),
            "conclusion": verification.conclusion,
            "next_actions": verification.next_actions,
        },
        "memo": asdict(memo),
        "disclaimer": "研究备忘录用于组织下一步研究，不是买卖建议。",
    }


def _safe_timestamp(value: str) -> str:
    return (
        value.replace(":", "")
        .replace("-", "")
        .replace("+0000", "Z")
        .replace("+00:00", "Z")
    )
