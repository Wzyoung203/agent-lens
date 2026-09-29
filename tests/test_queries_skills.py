import pytest

from agent_lens import queries, storage
from agent_lens.skills import SkillHit

ALPHA_FILE = "/sessions/rollout-alpha.jsonl"


@pytest.fixture
def skills_db(seeded_db):
    """seeded_db 里 alpha 的 t1 有一条 ordinal=3 的 exec_command 工具调用。"""
    storage.write_skill_hits(
        seeded_db,
        [
            SkillHit(
                file_path=ALPHA_FILE,
                ordinal=3,
                skill_name="brainstorming",
                skill_path="/x/skills/brainstorming/SKILL.md",
                tool_name="exec_command",
            )
        ],
    )
    return seeded_db


def test_skill_stats_groups_by_name_and_joins_session(skills_db):
    result = queries.skill_stats(skills_db, days=3650)
    assert result.total_loads == 1
    (stat,) = result.skills
    assert stat.skill_name == "brainstorming"
    assert stat.loads == 1
    assert stat.session_count == 1
    assert stat.tool_names == ["exec_command"]
    assert stat.first_seen is not None


def test_skill_stats_filters_by_project(skills_db):
    assert queries.skill_stats(skills_db, days=3650, project="alpha").total_loads == 1
    assert queries.skill_stats(skills_db, days=3650, project="beta").total_loads == 0


def test_skill_endpoint_returns_rows(skills_db):
    from fastapi.testclient import TestClient

    from agent_lens.api.app import create_app

    db_file = skills_db.execute("PRAGMA database_list").fetchone()[2]
    with TestClient(create_app(db_file)) as client:
        response = client.get("/api/skills?days=3650")
    assert response.status_code == 200
    body = response.json()
    assert body["total_loads"] == 1
    assert body["skills"][0]["skill_name"] == "brainstorming"
