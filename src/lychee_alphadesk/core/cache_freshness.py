import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lychee_alphadesk.core.market_calendar import (
    MarketSessionState,
    infer_market_from_symbol,
    market_session_state,
)

MARKET_CACHE_TTL_SECONDS = 15 * 60
NEWS_CACHE_TTL_SECONDS = 60 * 60
FINANCIALS_CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class CacheEntry:
    layer: str
    cache_key: str
    provider: str
    artifact_path: Path
    created_at: datetime
    expires_at: datetime
    ttl_seconds: int
    status: str
    row_count: int
    market: str
    session_state: str
    is_final_for_session: bool


@dataclass(frozen=True)
class CacheDecision:
    should_refresh: bool
    reason: str
    entry: CacheEntry | None


def cache_db_path(output_dir: Path) -> Path:
    return output_dir / "research.sqlite3"


def market_cache_key(provider: str, symbols: list[str]) -> str:
    normalized_symbols = ",".join(sorted(symbol.upper() for symbol in symbols))
    return f"market:{provider}:{normalized_symbols}"


def news_cache_key(
    provider: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    query: str | None = None,
) -> str:
    normalized_symbols = _normalized_news_scope(symbols)
    suffix = f":{query.strip()}" if query and query.strip() else ""
    return f"news:{provider}:{normalized_symbols}:{start_date}:{end_date}{suffix}"


def financials_cache_key(symbols: list[str]) -> str:
    normalized_symbols = ",".join(sorted(symbol.upper() for symbol in symbols))
    return f"financials:sec_edgar:{normalized_symbols}"


def evaluate_market_cache(
    *,
    output_dir: Path,
    provider: str,
    symbols: list[str],
    now: datetime | None = None,
    force: bool = False,
) -> CacheDecision:
    current = _ensure_aware(now or datetime.now(UTC))
    cache_key = market_cache_key(provider, symbols)
    entry = get_cache_entry(output_dir, "market", cache_key)
    if force:
        return CacheDecision(True, "用户强制刷新行情缓存。", entry)
    if entry is None:
        return CacheDecision(True, "没有可用的行情缓存。", None)
    if not entry.artifact_path.exists():
        return CacheDecision(True, "行情缓存记录存在，但缓存文件缺失。", entry)
    if entry.row_count == 0:
        return CacheDecision(True, "行情缓存记录为空，需要重新刷新。", entry)

    states = [_state_for_symbol(symbol, current) for symbol in symbols]
    if _needs_post_close_final_refresh(states, entry):
        return CacheDecision(True, "市场刚收盘，需要做一次收盘确认刷新。", entry)
    if _all_lunch_break(states):
        return CacheDecision(False, "市场午休中，行情缓存暂不刷新。", entry)
    if entry.is_final_for_session and _all_outside_regular_session(states):
        return CacheDecision(False, "行情缓存已是本交易时段收盘确认数据，收盘后跳过刷新。", entry)
    if current < entry.expires_at:
        return CacheDecision(False, "行情缓存仍在保质期内，跳过刷新。", entry)
    return CacheDecision(True, "行情缓存已过期。", entry)


def evaluate_news_cache(
    *,
    output_dir: Path,
    provider: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    query: str | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> CacheDecision:
    current = _ensure_aware(now or datetime.now(UTC))
    cache_key = news_cache_key(provider, symbols, start_date, end_date, query)
    entry = get_cache_entry(output_dir, "news", cache_key)
    if force:
        return CacheDecision(True, "用户强制刷新新闻缓存。", entry)
    if entry is None:
        return CacheDecision(True, "没有可用的新闻缓存。", None)
    if not entry.artifact_path.exists():
        return CacheDecision(True, "新闻缓存记录存在，但缓存文件缺失。", entry)
    if current < entry.expires_at:
        return CacheDecision(False, "新闻缓存仍在保质期内，跳过刷新。", entry)
    return CacheDecision(True, "新闻缓存已过期。", entry)


def evaluate_financials_cache(
    *,
    output_dir: Path,
    symbols: list[str],
    now: datetime | None = None,
    force: bool = False,
) -> CacheDecision:
    current = _ensure_aware(now or datetime.now(UTC))
    cache_key = financials_cache_key(symbols)
    entry = get_cache_entry(output_dir, "financials", cache_key)
    if force:
        return CacheDecision(True, "用户强制刷新财务快照缓存。", entry)
    if entry is None:
        return CacheDecision(True, "没有可用的财务快照缓存。", None)
    if not entry.artifact_path.exists():
        return CacheDecision(True, "财务快照缓存记录存在，但缓存文件缺失。", entry)
    if entry.row_count == 0:
        return CacheDecision(True, "财务快照缓存为空，需要重新刷新。", entry)
    if current < entry.expires_at:
        return CacheDecision(False, "财务快照缓存仍在保质期内，跳过刷新。", entry)
    return CacheDecision(True, "财务快照缓存已过期。", entry)


def record_market_cache(
    *,
    output_dir: Path,
    provider: str,
    symbols: list[str],
    artifact_path: Path,
    row_count: int,
    now: datetime | None = None,
    forced: bool = False,
) -> CacheEntry:
    current = _ensure_aware(now or datetime.now(UTC))
    states = [_state_for_symbol(symbol, current) for symbol in symbols]
    expires_at = _market_expires_at(states, current)
    return record_cache_entry(
        output_dir=output_dir,
        layer="market",
        cache_key=market_cache_key(provider, symbols),
        provider=provider,
        artifact_path=artifact_path,
        created_at=current,
        expires_at=expires_at,
        ttl_seconds=MARKET_CACHE_TTL_SECONDS,
        row_count=row_count,
        market=",".join(sorted({state.market for state in states})),
        session_state=",".join(sorted({state.state for state in states})),
        is_final_for_session=_is_final_for_session(states),
        forced=forced,
    )


def record_news_cache(
    *,
    output_dir: Path,
    provider: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    query: str | None = None,
    artifact_path: Path,
    row_count: int,
    now: datetime | None = None,
    forced: bool = False,
    cache_provider: str | None = None,
) -> CacheEntry:
    current = _ensure_aware(now or datetime.now(UTC))
    markets = ",".join(
        sorted({infer_market_from_symbol(symbol) for symbol in symbols})
    ) or "mixed"
    return record_cache_entry(
        output_dir=output_dir,
        layer="news",
        cache_key=news_cache_key(
            cache_provider or provider,
            symbols,
            start_date,
            end_date,
            query,
        ),
        provider=provider,
        artifact_path=artifact_path,
        created_at=current,
        expires_at=current + timedelta(seconds=NEWS_CACHE_TTL_SECONDS),
        ttl_seconds=NEWS_CACHE_TTL_SECONDS,
        row_count=row_count,
        market=markets,
        session_state="ttl",
        is_final_for_session=False,
        forced=forced,
    )


def record_financials_cache(
    *,
    output_dir: Path,
    symbols: list[str],
    artifact_path: Path,
    row_count: int,
    now: datetime | None = None,
    forced: bool = False,
) -> CacheEntry:
    current = _ensure_aware(now or datetime.now(UTC))
    markets = ",".join(
        sorted({infer_market_from_symbol(symbol) for symbol in symbols})
    ) or "US"
    return record_cache_entry(
        output_dir=output_dir,
        layer="financials",
        cache_key=financials_cache_key(symbols),
        provider="sec_edgar",
        artifact_path=artifact_path,
        created_at=current,
        expires_at=current + timedelta(seconds=FINANCIALS_CACHE_TTL_SECONDS),
        ttl_seconds=FINANCIALS_CACHE_TTL_SECONDS,
        row_count=row_count,
        market=markets,
        session_state="ttl",
        is_final_for_session=False,
        forced=forced,
    )


def _normalized_news_scope(symbols: list[str]) -> str:
    normalized_symbols = ",".join(sorted(symbol.upper() for symbol in symbols))
    return normalized_symbols or "MARKET"


def record_cache_entry(
    *,
    output_dir: Path,
    layer: str,
    cache_key: str,
    provider: str,
    artifact_path: Path,
    created_at: datetime,
    expires_at: datetime,
    ttl_seconds: int,
    row_count: int,
    market: str,
    session_state: str,
    is_final_for_session: bool,
    status: str = "fresh",
    last_error: str = "",
    forced: bool = False,
) -> CacheEntry:
    output_dir.mkdir(parents=True, exist_ok=True)
    _init_cache_db(output_dir)
    normalized_created_at = _ensure_aware(created_at)
    normalized_expires_at = _ensure_aware(expires_at)
    with sqlite3.connect(cache_db_path(output_dir)) as connection:
        connection.execute(
            """
            INSERT INTO cache_entries (
                layer,
                cache_key,
                provider,
                artifact_path,
                created_at,
                expires_at,
                ttl_seconds,
                status,
                row_count,
                last_error,
                forced,
                market,
                session_state,
                is_final_for_session
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(layer, cache_key) DO UPDATE SET
                provider = excluded.provider,
                artifact_path = excluded.artifact_path,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at,
                ttl_seconds = excluded.ttl_seconds,
                status = excluded.status,
                row_count = excluded.row_count,
                last_error = excluded.last_error,
                forced = excluded.forced,
                market = excluded.market,
                session_state = excluded.session_state,
                is_final_for_session = excluded.is_final_for_session
            """,
            (
                layer,
                cache_key,
                provider,
                str(artifact_path),
                normalized_created_at.isoformat(timespec="seconds"),
                normalized_expires_at.isoformat(timespec="seconds"),
                ttl_seconds,
                status,
                row_count,
                last_error,
                int(forced),
                market,
                session_state,
                int(is_final_for_session),
            ),
        )
    return CacheEntry(
        layer=layer,
        cache_key=cache_key,
        provider=provider,
        artifact_path=artifact_path,
        created_at=normalized_created_at,
        expires_at=normalized_expires_at,
        ttl_seconds=ttl_seconds,
        status=status,
        row_count=row_count,
        market=market,
        session_state=session_state,
        is_final_for_session=is_final_for_session,
    )


def get_cache_entry(output_dir: Path, layer: str, cache_key: str) -> CacheEntry | None:
    db_path = cache_db_path(output_dir)
    if not db_path.exists():
        return None
    _init_cache_db(output_dir)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                layer,
                cache_key,
                provider,
                artifact_path,
                created_at,
                expires_at,
                ttl_seconds,
                status,
                row_count,
                market,
                session_state,
                is_final_for_session
            FROM cache_entries
            WHERE layer = ? AND cache_key = ?
            """,
            (layer, cache_key),
        ).fetchone()
    if row is None:
        return None
    return _cache_entry_from_row(row)


def list_cache_entries(output_dir: Path, layer: str | None = None) -> list[CacheEntry]:
    db_path = cache_db_path(output_dir)
    if not db_path.exists():
        return []
    _init_cache_db(output_dir)
    where_clause = ""
    parameters: tuple[str, ...] = ()
    if layer:
        where_clause = "WHERE layer = ?"
        parameters = (layer,)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
                layer,
                cache_key,
                provider,
                artifact_path,
                created_at,
                expires_at,
                ttl_seconds,
                status,
                row_count,
                market,
                session_state,
                is_final_for_session
            FROM cache_entries
            {where_clause}
            ORDER BY layer, cache_key
            """,
            parameters,
        ).fetchall()
    return [_cache_entry_from_row(row) for row in rows]


def cache_entry_status(entry: CacheEntry, now: datetime | None = None) -> str:
    current = _ensure_aware(now or datetime.now(UTC))
    if entry.status != "fresh":
        return entry.status
    if current >= entry.expires_at:
        return "expired"
    if entry.is_final_for_session:
        return "final_for_session"
    return "fresh"


def _init_cache_db(output_dir: Path) -> None:
    with sqlite3.connect(cache_db_path(output_dir)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                layer TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                provider TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                ttl_seconds INTEGER NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                last_error TEXT NOT NULL,
                forced INTEGER NOT NULL,
                market TEXT NOT NULL,
                session_state TEXT NOT NULL,
                is_final_for_session INTEGER NOT NULL,
                PRIMARY KEY (layer, cache_key)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cache_entries_layer_expires
            ON cache_entries(layer, expires_at)
            """
        )


def _cache_entry_from_row(row: tuple[object, ...]) -> CacheEntry:
    return CacheEntry(
        layer=str(row[0]),
        cache_key=str(row[1]),
        provider=str(row[2]),
        artifact_path=Path(str(row[3])),
        created_at=_parse_datetime(str(row[4])),
        expires_at=_parse_datetime(str(row[5])),
        ttl_seconds=int(str(row[6])),
        status=str(row[7]),
        row_count=int(str(row[8])),
        market=str(row[9]),
        session_state=str(row[10]),
        is_final_for_session=bool(int(str(row[11]))),
    )


def _state_for_symbol(symbol: str, now: datetime) -> MarketSessionState:
    return market_session_state(infer_market_from_symbol(symbol), now)


def _needs_post_close_final_refresh(
    states: list[MarketSessionState],
    entry: CacheEntry,
) -> bool:
    return any(state.state == "post_close_refresh" for state in states) and not (
        entry.is_final_for_session
    )


def _all_lunch_break(states: list[MarketSessionState]) -> bool:
    return bool(states) and all(state.state == "lunch_break" for state in states)


def _all_outside_regular_session(states: list[MarketSessionState]) -> bool:
    outside_states = {"pre_open", "closed", "weekend"}
    return bool(states) and all(state.state in outside_states for state in states)


def _market_expires_at(states: list[MarketSessionState], now: datetime) -> datetime:
    if any(state.state == "open" for state in states):
        next_boundary = min(state.next_refresh_after for state in states)
        return min(now + timedelta(seconds=MARKET_CACHE_TTL_SECONDS), next_boundary)
    return min(state.next_refresh_after for state in states)


def _is_final_for_session(states: list[MarketSessionState]) -> bool:
    final_states = {"pre_open", "post_close_refresh", "closed", "weekend"}
    return bool(states) and all(state.state in final_states for state in states)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _ensure_aware(parsed)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
