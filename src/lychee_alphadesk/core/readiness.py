from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lychee_alphadesk.core.config import config_file_path, load_config
from lychee_alphadesk.core.live_data import run_cached_data_health
from lychee_alphadesk.core.paths import DEFAULT_OUTPUT_DIR


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    label: str
    status: str
    detail: str
    required: bool = True


@dataclass(frozen=True)
class ReadinessReport:
    created_at: str
    status: str
    checks: list[ReadinessCheck]
    artifact_path: Path

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


def run_readiness_audit(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    config_path: Path | None = None,
    now: datetime | None = None,
) -> ReadinessReport:
    """Check local prerequisites without fetching data or calling an LLM."""
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    checks = _build_checks(output_dir, config_path=config_path)
    required_errors = [check for check in checks if check.required and check.status == "error"]
    status = "blocked" if required_errors else (
        "partial" if any(check.status == "warning" for check in checks) else "ready"
    )
    artifact_path = _write_artifact(
        output_dir=output_dir,
        created_at=created_at,
        status=status,
        checks=checks,
    )
    return ReadinessReport(created_at, status, checks, artifact_path)


def _build_checks(output_dir: Path, *, config_path: Path | None) -> list[ReadinessCheck]:
    path = config_path or config_file_path()
    checks: list[ReadinessCheck] = []
    if not path.exists():
        checks.append(
            ReadinessCheck(
                "config",
                "本地配置",
                "warning",
                f"尚未创建配置文件: {path}；可运行 `lychee setup`。",
                required=False,
            )
        )
        llm_configured = False
    else:
        try:
            config = load_config(path)
        except (OSError, ValueError) as error:
            checks.append(ReadinessCheck("config", "本地配置", "error", f"配置无法读取: {error}"))
            llm_configured = False
        else:
            checks.append(ReadinessCheck("config", "本地配置", "pass", f"已读取 {path}"))
            llm = config.llm.openai_compatible
            llm_configured = bool(llm.base_url and llm.api_key and llm.model)

    checks.append(
        ReadinessCheck(
            "llm",
            "LLM 分析服务",
            "pass" if llm_configured else "error",
            (
                "已配置 OpenAI-compatible base URL、API key 和模型。"
                if llm_configured
                else "未配置完整 LLM；今日发现和研究备忘录不能运行，不提供回退分析。"
            ),
        )
    )

    health = run_cached_data_health(output_dir)
    market_rows = next((check for check in health if check.name == "market-cache-present"), None)
    news_rows = next((check for check in health if check.name == "news-cache-present"), None)
    checks.append(_required_cache_check("market", "行情缓存", market_rows))
    checks.append(_required_cache_check("news", "新闻缓存", news_rows))
    checks.extend(
        ReadinessCheck(
            check.name,
            check.name,
            "warning" if check.status == "warning" else "pass",
            check.message,
            required=False,
        )
        for check in health
        if check.name.startswith("market-") and check.name != "market-cache-present"
    )

    queue_count = _research_candidate_count(output_dir)
    checks.append(
        ReadinessCheck(
            "research_queue",
            "研究任务队列",
            "pass" if queue_count else "error",
            (
                f"本地研究库有 {queue_count} 条候选任务。"
                if queue_count
                else "暂无研究任务；先运行 `lychee discover today` 建立发现入口。"
            ),
        )
    )
    checks.append(
        ReadinessCheck(
            "portfolio",
            "组合审计",
            "pass",
            "未导入组合也不阻塞市场研究；如需组合上下文，可导入只读券商 CSV。",
            required=False,
        )
    )
    checks.append(
        ReadinessCheck(
            "ipo",
            "IPO/打新资料",
            "pass",
            "人工核验入口可用；未导入 IPO 资料不阻塞市场研究。",
            required=False,
        )
    )
    return checks


def _required_cache_check(
    key: str,
    label: str,
    check: object | None,
) -> ReadinessCheck:
    status = getattr(check, "status", "error")
    message = getattr(check, "message", "缓存检查不可用")
    if status == "pass":
        return ReadinessCheck(key, label, "pass", message)
    return ReadinessCheck(key, label, "error", message)


def _research_candidate_count(output_dir: Path) -> int:
    db_path = output_dir / "research.sqlite3"
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM research_candidates"
            ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row and isinstance(row[0], int) else 0


def _write_artifact(
    *,
    output_dir: Path,
    created_at: str,
    status: str,
    checks: list[ReadinessCheck],
) -> Path:
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    stamp = created_at.replace("+00:00", "Z").replace(":", "").replace("-", "")
    path = research_dir / f"readiness-{stamp}.json"
    suffix = 1
    while path.exists():
        path = research_dir / f"readiness-{stamp}~{suffix:02d}.json"
        suffix += 1
    payload = {
        "created_at": created_at,
        "status": status,
        "checks": [asdict(check) for check in checks],
        "boundary": "就绪审计只读取本地状态，不拉取数据、不调用 LLM、不产生投资建议。",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
