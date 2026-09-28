import sqlite3
from pathlib import Path

from agent_lens.storage import SCHEMA_VERSION, connect, init_db

V2_PRICING_DDL = """
CREATE TABLE pricing (
    provider                        TEXT NOT NULL,
    model                           TEXT NOT NULL,
    effective_from                  TEXT NOT NULL,
    input_price_per_mtok            REAL NOT NULL,
    cached_input_price_per_mtok     REAL NOT NULL,
    output_price_per_mtok           REAL NOT NULL,
    reasoning_output_price_per_mtok REAL,
    currency                        TEXT NOT NULL DEFAULT 'USD',
    PRIMARY KEY (provider, model, effective_from)
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_schema_version_is_three(tmp_path: Path):
    conn = connect(tmp_path / "lens.db")
    init_db(conn)

    assert SCHEMA_VERSION == 3
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert "time_window" in _columns(conn, "pricing")
    assert "model_provider" in _columns(conn, "api_call_view")
    assert "occurred_at" in _columns(conn, "api_call_view")


def test_v2_pricing_rows_survive_migration(tmp_path: Path):
    conn = connect(tmp_path / "legacy.db")
    conn.executescript(V2_PRICING_DDL)
    conn.execute(
        """
        INSERT INTO pricing (
            provider, model, effective_from,
            input_price_per_mtok, cached_input_price_per_mtok,
            output_price_per_mtok, reasoning_output_price_per_mtok, currency
        ) VALUES ('deepseek', 'deepseek-flash', '2026-01-01T00:00:00+00:00', 0.3, 0.006, 1.2, NULL, 'USD')
        """
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()

    init_db(conn)

    rows = conn.execute(
        "SELECT model, time_window, input_price_per_mtok FROM pricing"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["time_window"] == "any"
    assert rows[0]["input_price_per_mtok"] == 0.3
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_migration_is_idempotent(tmp_path: Path):
    conn = connect(tmp_path / "legacy.db")
    conn.executescript(V2_PRICING_DDL)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()

    init_db(conn)
    init_db(conn)

    assert "time_window" in _columns(conn, "pricing")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
