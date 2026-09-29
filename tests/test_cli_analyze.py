import json

from agent_lens.cli import main
from agent_lens.storage import connect, init_db


def _write_session(tmp_path):
    path = (
        tmp_path
        / "sessions"
        / "rollout-2026-09-29T00-00-00-01a0abcd-1111-7222-8333-444455556666.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "session_meta",
            "payload": {"session_id": "s1", "base_instructions": {"text": "BASE"}},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hi"}],
            },
        },
        {
            "type": "token_usage_record",
            "ordinal": 2,
            "payload": {"session_id": "s1", "turn_id": "t1", "usage": {"input_tokens": 500}},
        },
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


def test_analyze_writes_breakdown_rows(tmp_path, capsys):
    _write_session(tmp_path)
    db = tmp_path / "lens.db"
    code = main(["analyze", "--db", str(db), "--sessions-dir", str(tmp_path / "sessions")])
    assert code == 0
    out = capsys.readouterr().out
    assert "calls=1" in out and "blocks=5" in out

    conn = connect(db)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM context_breakdown").fetchone()[0] == 5
    summed = conn.execute("SELECT SUM(attributed_tokens) FROM context_breakdown").fetchone()[0]
    assert round(summed, 6) == 500.0


def test_analyze_is_idempotent(tmp_path):
    _write_session(tmp_path)
    db = tmp_path / "lens.db"
    for _ in range(2):
        assert main(["analyze", "--db", str(db), "--sessions-dir", str(tmp_path / "sessions")]) == 0
    conn = connect(db)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM context_breakdown").fetchone()[0] == 5
    assert conn.execute("SELECT SUM(attributed_tokens) FROM context_breakdown").fetchone()[0] == 500.0


def test_analyze_single_file(tmp_path, capsys):
    path = _write_session(tmp_path)
    code = main(["analyze", "--db", str(tmp_path / "lens.db"), "--file", str(path)])
    assert code == 0
    assert "calls=1" in capsys.readouterr().out


def test_analyze_skips_files_without_usage_records(tmp_path, capsys):
    path = tmp_path / "sessions" / "empty.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": {"session_id": "s9"}}))
    code = main(["analyze", "--db", str(tmp_path / "lens.db"), "--file", str(path)])
    assert code == 0
    assert "calls=0" in capsys.readouterr().out
