from pathlib import Path

from agent_lens.storage import DB_PATH_ENV, connect, init_db, resolve_db_path


def test_schema_is_idempotent_and_versioned(tmp_path: Path):
    conn = connect(tmp_path / "lens.db")
    init_db(conn)
    init_db(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }

    assert {
        "sessions",
        "turns",
        "api_calls",
        "tool_calls",
        "tool_results",
        "items",
        "events",
        "pricing",
        "projects",
        "project_paths",
        "ingest_state",
    } <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_db_path_precedence(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "from_env.db"))

    assert resolve_db_path() == tmp_path / "from_env.db"
    assert resolve_db_path(tmp_path / "explicit.db") == tmp_path / "explicit.db"
