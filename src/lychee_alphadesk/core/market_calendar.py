from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

MarketCode = Literal["US", "HK", "CN"]
SessionState = Literal[
    "pre_open",
    "open",
    "lunch_break",
    "post_close_refresh",
    "closed",
    "weekend",
]

POST_CLOSE_REFRESH_WINDOW = timedelta(minutes=30)


@dataclass(frozen=True)
class MarketSessionState:
    market: MarketCode
    state: SessionState
    is_trading_day: bool
    allows_refresh: bool
    next_refresh_after: datetime


def market_session_state(market: str, now: datetime | None = None) -> MarketSessionState:
    normalized_market = _normalize_market(market)
    current = _ensure_aware(now or datetime.now(UTC))
    if normalized_market == "US":
        return _single_session_state(
            market="US",
            now=current,
            timezone=ZoneInfo("America/New_York"),
            open_time=time(9, 30),
            close_time=time(16, 0),
        )
    if normalized_market == "HK":
        return _split_session_state(
            market="HK",
            now=current,
            timezone=ZoneInfo("Asia/Hong_Kong"),
            morning_open=time(9, 30),
            morning_close=time(12, 0),
            afternoon_open=time(13, 0),
            afternoon_close=time(16, 0),
        )
    return _split_session_state(
        market="CN",
        now=current,
        timezone=ZoneInfo("Asia/Shanghai"),
        morning_open=time(9, 30),
        morning_close=time(11, 30),
        afternoon_open=time(13, 0),
        afternoon_close=time(15, 0),
    )


def infer_market_from_symbol(symbol: str) -> MarketCode:
    normalized = symbol.upper()
    if normalized.endswith(".HK"):
        return "HK"
    if normalized.endswith((".SH", ".SZ", ".SS")):
        return "CN"
    return "US"


def _single_session_state(
    *,
    market: MarketCode,
    now: datetime,
    timezone: ZoneInfo,
    open_time: time,
    close_time: time,
) -> MarketSessionState:
    local_now = now.astimezone(timezone)
    if _is_weekend(local_now):
        next_open = _next_weekday_at(local_now, open_time, timezone)
        return _state(market, "weekend", False, False, next_open)

    opened_at = _local_datetime(local_now, open_time, timezone)
    closed_at = _local_datetime(local_now, close_time, timezone)
    post_close_until = closed_at + POST_CLOSE_REFRESH_WINDOW
    if local_now < opened_at:
        return _state(market, "pre_open", True, False, opened_at.astimezone(UTC))
    if opened_at <= local_now < closed_at:
        return _state(market, "open", True, True, closed_at.astimezone(UTC))
    if closed_at <= local_now < post_close_until:
        return _state(
            market,
            "post_close_refresh",
            True,
            True,
            post_close_until.astimezone(UTC),
        )
    next_open = _next_weekday_at(local_now, open_time, timezone)
    return _state(market, "closed", True, False, next_open)


def _split_session_state(
    *,
    market: MarketCode,
    now: datetime,
    timezone: ZoneInfo,
    morning_open: time,
    morning_close: time,
    afternoon_open: time,
    afternoon_close: time,
) -> MarketSessionState:
    local_now = now.astimezone(timezone)
    if _is_weekend(local_now):
        next_open = _next_weekday_at(local_now, morning_open, timezone)
        return _state(market, "weekend", False, False, next_open)

    morning_opened_at = _local_datetime(local_now, morning_open, timezone)
    morning_closed_at = _local_datetime(local_now, morning_close, timezone)
    afternoon_opened_at = _local_datetime(local_now, afternoon_open, timezone)
    afternoon_closed_at = _local_datetime(local_now, afternoon_close, timezone)
    post_close_until = afternoon_closed_at + POST_CLOSE_REFRESH_WINDOW

    if local_now < morning_opened_at:
        return _state(market, "pre_open", True, False, morning_opened_at.astimezone(UTC))
    if morning_opened_at <= local_now < morning_closed_at:
        return _state(market, "open", True, True, morning_closed_at.astimezone(UTC))
    if morning_closed_at <= local_now < afternoon_opened_at:
        return _state(
            market,
            "lunch_break",
            True,
            False,
            afternoon_opened_at.astimezone(UTC),
        )
    if afternoon_opened_at <= local_now < afternoon_closed_at:
        return _state(market, "open", True, True, afternoon_closed_at.astimezone(UTC))
    if afternoon_closed_at <= local_now < post_close_until:
        return _state(
            market,
            "post_close_refresh",
            True,
            True,
            post_close_until.astimezone(UTC),
        )
    next_open = _next_weekday_at(local_now, morning_open, timezone)
    return _state(market, "closed", True, False, next_open)


def _state(
    market: MarketCode,
    state: SessionState,
    is_trading_day: bool,
    allows_refresh: bool,
    next_refresh_after: datetime,
) -> MarketSessionState:
    return MarketSessionState(
        market=market,
        state=state,
        is_trading_day=is_trading_day,
        allows_refresh=allows_refresh,
        next_refresh_after=next_refresh_after.astimezone(UTC),
    )


def _normalize_market(market: str) -> MarketCode:
    normalized = market.strip().upper()
    if normalized in {"US", "HK", "CN"}:
        return normalized  # type: ignore[return-value]
    raise ValueError(f"不支持的市场: {market}")


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _is_weekend(local_now: datetime) -> bool:
    return local_now.weekday() >= 5


def _local_datetime(local_now: datetime, value: time, timezone: ZoneInfo) -> datetime:
    return datetime.combine(local_now.date(), value, timezone)


def _next_weekday_at(local_now: datetime, value: time, timezone: ZoneInfo) -> datetime:
    candidate = _local_datetime(local_now + timedelta(days=1), value, timezone)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)
