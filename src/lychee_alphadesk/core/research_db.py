import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from lychee_alphadesk.core.discovery import DiscoveryReport


@dataclass(frozen=True)
class ResearchQueueItem:
    candidate_id: int
    run_id: str
    created_at: str
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
    status: str


@dataclass(frozen=True)
class ResearchPacketRecord:
    packet_id: str
    candidate_id: int
    created_at: str
    display_name: str
    symbol: str | None
    market: str
    packet: dict[str, object]
    artifact_path: str


@dataclass(frozen=True)
class ResearchReviewRecord:
    review_id: str
    created_at: str
    display_name: str
    symbol: str | None
    market: str
    verdict: str
    verdict_label: str
    note: str
    support_count: int
    risk_count: int
    missing_count: int
    review_path: str
    verification_path: str
    payload: dict[str, object]


@dataclass(frozen=True)
class ResearchMemoRecord:
    memo_id: str
    created_at: str
    display_name: str
    symbol: str | None
    market: str
    confidence: str
    summary: str
    support_count: int
    skeptic_count: int
    missing_count: int
    next_step_count: int
    memo_path: str
    verification_path: str
    payload: dict[str, object]


def research_db_path(output_dir: Path) -> Path:
    return output_dir / "research.sqlite3"


def init_research_db(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = research_db_path(output_dir)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS discovery_runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                markets_json TEXT NOT NULL,
                report_path TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                next_actions_json TEXT NOT NULL,
                disclaimer TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS research_candidates (
                candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                display_name TEXT NOT NULL,
                symbol TEXT,
                market TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                related_theme TEXT NOT NULL,
                why_watch TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                risk_flags_json TEXT NOT NULL,
                next_actions_json TEXT NOT NULL,
                confidence TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                FOREIGN KEY (run_id) REFERENCES discovery_runs(run_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_research_candidates_status_created
            ON research_candidates(status, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS research_packets (
                packet_id TEXT PRIMARY KEY,
                candidate_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                display_name TEXT NOT NULL,
                symbol TEXT,
                market TEXT NOT NULL,
                packet_json TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                FOREIGN KEY (candidate_id) REFERENCES research_candidates(candidate_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_research_packets_created
            ON research_packets(created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS research_reviews (
                review_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                display_name TEXT NOT NULL,
                symbol TEXT,
                market TEXT NOT NULL,
                verdict TEXT NOT NULL,
                verdict_label TEXT NOT NULL,
                note TEXT NOT NULL,
                support_count INTEGER NOT NULL,
                risk_count INTEGER NOT NULL,
                missing_count INTEGER NOT NULL,
                review_path TEXT NOT NULL,
                verification_path TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_research_reviews_created
            ON research_reviews(created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS research_memos (
                memo_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                display_name TEXT NOT NULL,
                symbol TEXT,
                market TEXT NOT NULL,
                confidence TEXT NOT NULL,
                summary TEXT NOT NULL,
                support_count INTEGER NOT NULL,
                skeptic_count INTEGER NOT NULL,
                missing_count INTEGER NOT NULL,
                next_step_count INTEGER NOT NULL,
                memo_path TEXT NOT NULL,
                verification_path TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_research_memos_created
            ON research_memos(created_at DESC)
            """
        )
    return db_path


def write_discovery_research_run(
    report: DiscoveryReport,
    output_dir: Path,
    report_path: Path,
) -> Path:
    init_research_db(output_dir)
    run_id = f"discovery:{report.created_at}"
    with sqlite3.connect(research_db_path(output_dir)) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO discovery_runs (
                run_id,
                created_at,
                mode,
                markets_json,
                report_path,
                warnings_json,
                next_actions_json,
                disclaimer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                report.created_at,
                report.mode,
                json.dumps(report.markets, ensure_ascii=False),
                str(report_path),
                json.dumps(report.warnings, ensure_ascii=False),
                json.dumps(report.next_actions, ensure_ascii=False),
                report.disclaimer,
            ),
        )
        connection.execute("DELETE FROM research_candidates WHERE run_id = ?", (run_id,))
        for candidate in report.candidates:
            connection.execute(
                """
                INSERT INTO research_candidates (
                    run_id,
                    created_at,
                    display_name,
                    symbol,
                    market,
                    asset_type,
                    related_theme,
                    why_watch,
                    evidence_json,
                    risk_flags_json,
                    next_actions_json,
                    confidence,
                    recommendation,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    report.created_at,
                    candidate.display_name,
                    candidate.symbol,
                    candidate.market,
                    candidate.asset_type,
                    candidate.related_theme,
                    candidate.why_watch,
                    json.dumps(candidate.evidence, ensure_ascii=False),
                    json.dumps(candidate.risk_flags, ensure_ascii=False),
                    json.dumps(candidate.next_actions, ensure_ascii=False),
                    candidate.confidence,
                    candidate.recommendation,
                    "new",
                ),
            )
    return research_db_path(output_dir)


def list_research_queue(
    output_dir: Path,
    *,
    status: str | None = None,
    limit: int = 20,
    dedupe: bool = True,
) -> list[ResearchQueueItem]:
    db_path = research_db_path(output_dir)
    if not db_path.exists():
        return []

    where_clause = ""
    parameters: list[object] = []
    if status:
        where_clause = "WHERE status = ?"
        parameters.append(status)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
                candidate_id,
                run_id,
                created_at,
                display_name,
                symbol,
                market,
                asset_type,
                related_theme,
                why_watch,
                evidence_json,
                risk_flags_json,
                next_actions_json,
                confidence,
                status
            FROM research_candidates
            {where_clause}
            ORDER BY created_at DESC, candidate_id ASC
            """,
            parameters,
        ).fetchall()

    queue = [_research_queue_item_from_row(row) for row in rows]
    if dedupe:
        queue = _dedupe_research_queue(queue)
    return queue[:limit]


def _research_queue_item_from_row(row: tuple[object, ...]) -> ResearchQueueItem:
    return ResearchQueueItem(
        candidate_id=int(str(row[0])),
        run_id=str(row[1]),
        created_at=str(row[2]),
        display_name=str(row[3]),
        symbol=str(row[4]) if row[4] is not None else None,
        market=str(row[5]),
        asset_type=str(row[6]),
        related_theme=str(row[7]),
        why_watch=str(row[8]),
        evidence=json.loads(str(row[9])),
        risk_flags=json.loads(str(row[10])),
        next_actions=json.loads(str(row[11])),
        confidence=str(row[12]),
        status=str(row[13]),
    )


def _dedupe_research_queue(
    queue: list[ResearchQueueItem],
) -> list[ResearchQueueItem]:
    seen: set[str] = set()
    deduped: list[ResearchQueueItem] = []
    for item in queue:
        key = _research_observation_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _research_observation_key(item: ResearchQueueItem) -> str:
    market = item.market.strip().upper()
    if item.symbol:
        return f"{market}:symbol:{item.symbol.strip().upper()}"
    return f"{market}:name:{_compact_text_key(item.display_name)}"


def _compact_text_key(value: str) -> str:
    return "".join(value.casefold().split())


def write_research_packet(
    *,
    output_dir: Path,
    candidate_id: int,
    packet_id: str,
    created_at: str,
    display_name: str,
    symbol: str | None,
    market: str,
    packet: dict[str, object],
    artifact_path: Path,
) -> Path:
    init_research_db(output_dir)
    with sqlite3.connect(research_db_path(output_dir)) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO research_packets (
                packet_id,
                candidate_id,
                created_at,
                display_name,
                symbol,
                market,
                packet_json,
                artifact_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                packet_id,
                candidate_id,
                created_at,
                display_name,
                symbol,
                market,
                json.dumps(packet, ensure_ascii=False),
                str(artifact_path),
            ),
        )
    return research_db_path(output_dir)


def list_research_packets(
    output_dir: Path,
    *,
    limit: int = 20,
) -> list[ResearchPacketRecord]:
    db_path = init_research_db(output_dir)
    if not db_path.exists():
        return []

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                packet_id,
                candidate_id,
                created_at,
                display_name,
                symbol,
                market,
                packet_json,
                artifact_path
            FROM research_packets
            ORDER BY created_at DESC, packet_id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        ResearchPacketRecord(
            packet_id=row[0],
            candidate_id=row[1],
            created_at=row[2],
            display_name=row[3],
            symbol=row[4],
            market=row[5],
            packet=json.loads(row[6]),
            artifact_path=row[7],
        )
        for row in rows
    ]


def write_research_review_record(
    *,
    output_dir: Path,
    review_id: str,
    created_at: str,
    display_name: str,
    symbol: str | None,
    market: str,
    verdict: str,
    verdict_label: str,
    note: str,
    support_count: int,
    risk_count: int,
    missing_count: int,
    review_path: Path,
    verification_path: Path,
    payload: dict[str, object],
) -> Path:
    init_research_db(output_dir)
    with sqlite3.connect(research_db_path(output_dir)) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO research_reviews (
                review_id,
                created_at,
                display_name,
                symbol,
                market,
                verdict,
                verdict_label,
                note,
                support_count,
                risk_count,
                missing_count,
                review_path,
                verification_path,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                created_at,
                display_name,
                symbol,
                market,
                verdict,
                verdict_label,
                note,
                support_count,
                risk_count,
                missing_count,
                str(review_path),
                str(verification_path),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    return research_db_path(output_dir)


def list_research_reviews(
    output_dir: Path,
    *,
    symbol: str | None = None,
    name: str | None = None,
    limit: int = 20,
) -> list[ResearchReviewRecord]:
    db_path = init_research_db(output_dir)
    where_parts: list[str] = []
    parameters: list[object] = []
    if symbol:
        where_parts.append("UPPER(COALESCE(symbol, '')) = ?")
        parameters.append(symbol.strip().upper())
    if name:
        where_parts.append("LOWER(display_name) LIKE ?")
        parameters.append(f"%{name.strip().lower()}%")
    where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    parameters.append(limit)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
                review_id,
                created_at,
                display_name,
                symbol,
                market,
                verdict,
                verdict_label,
                note,
                support_count,
                risk_count,
                missing_count,
                review_path,
                verification_path,
                payload_json
            FROM research_reviews
            {where_clause}
            ORDER BY created_at DESC, review_id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

    return [
        ResearchReviewRecord(
            review_id=row[0],
            created_at=row[1],
            display_name=row[2],
            symbol=row[3],
            market=row[4],
            verdict=row[5],
            verdict_label=row[6],
            note=row[7],
            support_count=row[8],
            risk_count=row[9],
            missing_count=row[10],
            review_path=row[11],
            verification_path=row[12],
            payload=json.loads(row[13]),
        )
        for row in rows
    ]


def write_research_memo_record(
    *,
    output_dir: Path,
    memo_id: str,
    created_at: str,
    display_name: str,
    symbol: str | None,
    market: str,
    confidence: str,
    summary: str,
    support_count: int,
    skeptic_count: int,
    missing_count: int,
    next_step_count: int,
    memo_path: Path,
    verification_path: Path,
    payload: dict[str, object],
) -> Path:
    init_research_db(output_dir)
    with sqlite3.connect(research_db_path(output_dir)) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO research_memos (
                memo_id,
                created_at,
                display_name,
                symbol,
                market,
                confidence,
                summary,
                support_count,
                skeptic_count,
                missing_count,
                next_step_count,
                memo_path,
                verification_path,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memo_id,
                created_at,
                display_name,
                symbol,
                market,
                confidence,
                summary,
                support_count,
                skeptic_count,
                missing_count,
                next_step_count,
                str(memo_path),
                str(verification_path),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    return research_db_path(output_dir)


def list_research_memos(
    output_dir: Path,
    *,
    symbol: str | None = None,
    name: str | None = None,
    limit: int = 20,
) -> list[ResearchMemoRecord]:
    db_path = init_research_db(output_dir)
    where_parts: list[str] = []
    parameters: list[object] = []
    if symbol:
        where_parts.append("UPPER(COALESCE(symbol, '')) = ?")
        parameters.append(symbol.strip().upper())
    if name:
        where_parts.append("LOWER(display_name) LIKE ?")
        parameters.append(f"%{name.strip().lower()}%")
    where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    parameters.append(limit)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
                memo_id,
                created_at,
                display_name,
                symbol,
                market,
                confidence,
                summary,
                support_count,
                skeptic_count,
                missing_count,
                next_step_count,
                memo_path,
                verification_path,
                payload_json
            FROM research_memos
            {where_clause}
            ORDER BY created_at DESC, memo_id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

    return [
        ResearchMemoRecord(
            memo_id=row[0],
            created_at=row[1],
            display_name=row[2],
            symbol=row[3],
            market=row[4],
            confidence=row[5],
            summary=row[6],
            support_count=row[7],
            skeptic_count=row[8],
            missing_count=row[9],
            next_step_count=row[10],
            memo_path=row[11],
            verification_path=row[12],
            payload=json.loads(row[13]),
        )
        for row in rows
    ]
