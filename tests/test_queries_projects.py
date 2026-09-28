import pytest

from agent_lens import queries
from tests.conftest import SEEDED_NOW


def test_list_projects_matches_overview_ranking(seeded_db):
    projects = queries.list_projects(seeded_db, days=30, now=SEEDED_NOW)

    assert [stat.project for stat in projects] == ["alpha", "beta"]


def test_project_detail_has_sessions_daily_and_breakdown(seeded_db):
    detail = queries.project_detail(seeded_db, "alpha", days=30, now=SEEDED_NOW)

    assert detail.project.project == "alpha"
    assert [point.day for point in detail.daily] == ["2026-09-21", "2026-09-22"]
    assert [item.session_id for item in detail.sessions] == ["s1"]
    assert detail.cost_breakdown.total == pytest.approx(0.0048)
    assert detail.cost_breakdown.total == pytest.approx(
        detail.cost_breakdown.uncached_input
        + detail.cost_breakdown.cached_input
        + detail.cost_breakdown.output
    )


def test_unknown_project_detail_is_an_empty_shell(seeded_db):
    detail = queries.project_detail(seeded_db, "nope", days=30, now=SEEDED_NOW)

    assert detail.project.api_call_count == 0
    assert detail.sessions == []
