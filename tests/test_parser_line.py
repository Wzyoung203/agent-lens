import json

from agent_lens.parser import parse_line


def test_parse_session_meta():
    raw = json.dumps(
        {
            "ordinal": 0,
            "type": "session_meta",
            "payload": {
                "session_id": "01a0d3ee",
                "timestamp": "2026-09-24T15:00:48.468Z",
                "cwd": "D:\\codex\\wzy_workstudio",
                "cli_version": "0.154.0",
                "model_provider": "deepseek",
                "base_instructions": {"text": "x" * 100},
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 0)

    assert result.session_meta.session_id == "01a0d3ee"
    assert result.session_meta.cli_version == "0.154.0"
    assert result.session_meta.base_instructions_chars == 100
    assert result.session_meta.cwd == "D:\\codex\\wzy_workstudio"


def test_parse_token_usage_record():
    raw = json.dumps(
        {
            "ordinal": 16,
            "type": "token_usage_record",
            "payload": {
                "thread_id": "t1",
                "turn_id": "turn1",
                "response_id": "resp1",
                "usage": {
                    "input_tokens": 18974,
                    "cached_input_tokens": 13184,
                    "output_tokens": 263,
                    "reasoning_output_tokens": 92,
                    "total_tokens": 19237,
                },
                "thread_token_usage": {"input_tokens": 43181},
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 16)

    assert result.api_call.usage.input_tokens == 18974
    assert result.api_call.thread_input_tokens_cumulative == 43181
    assert result.api_call.idempotency_key == ("f.jsonl", 16)


def test_parse_function_call_collects_arguments_as_raw_text():
    raw = json.dumps(
        {
            "ordinal": 5,
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": '{"command":["ls"]}',
                "call_id": "call_1",
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 5)

    assert result.tool_call.name == "exec_command"
    assert result.tool_call.kind == "function_call"
    assert result.tool_call.arguments_raw == '{"command":["ls"]}'


def test_parse_function_call_output_extracts_metadata():
    raw = json.dumps(
        {
            "ordinal": 6,
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "Exit code: 1\nWall time: 2 seconds\n",
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 6)

    assert result.tool_result.exit_code == 1
    assert result.tool_result.success is False
    assert result.tool_result.wall_time_seconds == 2.0


def test_parse_item_completed_computes_duration():
    raw = json.dumps(
        {
            "ordinal": 7,
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "CommandExecution", "id": "i1"},
                "started_at_ms": 1000,
                "completed_at_ms": 4500,
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 7)

    assert result.item_completed.item_type == "CommandExecution"
    assert result.item_completed.duration_ms == 3500


def test_parse_turn_context_records_model_and_effort():
    raw = json.dumps(
        {
            "ordinal": 2,
            "type": "turn_context",
            "payload": {"turn_id": "turn1", "model": "deepseek-v4-flash", "effort": "high"},
        }
    )

    result = parse_line(raw, "f.jsonl", 2)

    assert result.turn_context.model == "deepseek-v4-flash"
    assert result.turn_context.effort == "high"


def test_parse_task_complete_and_turn_aborted():
    complete = json.dumps(
        {
            "ordinal": 9,
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "t",
                "duration_ms": 300000,
            },
        }
    )
    aborted = json.dumps(
        {
            "ordinal": 10,
            "type": "event_msg",
            "payload": {"type": "turn_aborted", "turn_id": "t", "reason": "user_interrupt"},
        }
    )

    assert parse_line(complete, "f.jsonl", 9).turn_completed.duration_ms == 300000
    assert parse_line(aborted, "f.jsonl", 10).turn_aborted.aborted_reason == "user_interrupt"


def test_unknown_line_type_yields_empty_result():
    raw = json.dumps({"ordinal": 3, "type": "world_state", "payload": {}})

    result = parse_line(raw, "f.jsonl", 3)

    assert result.is_empty()
    assert result.parse_error is None


def test_broken_json_yields_parse_error():
    result = parse_line('{"ordinal": 4, "type": ', "f.jsonl", 4)

    assert result.parse_error is not None
    assert result.parse_error.reason == "invalid_json"


def test_token_record_without_cumulative_fields_still_parses():
    """0.153.4 之类的版本可能缺失累计字段与 session_id。"""
    raw = json.dumps(
        {
            "ordinal": 8,
            "type": "token_usage_record",
            "payload": {
                "turn_id": "t",
                "response_id": "r",
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 8)

    assert result.api_call.thread_input_tokens_cumulative is None
    assert result.api_call.session_id is None
