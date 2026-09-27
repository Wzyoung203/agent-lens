import pytest

from agent_lens.models import (
    ApiCallRecord,
    ItemCompletedRecord,
    ParsedSession,
    TokenUsage,
    ToolCallRecord,
    ToolResultRecord,
    TurnContextRecord,
    TurnRecord,
    to_utc,
)
from agent_lens.storage import UNCLASSIFIED_PROJECT, counts, write_parsed_session


def minimal_session(file_path: str = "f.jsonl") -> ParsedSession:
    return ParsedSession(
        session_id="s1",
        file_path=file_path,
        cli_version="0.157.1",
        cwd="/Users/someone/project",
        model_provider="deepseek",
        base_instructions_chars=17766,
        recorded_at=to_utc("2026-09-27T10:00:00Z"),
        turn_contexts=[
            TurnContextRecord(turn_id="t1", model="deepseek-flash", effort="high")
        ],
        turns=[TurnRecord(turn_id="t1", duration_ms=3000, aborted_reason=None)],
        api_calls=[
            ApiCallRecord(
                file_path=file_path,
                ordinal=16,
                session_id="s1",
                turn_id="t1",
                response_id="r1",
                timestamp=to_utc("2026-09-27T10:00:01Z"),
                usage=TokenUsage(input_tokens=100, cached_input_tokens=50, output_tokens=10),
                thread_input_tokens_cumulative=100,
            )
        ],
        tool_calls=[
            ToolCallRecord(
                file_path=file_path,
                ordinal=17,
                call_id="c1",
                name="exec_command",
                arguments_raw='{"cmd": "SECRET_ARGUMENT"}',
            )
        ],
        tool_results=[
            ToolResultRecord(
                file_path=file_path,
                ordinal=18,
                call_id="c1",
                output_text="SECRET_OUTPUT",
                exit_code=1,
                wall_time_seconds=2.5,
                success=False,
            )
        ],
        items=[
            ItemCompletedRecord(
                file_path=file_path,
                ordinal=19,
                item_type="CommandExecution",
                started_at_ms=1000,
                completed_at_ms=4500,
            )
        ],
        events=[
            {
                "ordinal": 20,
                "event_type": "context_compacted",
                "payload": {"note": "SECRET_PAYLOAD_VALUE"},
            }
        ],
    )


def test_write_then_rewrite_is_idempotent(lens_db):
    parsed = minimal_session()

    first = write_parsed_session(lens_db, parsed)
    after_first = counts(lens_db)
    second = write_parsed_session(lens_db, parsed)
    after_second = counts(lens_db)

    assert first.inserted["api_calls"] == 1
    assert second.inserted["api_calls"] == 0
    assert second.skipped["api_calls"] == 1
    assert after_first == after_second
    assert after_first["api_calls"] == 1
    assert after_first["tool_calls"] == 1
    assert after_first["tool_results"] == 1
    assert after_first["items"] == 1
    assert after_first["events"] == 1
    assert after_first["turns"] == 1


def test_api_call_view_exposes_cache_hit_rate(lens_db):
    write_parsed_session(lens_db, minimal_session())

    row = lens_db.execute("SELECT * FROM api_call_view").fetchone()

    assert row["project"] == UNCLASSIFIED_PROJECT
    assert row["model"] == "deepseek-flash"
    assert row["cache_hit_rate"] == pytest.approx(50 / 100)


def test_body_text_never_reaches_the_database(lens_db):
    write_parsed_session(lens_db, minimal_session())

    dump = "\n".join(
        str(tuple(row))
        for table in ("api_calls", "tool_calls", "tool_results", "items", "events")
        for row in lens_db.execute(f"SELECT * FROM {table}")
    )

    assert "SECRET_OUTPUT" not in dump
    assert "SECRET_ARGUMENT" not in dump
    assert "SECRET_PAYLOAD_VALUE" not in dump
    # 事件只存键名不存值，键名本身允许出现在库里
    assert '"note"' in dump
    assert lens_db.execute("SELECT arguments_chars FROM tool_calls").fetchone()[0] == len(
        '{"cmd": "SECRET_ARGUMENT"}'
    )
    assert lens_db.execute("SELECT output_chars FROM tool_results").fetchone()[0] == 13


def test_tool_call_details_view_joins_call_and_result(lens_db):
    write_parsed_session(lens_db, minimal_session())

    row = lens_db.execute("SELECT * FROM tool_call_details").fetchone()

    assert row["name"] == "exec_command"
    assert row["exit_code"] == 1
    assert row["success"] == 0
    assert row["wall_time_seconds"] == pytest.approx(2.5)
