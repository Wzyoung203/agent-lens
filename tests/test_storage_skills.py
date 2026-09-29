from agent_lens import storage
from agent_lens.skills import SkillHit


def _hit() -> SkillHit:
    return SkillHit(
        file_path="f.jsonl",
        ordinal=7,
        skill_name="brainstorming",
        skill_path="/x/skills/brainstorming/SKILL.md",
        tool_name="exec_command",
    )


def test_write_skill_hits_is_idempotent(lens_db):
    assert storage.write_skill_hits(lens_db, [_hit()]) == 1
    assert storage.write_skill_hits(lens_db, [_hit()]) == 1
    rows = lens_db.execute("SELECT skill_name, tool_name FROM skill_hits").fetchall()
    assert len(rows) == 1
    assert rows[0]["skill_name"] == "brainstorming"
    assert rows[0]["tool_name"] == "exec_command"


def test_write_skill_hits_handles_empty_input(lens_db):
    assert storage.write_skill_hits(lens_db, []) == 0


def test_skill_hits_table_survives_a_v4_database(tmp_path):
    conn = storage.connect(tmp_path / "old.db")
    conn.executescript(storage.SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    storage.init_db(conn)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "skill_hits" in tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
