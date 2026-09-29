from agent_lens.storage import SCHEMA_VERSION, counts, init_db


def test_schema_version_is_current(lens_db):
    """report_queue 自 v3 起存在；版本号随迁移递增，这里只要求不低于 v3。"""
    assert SCHEMA_VERSION >= 3
    assert lens_db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_report_queue_table_exists(lens_db):
    row = lens_db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'report_queue'"
    ).fetchone()

    assert row is not None
    assert counts(lens_db)["report_queue"] == 0


def test_report_queue_has_expected_columns(lens_db):
    columns = {
        row["name"] for row in lens_db.execute("PRAGMA table_info(report_queue)").fetchall()
    }

    assert columns == {
        "report_id",
        "session_id",
        "turn_id",
        "kind",
        "granularity",
        "payload",
        "status",
        "attempts",
        "last_error",
        "next_attempt_at",
        "created_at",
        "updated_at",
    }


def test_init_db_is_idempotent_with_queue(lens_db):
    init_db(lens_db)

    assert counts(lens_db)["report_queue"] == 0
