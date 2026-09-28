import pytest

from agent_lens import queries
from tests.conftest import SEEDED_NOW


def test_overview_cards_totals(seeded_db):
    result = queries.overview(seeded_db, days=30, now=SEEDED_NOW)

    cards = result.cards
    assert cards.api_call_count == 3
    assert cards.session_count == 2
    assert cards.turn_count == 3
    assert cards.tool_call_count == 3
    assert cards.input_tokens == 3500
    assert cards.output_tokens == 350
    assert cards.total_tokens == 3850
    assert cards.cached_input_tokens == 1400
    assert cards.cache_hit_rate == pytest.approx(1400 / 3500)
    assert cards.unpriced_calls == 0
    assert cards.currency == "USD"


def test_overview_cost_uses_peak_and_idle_prices(seeded_db):
    """t1 空闲、t2 高峰、t3 空闲：时段不同，单价翻倍。"""
    result = queries.overview(seeded_db, days=30, now=SEEDED_NOW)

    assert result.cards.total_cost == pytest.approx(0.0054)


def test_overview_tool_failure_rate(seeded_db):
    result = queries.overview(seeded_db, days=30, now=SEEDED_NOW)

    assert result.cards.tool_failure_rate == pytest.approx(1 / 3)


def test_overview_daily_buckets_by_day(seeded_db):
    result = queries.overview(seeded_db, days=30, now=SEEDED_NOW)

    days = {point.day: point for point in result.daily}
    assert sorted(days) == ["2026-09-21", "2026-09-22", "2026-09-23"]
    assert days["2026-09-21"].api_calls == 1
    assert days["2026-09-21"].cost == pytest.approx(0.001)
    assert days["2026-09-22"].api_calls == 1
    assert days["2026-09-22"].cost == pytest.approx(0.0038)
    assert days["2026-09-23"].cost == pytest.approx(0.0006)


def test_overview_projects_are_ranked_by_cost(seeded_db):
    result = queries.overview(seeded_db, days=30, now=SEEDED_NOW)

    assert [stat.project for stat in result.projects] == ["alpha", "beta"]
    alpha = result.projects[0]
    assert alpha.api_call_count == 2
    assert alpha.session_count == 1
    assert alpha.total_tokens == 3300
    assert alpha.tool_failure_rate == pytest.approx(0.5)


def test_overview_window_excludes_older_calls(seeded_db):
    """窗口是「含当天、向前推 days 天」，days=1 只覆盖 09-25 当天。"""
    result = queries.overview(seeded_db, days=1, now=SEEDED_NOW)

    assert result.cards.api_call_count == 0
    assert result.cards.total_cost == 0.0


def test_overview_project_filter(seeded_db):
    result = queries.overview(seeded_db, days=30, project="beta", now=SEEDED_NOW)

    assert result.cards.api_call_count == 1
    assert result.cards.session_count == 1


def test_range_info_is_isoformat_utc(seeded_db):
    result = queries.overview(seeded_db, days=30, now=SEEDED_NOW)

    assert result.range.days == 30
    assert result.range.start.endswith("+00:00")
    assert result.range.start.startswith("2026-08-27")


def test_reasoning_tokens_are_not_double_counted(seeded_db):
    """output 已包含 reasoning，total_tokens 只能是 input + output。"""
    result = queries.overview(seeded_db, days=30, now=SEEDED_NOW)

    assert result.cards.total_tokens == result.cards.input_tokens + result.cards.output_tokens
