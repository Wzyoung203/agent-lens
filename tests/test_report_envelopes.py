import json

from agent_lens.models import (
    ApiCallRecord,
    ParsedSession,
    TokenUsage,
    ToolCallRecord,
    ToolResultRecord,
    TurnContextRecord,
    TurnRecord,
)
from agent_lens.redact import Redactor
from agent_lens.reporter import build_envelopes


def sample_parsed() -> ParsedSession:
    return ParsedSession(
        session_id="s1",
        file_path="/f.jsonl",
        turn_contexts=[TurnContextRecord(turn_id="t1", model="deepseek-v4-pro", effort="high")],
        turns=[TurnRecord(turn_id="t1", duration_ms=3000)],
        api_calls=[
            ApiCallRecord(
                file_path="/f.jsonl",
                ordinal=10,
                turn_id="t1",
                response_id="r1",
                usage=TokenUsage(input_tokens=100, cached_input_tokens=40, output_tokens=5),
            )
        ],
        tool_calls=[
            ToolCallRecord(
                file_path="/f.jsonl",
                ordinal=4,
                call_id="c1",
                name="exec_command",
                arguments_raw="ghp_" + "a" * 36,
            )
        ],
        tool_results=[
            ToolResultRecord(
                file_path="/f.jsonl",
                ordinal=5,
                call_id="c1",
                output_text="Exit code: 1\nWall time: 2 seconds",
                exit_code=1,
                wall_time_seconds=2.0,
                success=False,
                result_summary="Exit code: 1",
            )
        ],
    )


def test_full_granularity_makes_one_envelope_per_record():
    envelopes = build_envelopes(sample_parsed(), redactor=Redactor(), granularity="full")

    assert [item.kind for item in envelopes] == ["api_call", "tool_call"]
    assert [item.report_id for item in envelopes] == [
        "api_call:/f.jsonl:10",
        "tool_call:/f.jsonl:4",
    ]


def test_full_granularity_attaches_tool_call_to_the_following_turn():
    """工具调用 ordinal=4 之后的第一条 api_call 是 ordinal=10，属于 t1。"""
    envelopes = build_envelopes(sample_parsed(), redactor=Redactor(), granularity="full")

    tool_envelope = envelopes[1]
    assert tool_envelope.turn_id == "t1"
    assert tool_envelope.trace_id == envelopes[0].trace_id


def test_api_call_payload_carries_model_effort_and_usage():
    envelopes = build_envelopes(sample_parsed(), redactor=Redactor(), granularity="full")

    payload = envelopes[0].payload
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["effort"] == "high"
    assert payload["usage"]["input_tokens"] == 100
    assert payload["usage"]["cached_input_tokens"] == 40


def test_tool_call_payload_is_redacted():
    envelopes = build_envelopes(sample_parsed(), redactor=Redactor(), granularity="full")

    arguments_summary = envelopes[1].payload["arguments_summary"]
    assert "ghp_" not in arguments_summary
    assert "[REDACTED:github_token]" in arguments_summary
    assert envelopes[1].payload["success"] is False
    assert envelopes[1].payload["exit_code"] == 1


def test_turn_granularity_collapses_to_one_envelope():
    envelopes = build_envelopes(sample_parsed(), redactor=Redactor(), granularity="turn")

    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope.kind == "turn"
    assert envelope.turn_id == "t1"
    assert envelope.report_id == "turn:/f.jsonl:t1"
    payload = envelope.payload
    assert payload["api_call_count"] == 1
    assert payload["tool_call_count"] == 1
    assert payload["tool_failure_count"] == 1
    assert payload["total_input_tokens"] == 100
    assert payload["duration_ms"] == 3000


def test_payload_is_json_serializable():
    envelopes = build_envelopes(sample_parsed(), redactor=Redactor(), granularity="full")

    for envelope in envelopes:
        json.dumps(envelope.payload, ensure_ascii=False)
