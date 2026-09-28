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
