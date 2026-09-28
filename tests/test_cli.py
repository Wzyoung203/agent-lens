from agent_lens.cli import build_parser, main


def test_parser_exposes_three_commands():
    parser = build_parser()

    assert parser.parse_args(["collect", "--once"]).once is True
    assert parser.parse_args(["collect"]).once is False
    assert parser.parse_args(["status"]).command == "status"
    assert parser.parse_args(["backfill"]).command == "backfill"


def test_collect_accepts_overrides():
    args = build_parser().parse_args(
        ["collect", "--db", "/tmp/x.db", "--sessions-dir", "/tmp/s", "--poll-interval", "1.0"]
    )

    assert args.db == "/tmp/x.db"
    assert args.sessions_dir == "/tmp/s"
    assert args.poll_interval == 1.0


def test_help_exits_zero(capsys):
    assert main(["--help"]) == 0
    assert "agent-lens" in capsys.readouterr().out


import json

from agent_lens.storage import connect, counts


def _write_session(path, session_id: str = "01a0d3ee-0000-7000-8000-000000000000") -> None:
    rows = [
        {
            "ordinal": 0,
            "type": "session_meta",
            "payload": {
                "session_id": session_id,
                "timestamp": "2026-09-24T15:00:48.468Z",
                "cwd": "/work/p",
                "cli_version": "0.154.0",
                "base_instructions": {"text": "x" * 10},
            },
        },
        {
            "ordinal": 1,
            "type": "token_usage_record",
            "payload": {
                "session_id": session_id,
                "turn_id": "t1",
                "response_id": "r1",
                "usage": {"input_tokens": 100, "output_tokens": 2},
            },
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_collect_once_ingests_and_exits_zero(tmp_path, capsys):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _write_session(sessions / "rollout-a.jsonl")
    db = tmp_path / "lens.db"

    exit_code = main(
        ["collect", "--once", "--no-langfuse", "--db", str(db), "--sessions-dir", str(sessions)]
    )

    assert exit_code == 0
    assert "api_calls=1" in capsys.readouterr().out
    conn = connect(db)
    assert counts(conn)["api_calls"] == 1


def test_collect_once_is_idempotent(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _write_session(sessions / "rollout-a.jsonl")
    db = tmp_path / "lens.db"
    argv = ["collect", "--once", "--no-langfuse", "--db", str(db), "--sessions-dir", str(sessions)]

    main(argv)
    main(argv)

    conn = connect(db)
    assert counts(conn)["api_calls"] == 1


def test_status_prints_table_counts(tmp_path, capsys):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _write_session(sessions / "rollout-a.jsonl")
    db = tmp_path / "lens.db"
    main(["collect", "--once", "--no-langfuse", "--db", str(db), "--sessions-dir", str(sessions)])

    exit_code = main(["status", "--db", str(db)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "api_calls" in out


def test_backfill_replays_from_the_start_without_duplicating(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _write_session(sessions / "rollout-a.jsonl")
    db = tmp_path / "lens.db"
    main(["collect", "--once", "--no-langfuse", "--db", str(db), "--sessions-dir", str(sessions)])

    exit_code = main(["backfill", "--db", str(db), "--sessions-dir", str(sessions)])

    assert exit_code == 0
    conn = connect(db)
    assert counts(conn)["api_calls"] == 1
