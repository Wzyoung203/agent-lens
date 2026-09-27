import json
from datetime import UTC, datetime

from agent_lens.parser import parse_line


def test_task_complete_fills_turn_window():
    raw = json.dumps(
        {
            "ordinal": 9,
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "t1",
                "started_at": 1758000000,
                "completed_at": 1758000300,
                "duration_ms": 300000,
            },
        }
    )

    turn = parse_line(raw, "f.jsonl", 9).turn_completed

    assert turn.started_at == datetime.fromtimestamp(1758000000, tz=UTC)
    assert turn.completed_at == datetime.fromtimestamp(1758000300, tz=UTC)


def test_task_started_becomes_turn_started():
    raw = json.dumps(
        {
            "ordinal": 1,
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "t1", "started_at": 1758000000},
        }
    )

    result = parse_line(raw, "f.jsonl", 1)

    assert result.turn_started.turn_id == "t1"
    assert result.turn_started.started_at == datetime.fromtimestamp(1758000000, tz=UTC)
    assert result.is_empty() is False


def test_unknown_event_carries_ordinal():
    raw = json.dumps(
        {"ordinal": 12, "type": "event_msg", "payload": {"type": "context_compacted"}}
    )

    result = parse_line(raw, "f.jsonl", 12)

    assert result.event["ordinal"] == 12
    assert result.event["event_type"] == "context_compacted"
