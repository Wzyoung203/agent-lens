import pytest

from agent_lens import queries
from tests.conftest import SEEDED_NOW


def test_list_sessions_pages_and_totals(seeded_db):
    page = queries.list_sessions(seeded_db, days=30, now=SEEDED_NOW)

    assert page.total == 2
    assert [item.session_id for item in page.items] == ["s1", "s2"]
    assert page.items[0].turn_count == 2
    assert page.items[0].api_call_count == 2
    assert page.items[0].tool_call_count == 2
    assert page.items[0].total_tokens == 3300


def test_list_sessions_filters_by_project(seeded_db):
    page = queries.list_sessions(seeded_db, project="beta", days=30, now=SEEDED_NOW)

    assert [item.session_id for item in page.items] == ["s2"]


def test_list_sessions_search_matches_cwd(seeded_db):
    page = queries.list_sessions(seeded_db, search="alpha", days=30, now=SEEDED_NOW)

    assert [item.session_id for item in page.items] == ["s1"]


def test_list_sessions_respects_offset_and_limit(seeded_db):
    page = queries.list_sessions(seeded_db, days=30, limit=1, offset=1, now=SEEDED_NOW)

    assert page.total == 2
    assert [item.session_id for item in page.items] == ["s2"]


def test_session_detail_returns_turns_in_order(seeded_db):
    detail = queries.session_detail(seeded_db, "s1")

    assert detail is not None
    assert [turn.turn_id for turn in detail.turns] == ["t1", "t2"]
    assert [turn.index for turn in detail.turns] == [1, 2]


def test_turn_summary_computes_context_growth(seeded_db):
    detail = queries.session_detail(seeded_db, "s1")

    first, second = detail.turns
    assert first.context_growth is None
    assert second.context_growth == pytest.approx(2.0)


def test_turn_summary_counts_tool_failures(seeded_db):
    detail = queries.session_detail(seeded_db, "s1")

    assert detail.turns[0].tool_call_count == 1
    assert detail.turns[0].tool_failure_count == 1
    assert detail.turns[1].tool_failure_count == 0


def test_session_detail_cost_breakdown_matches_turn_sum(seeded_db):
    detail = queries.session_detail(seeded_db, "s1")

    assert detail.cost_breakdown.total == pytest.approx(
        sum(turn.cost for turn in detail.turns)
    )


def test_unknown_session_returns_none(seeded_db):
    assert queries.session_detail(seeded_db, "missing") is None


def test_turn_detail_lists_api_calls_and_tools(seeded_db):
    detail = queries.turn_detail(seeded_db, "s1", "t1")

    assert detail is not None
    assert detail.turn.turn_id == "t1"
    assert len(detail.api_calls) == 1
    assert detail.api_calls[0].priced is True
    assert detail.api_calls[0].cost == pytest.approx(0.001)
    assert len(detail.tool_calls) == 1
    assert detail.tool_calls[0].name == "exec_command"
    assert detail.tool_calls[0].duration_ms == 2000


def test_turn_detail_unknown_turn_returns_none(seeded_db):
    assert queries.turn_detail(seeded_db, "s1", "nope") is None
