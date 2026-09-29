from agent_lens import context, storage


def _sample() -> context.CallBreakdown:
    return context.CallBreakdown(
        file_path="f.jsonl",
        ordinal=3,
        session_id="s1",
        turn_id="t1",
        input_tokens=1000,
        blocks={
            context.BLOCK_FIXED_INSTRUCTIONS: context.BlockChars(other=100),
            context.BLOCK_SKILL_CATALOG: context.BlockChars(other=10),
            context.BLOCK_HISTORY: context.BlockChars(cjk=50, other=50),
            context.BLOCK_TOOL_OUTPUT: context.BlockChars(other=100),
        },
        unattributed_tokens=895.0,
    )


def test_schema_version_is_four(lens_db):
    assert storage.SCHEMA_VERSION >= 4
    assert lens_db.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION


def test_write_context_breakdown_is_idempotent(lens_db):
    assert storage.write_context_breakdown(lens_db, [_sample()]) == 5
    assert storage.write_context_breakdown(lens_db, [_sample()]) == 5
    rows = lens_db.execute(
        "SELECT block, attributed_tokens FROM context_breakdown ORDER BY block"
    ).fetchall()
    assert len(rows) == 5
    assert round(sum(row["attributed_tokens"] for row in rows), 6) == 1000.0


def test_unattributed_row_carries_no_chars(lens_db):
    storage.write_context_breakdown(lens_db, [_sample()])
    row = lens_db.execute(
        "SELECT cjk_chars, other_chars, attributed_tokens FROM context_breakdown"
        " WHERE block = ?",
        (context.BLOCK_UNATTRIBUTED,),
    ).fetchone()
    assert (row["cjk_chars"], row["other_chars"]) == (0, 0)
    # 四块估算 = 30 + 3 + 45 + 30 = 108，残差 1000 - 108 = 892
    assert row["attributed_tokens"] == 892.0


def test_migrating_a_v3_database_adds_the_table(tmp_path):
    conn = storage.connect(tmp_path / "old.db")
    conn.executescript(storage.SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    storage.init_db(conn)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "context_breakdown" in tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
