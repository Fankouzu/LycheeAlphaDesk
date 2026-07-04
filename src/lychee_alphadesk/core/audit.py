import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class AuditRecord:
    report_id: str
    created_at: str
    mode: str
    report_path: str
    providers: list[str]
    warnings: list[str]
    errors: list[str]


def audit_db_path(output_dir: Path) -> Path:
    return output_dir / "audit.sqlite3"


def init_audit_db(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = audit_db_path(output_dir)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_records (
                report_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                report_path TEXT NOT NULL,
                providers_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                errors_json TEXT NOT NULL
            )
            """
        )
    return db_path


def write_audit_record(
    output_dir: Path,
    *,
    report_id: str,
    mode: str,
    report_path: Path,
    providers: list[str],
    warnings: list[str],
    errors: list[str],
) -> AuditRecord:
    init_audit_db(output_dir)
    record = AuditRecord(
        report_id=report_id,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        mode=mode,
        report_path=str(report_path),
        providers=providers,
        warnings=warnings,
        errors=errors,
    )
    with sqlite3.connect(audit_db_path(output_dir)) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO audit_records (
                report_id,
                created_at,
                mode,
                report_path,
                providers_json,
                warnings_json,
                errors_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.report_id,
                record.created_at,
                record.mode,
                record.report_path,
                json.dumps(record.providers),
                json.dumps(record.warnings),
                json.dumps(record.errors),
            ),
        )
    return record


def list_audit_records(output_dir: Path) -> list[AuditRecord]:
    db_path = audit_db_path(output_dir)
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                report_id,
                created_at,
                mode,
                report_path,
                providers_json,
                warnings_json,
                errors_json
            FROM audit_records
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        AuditRecord(
            report_id=row[0],
            created_at=row[1],
            mode=row[2],
            report_path=row[3],
            providers=json.loads(row[4]),
            warnings=json.loads(row[5]),
            errors=json.loads(row[6]),
        )
        for row in rows
    ]
