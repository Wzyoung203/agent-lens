from agent_lens.storage import (
    UNCLASSIFIED_PROJECT,
    assign_project,
    refresh_session_projects,
    resolve_project,
    write_parsed_session,
)
from tests.test_storage_write import minimal_session


def test_longest_prefix_wins_and_falls_back(lens_db):
    assign_project(lens_db, "/Users/someone/project", "agent-lens")
    assign_project(lens_db, "/Users/someone/project/vendor", "vendor-lib")

    assert resolve_project(lens_db, "/Users/someone/project/app") == "agent-lens"
    assert resolve_project(lens_db, "/Users/someone/project/vendor/lib") == "vendor-lib"
    assert resolve_project(lens_db, "/Users/someone/other") == UNCLASSIFIED_PROJECT
    assert resolve_project(lens_db, None) == UNCLASSIFIED_PROJECT


def test_prefix_matching_ignores_like_wildcards(lens_db):
    assign_project(lens_db, "/Users/someone/pro_ject", "underscore-project")

    assert resolve_project(lens_db, "/Users/someone/proXject/app") == UNCLASSIFIED_PROJECT


def test_refresh_projects_repairs_existing_sessions(lens_db):
    write_parsed_session(lens_db, minimal_session())
    before = lens_db.execute("SELECT project FROM sessions").fetchone()[0]

    assign_project(lens_db, "/Users/someone/project", "agent-lens")
    changed = refresh_session_projects(lens_db)

    assert before == UNCLASSIFIED_PROJECT
    assert changed == 1
    assert lens_db.execute("SELECT project FROM sessions").fetchone()[0] == "agent-lens"
