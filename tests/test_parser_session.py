import json

from agent_lens.parser import parse_session_file


def meta_line(
    session_id: str = "01a0d3ee-f6d2-7af3-80e0-641744b01963", chars: int = 17766
) -> dict:
    return {
        "ordinal": 0,
        "type": "session_meta",
        "payload": {
            "session_id": session_id,
            "timestamp": "2026-09-24T15:00:48.468Z",
            "cwd": "D:\\codex\\wzy_workstudio",
            "cli_version": "0.154.0",
            "model_provider": "deepseek",
            "base_instructions": {"text": "x" * chars},
        },
    }


def usage_line(ordinal: int, input_tokens: int, thread_cumulative: int) -> dict:
    return {
        "ordinal": ordinal,
        "type": "token_usage_record",
        "payload": {
            "turn_id": "turn1",
            "response_id": f"resp{ordinal}",
            "usage": {"input_tokens": input_tokens, "output_tokens": 10},
            "thread_token_usage": {"input_tokens": thread_cumulative},
        },
    }


def test_parses_meta_and_usage_rows(write_jsonl):
    path = write_jsonl(
        [meta_line(), usage_line(16, 18974, 18974), usage_line(28, 24207, 43181)]
    )

    parsed = parse_session_file(path)

    assert parsed.session_id == "01a0d3ee-f6d2-7af3-80e0-641744b01963"
    assert parsed.cli_version == "0.154.0"
    assert parsed.base_instructions_chars == 17766
    assert len(parsed.api_calls) == 2
    assert parsed.total_input_tokens == 43181
    assert parsed.last_thread_input_tokens == 43181


def test_incomplete_last_line_is_kept_as_partial_tail(write_jsonl):
    truncated = '{"ordinal": 99, "type": "token_usage_record", "payload": {"usage": {"input'
    path = write_jsonl([meta_line(), usage_line(16, 100, 100), truncated])

    parsed = parse_session_file(path)

    assert parsed.partial_tail == truncated
    assert parsed.parse_errors == []
    assert len(parsed.api_calls) == 1


def test_broken_middle_line_is_recorded_but_does_not_stop_parsing(write_jsonl):
    path = write_jsonl(
        [meta_line(), usage_line(16, 100, 100), '{"ordinal": 17, 坏的', usage_line(18, 200, 300)]
    )

    parsed = parse_session_file(path)

    assert len(parsed.parse_errors) == 1
    assert parsed.parse_errors[0].reason == "invalid_json"
    assert len(parsed.api_calls) == 2
    assert parsed.total_input_tokens == 300


def test_very_long_line_is_parsed(write_jsonl):
    path = write_jsonl([meta_line(chars=273823)])

    parsed = parse_session_file(path)

    assert parsed.base_instructions_chars == 273823


def test_session_without_usage_records_is_not_an_error(write_jsonl):
    """0.153.4 的会话完全没有 token_usage_record。"""
    path = write_jsonl(
        [meta_line(), {"ordinal": 1, "type": "event_msg", "payload": {"type": "task_started"}}]
    )

    parsed = parse_session_file(path)

    assert parsed.api_calls == []
    assert parsed.total_input_tokens == 0
    assert parsed.last_thread_input_tokens is None
    assert parsed.parse_errors == []


def test_empty_file_does_not_raise(write_jsonl):
    path = write_jsonl([])

    parsed = parse_session_file(path)

    assert parsed.api_calls == []
    assert parsed.partial_tail is None


def test_session_id_falls_back_to_filename(write_jsonl):
    path = write_jsonl(
        [], name="rollout-2026-09-24T23-00-48-01a0beef-f6d2-7af3-80e0-641744b01963.jsonl"
    )

    parsed = parse_session_file(path)

    assert parsed.session_id == "01a0beef-f6d2-7af3-80e0-641744b01963"


def test_row_ordinal_wins_over_line_number(write_jsonl):
    path = write_jsonl([meta_line(), usage_line(42, 100, 100)])

    parsed = parse_session_file(path)

    assert parsed.api_calls[0].ordinal == 42


def test_turn_complete_and_aborted_merge_into_one_turn(write_jsonl):
    completed = {
        "ordinal": 5,
        "type": "event_msg",
        "payload": {"type": "task_complete", "turn_id": "t1", "duration_ms": 300000},
    }
    aborted = {
        "ordinal": 6,
        "type": "event_msg",
        "payload": {"type": "turn_aborted", "turn_id": "t1", "reason": "user_interrupt"},
    }
    path = write_jsonl([meta_line(), completed, aborted])

    parsed = parse_session_file(path)

    assert len(parsed.turns) == 1
    assert parsed.turns[0].duration_ms == 300000
    assert parsed.turns[0].aborted_reason == "user_interrupt"


def test_merge_line_result_is_public(write_jsonl):
    from agent_lens.models import ParsedSession
    from agent_lens.parser import merge_line_result, parse_line

    parsed = ParsedSession(session_id="s", file_path="f.jsonl")

    merge_line_result(parsed, parse_line(json.dumps(usage_line(4, 10, 10)), "f.jsonl", 4))

    assert len(parsed.api_calls) == 1
    assert parsed.total_input_tokens == 10
