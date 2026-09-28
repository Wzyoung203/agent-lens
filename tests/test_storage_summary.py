from agent_lens.models import ParsedSession, ToolResultRecord
from agent_lens.storage import write_parsed_session


def test_tool_result_summary_is_written(lens_db):
    parsed = ParsedSession(
        session_id="s1",
        file_path="/tmp/rollout.jsonl",
        tool_results=[
            ToolResultRecord(
                file_path="/tmp/rollout.jsonl",
                ordinal=3,
                call_id="call_1",
                output_text="Exit code: 1",
                result_summary="Exit code: 1",
            )
        ],
    )

    write_parsed_session(lens_db, parsed)

    row = lens_db.execute("SELECT result_summary FROM tool_results").fetchone()
    assert row["result_summary"] == "Exit code: 1"


def test_tool_result_summary_defaults_to_null(lens_db):
    parsed = ParsedSession(
        session_id="s1",
        file_path="/tmp/rollout.jsonl",
        tool_results=[
            ToolResultRecord(file_path="/tmp/rollout.jsonl", ordinal=3, call_id="call_1")
        ],
    )

    write_parsed_session(lens_db, parsed)

    row = lens_db.execute("SELECT result_summary FROM tool_results").fetchone()
    assert row["result_summary"] is None
