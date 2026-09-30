"""P3.1：TTFT 从 task_complete 落到 turns 表，并能在 v5 旧库上原地补列。"""

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_lens.models import (
    ApiCallRecord,
    ParsedSession,
    TokenUsage,
    TurnRecord,
)
from agent_lens.parser import parse_line, parse_session_file
from agent_lens.storage import SCHEMA_VERSION, connect, init_db, write_parsed_session


def _task_complete_line(tmp_path) -> str:
    return (
        '{"type":"event_msg","payload":{"type":"task_complete","turn_id":"t1",'
        '"started_at":1790446563,"completed_at":1790446565,"duration_ms":1275,'
        '"time_to_first_token_ms":1191,"last_agent_message":"hi"}}'
    )


def test_parser_reads_time_to_first_token(tmp_path):
    result = parse_line(_task_complete_line(tmp_path), tmp_path / "a.jsonl", 0)

    assert result.turn_completed is not None
    assert result.turn_completed.time_to_first_token_ms == 1191


def test_whole_file_parse_keeps_ttft(tmp_path):
    """整文件解析走的是 _upsert_turn 的字段级合并，别在那一层把新字段吃掉。"""
    path = tmp_path / "session.jsonl"
    path.write_text(_task_complete_line(tmp_path) + "\n", encoding="utf-8")

    parsed = parse_session_file(path)

    assert len(parsed.turns) == 1
    assert parsed.turns[0].time_to_first_token_ms == 1191


def _session(ttft: int | None) -> ParsedSession:
    started = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
    return ParsedSession(
        session_id="s-latency",
        file_path="latency.jsonl",
        turns=[
            TurnRecord(
                turn_id="t1",
                started_at=started,
                completed_at=started + timedelta(seconds=4),
                duration_ms=4000,
                time_to_first_token_ms=ttft,
            )
        ],
        api_calls=[
            ApiCallRecord(
                file_path="latency.jsonl",
                ordinal=1,
                session_id="s-latency",
                turn_id="t1",
                response_id="r1",
                timestamp=started,
                usage=TokenUsage(input_tokens=100, cached_input_tokens=0, output_tokens=50),
            )
        ],
    )


def test_turns_table_stores_ttft(lens_db):
    write_parsed_session(lens_db, _session(1191))

    assert SCHEMA_VERSION == 6
    assert lens_db.execute("SELECT time_to_first_token_ms FROM turns").fetchone()[0] == 1191


def test_rewriting_without_ttft_keeps_the_stored_value(lens_db):
    write_parsed_session(lens_db, _session(1191))
    write_parsed_session(lens_db, _session(None))

    assert lens_db.execute("SELECT time_to_first_token_ms FROM turns").fetchone()[0] == 1191


def test_v5_database_gains_the_column_in_place(tmp_path):
    """旧库没有这一列时，init_db 要原地补上，且既有行不丢。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, cli_version TEXT, cwd TEXT, model_provider TEXT,
            base_instructions_chars INTEGER NOT NULL DEFAULT 0, recorded_at TEXT,
            project TEXT NOT NULL DEFAULT '未归类',
            first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE turns (
            session_id TEXT NOT NULL, turn_id TEXT NOT NULL, cwd TEXT, model TEXT, effort TEXT,
            started_at TEXT, completed_at TEXT, duration_ms INTEGER, aborted_reason TEXT,
            updated_at TEXT NOT NULL, PRIMARY KEY (session_id, turn_id)
        );
        INSERT INTO sessions VALUES
            ('s1', NULL, '/tmp', 'deepseek', 0, NULL, '未归类', '2026-09-24T00:00:00+00:00',
             '2026-09-24T00:00:00+00:00');
        INSERT INTO turns VALUES
            ('s1', 't1', '/tmp', 'm', 'high', '2026-09-24T10:00:00+00:00',
             '2026-09-24T10:00:04+00:00', 4000, NULL, '2026-09-24T10:00:04+00:00');
        PRAGMA user_version = 5;
        """
    )
    conn.commit()
    conn.close()

    upgraded = connect(path)
    init_db(upgraded)

    row = upgraded.execute("SELECT turn_id, duration_ms, time_to_first_token_ms FROM turns").fetchone()
    assert row["turn_id"] == "t1"
    assert row["duration_ms"] == 4000
    assert row["time_to_first_token_ms"] is None
    upgraded.close()
