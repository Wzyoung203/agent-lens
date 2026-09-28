import pytest

from agent_lens import queries
from tests.conftest import SEEDED_NOW


def test_tool_stats_groups_by_name_with_failure_rate(seeded_db):
    result = queries.tool_stats(seeded_db, days=30, now=SEEDED_NOW)

    stats = {item.name: item for item in result.stats}
    assert stats["exec_command"].call_count == 2
    assert stats["exec_command"].failure_count == 1
    assert stats["exec_command"].failure_rate == pytest.approx(0.5)
    assert stats["apply_patch"].failure_rate == 0.0


def test_tool_stats_reports_duration_from_wall_time(seeded_db):
    result = queries.tool_stats(seeded_db, days=30, now=SEEDED_NOW)

    stats = {item.name: item for item in result.stats}
    assert stats["exec_command"].avg_duration_ms == pytest.approx(2000.0)
    assert stats["exec_command"].p95_duration_ms == pytest.approx(2000.0)


def test_tools_response_lists_failures(seeded_db):
    result = queries.tool_stats(seeded_db, days=30, now=SEEDED_NOW)

    assert [item.name for item in result.failures] == ["exec_command"]
    assert result.failures[0].exit_code == 1
    assert result.failures[0].project == "alpha"


def test_tools_response_breaks_failure_rate_down_by_project(seeded_db):
    result = queries.tool_stats(seeded_db, days=30, now=SEEDED_NOW)

    by_project = {item.project: item for item in result.by_project}
    assert by_project["alpha"].failure_rate == pytest.approx(0.5)
    assert by_project["beta"].failure_rate == 0.0


def test_tool_failures_pagination(seeded_db):
    page = queries.tool_failures(seeded_db, days=30, limit=1, offset=0, now=SEEDED_NOW)

    assert page.total == 1
    assert len(page.items) == 1


def test_failure_cost_counts_calls_after_the_first_failure(seeded_db):
    """alpha 的第一个失败工具调用之后有两条 api_call（同文件），都算重试。"""
    breakdown = queries.failure_cost(seeded_db, days=30, now=SEEDED_NOW)

    assert breakdown.total == pytest.approx(0.0048)


def test_failure_cost_is_zero_when_nothing_failed(seeded_db):
    breakdown = queries.failure_cost(seeded_db, days=30, project="beta", now=SEEDED_NOW)

    assert breakdown.total == 0.0
