from datetime import UTC, datetime

from lychee_alphadesk.core.market_calendar import market_session_state


def test_us_market_is_open_during_regular_session() -> None:
    state = market_session_state("US", datetime(2026, 7, 6, 14, 0, tzinfo=UTC))

    assert state.market == "US"
    assert state.state == "open"
    assert state.allows_refresh is True
    assert state.next_refresh_after == datetime(2026, 7, 6, 20, 0, tzinfo=UTC)


def test_hk_market_lunch_break_defers_refresh_until_afternoon_session() -> None:
    state = market_session_state("HK", datetime(2026, 7, 6, 4, 30, tzinfo=UTC))

    assert state.market == "HK"
    assert state.state == "lunch_break"
    assert state.allows_refresh is False
    assert state.next_refresh_after == datetime(2026, 7, 6, 5, 0, tzinfo=UTC)


def test_cn_market_after_close_waits_until_next_trading_day() -> None:
    state = market_session_state("CN", datetime(2026, 7, 6, 8, 0, tzinfo=UTC))

    assert state.market == "CN"
    assert state.state == "closed"
    assert state.allows_refresh is False
    assert state.next_refresh_after == datetime(2026, 7, 7, 1, 30, tzinfo=UTC)
