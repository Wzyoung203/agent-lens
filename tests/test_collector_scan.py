import json

from agent_lens.collector import Collector
from agent_lens.redact import Redactor
from agent_lens.reporter import ReportQueue
from agent_lens.storage import counts, get_ingest_state

SESSION_ID = "01a0d3ee-0000-7000-8000-000000000000"


def meta_line(cwd: str = "/work/p") -> dict:
    return {
        "ordinal": 0,
        "type": "session_meta",
        "payload": {
            "session_id": SESSION_ID,
            "timestamp": "2026-09-24T15:00:48.468Z",
            "cwd": cwd,
            "cli_version": "0.154.0",
            "model_provider": "deepseek",
            "base_instructions": {"text": "x" * 10},
        },
    }


def usage_line(ordinal: int, input_tokens: int) -> dict:
    return {
        "ordinal": ordinal,
        "type": "token_usage_record",
        "payload": {
            "session_id": SESSION_ID,
            "turn_id": "t1",
            "response_id": f"r{ordinal}",
            "usage": {"input_tokens": input_tokens, "output_tokens": 2},
        },
    }


def turn_context_line(ordinal: int) -> dict:
    return {
        "ordinal": ordinal,
        "type": "turn_context",
        "payload": {
            "turn_id": "t1",
            "model": "deepseek-v4-pro",
            "effort": "high",
            "cwd": "/work/p",
        },
    }


def tool_result_line(ordinal: int, output: str) -> dict:
    return {
        "ordinal": ordinal,
        "type": "response_item",
        "payload": {"type": "function_call_output", "call_id": "c1", "output": output},
    }


def write_lines(path, rows) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_missing_sessions_directory_is_not_an_error(lens_db, tmp_path):
    collector = Collector(lens_db, sessions_dir=tmp_path / "nope")

    outcome = collector.scan_once()

    assert outcome.files_scanned == 0
    assert outcome.api_calls_written == 0


def test_first_scan_ingests_a_file(lens_db, sessions_dir):
    write_lines(sessions_dir / "rollout-a.jsonl", [meta_line(), usage_line(1, 100)])
    collector = Collector(lens_db, sessions_dir=sessions_dir)

    outcome = collector.scan_once()

    assert outcome.files_scanned == 1
    assert outcome.files_changed == 1
    assert outcome.api_calls_written == 1
    assert counts(lens_db)["api_calls"] == 1
    assert counts(lens_db)["sessions"] == 1


def test_unchanged_file_is_skipped_on_the_second_scan(lens_db, sessions_dir):
    write_lines(sessions_dir / "rollout-a.jsonl", [meta_line(), usage_line(1, 100)])
    collector = Collector(lens_db, sessions_dir=sessions_dir)
    collector.scan_once()

    outcome = collector.scan_once()

    assert outcome.files_changed == 0
    assert outcome.lines_read == 0
    assert counts(lens_db)["api_calls"] == 1


def test_appended_lines_are_picked_up_incrementally(lens_db, sessions_dir):
    path = sessions_dir / "rollout-a.jsonl"
    write_lines(path, [meta_line(), usage_line(1, 100)])
    collector = Collector(lens_db, sessions_dir=sessions_dir)
    collector.scan_once()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(usage_line(2, 250), ensure_ascii=False) + "\n")

    outcome = collector.scan_once()

    assert outcome.lines_read == 1
    assert outcome.api_calls_written == 1
    assert counts(lens_db)["api_calls"] == 2
    assert get_ingest_state(lens_db, str(path)).last_ordinal == 2


def test_a_partial_last_line_is_left_for_the_next_scan(lens_db, sessions_dir):
    path = sessions_dir / "rollout-a.jsonl"
    write_lines(path, [meta_line()])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ordinal": 1, "type": "token_usage_record", "payload": {"usage"')
    collector = Collector(lens_db, sessions_dir=sessions_dir)

    first = collector.scan_once()

    assert first.api_calls_written == 0
    assert counts(lens_db)["api_calls"] == 0

    with path.open("a", encoding="utf-8") as handle:
        handle.write(': {"input_tokens": 42}}}\n')
    second = collector.scan_once()

    assert second.api_calls_written == 1
    assert counts(lens_db)["api_calls"] == 1


def test_rescanning_the_same_content_is_idempotent(lens_db, sessions_dir):
    path = sessions_dir / "rollout-a.jsonl"
    write_lines(path, [meta_line(), usage_line(1, 100), usage_line(2, 200)])
    collector = Collector(lens_db, sessions_dir=sessions_dir)
    collector.scan_once()

    # 模拟进程被杀后水位丢失：清空水位，强制从 0 重放
    lens_db.execute("DELETE FROM ingest_state")
    lens_db.commit()
    collector.scan_once()

    assert counts(lens_db)["api_calls"] == 2
    assert counts(lens_db)["sessions"] == 1


def test_truncated_file_is_replayed_from_the_start(lens_db, sessions_dir):
    path = sessions_dir / "rollout-a.jsonl"
    write_lines(path, [meta_line(), usage_line(1, 100), usage_line(2, 200)])
    collector = Collector(lens_db, sessions_dir=sessions_dir)
    collector.scan_once()
    write_lines(path, [meta_line(), usage_line(1, 100)])

    outcome = collector.scan_once()

    assert outcome.files_truncated == 1
    assert counts(lens_db)["api_calls"] == 2  # 重放不产生重复行
    # last_ordinal 只增不减（P1.2 的水位语义），所以这里断言的是读取位置回到文件末尾
    assert get_ingest_state(lens_db, str(path)).byte_offset == path.stat().st_size


def test_watermark_records_offset_size_and_mtime(lens_db, sessions_dir):
    path = sessions_dir / "rollout-a.jsonl"
    write_lines(path, [meta_line(), usage_line(1, 100)])
    Collector(lens_db, sessions_dir=sessions_dir).scan_once()

    state = get_ingest_state(lens_db, str(path))

    assert state.byte_offset == path.stat().st_size
    assert state.file_size == path.stat().st_size
    assert state.mtime == path.stat().st_mtime
    assert state.session_id == SESSION_ID
    assert state.cli_version == "0.154.0"


def test_tool_output_summary_is_redacted_before_storage(lens_db, sessions_dir):
    secret_output = "Exit code: 0\nWall time: 1 seconds\n密钥 ghp_" + "a" * 36
    write_lines(
        sessions_dir / "rollout-a.jsonl",
        [meta_line(), usage_line(1, 100), tool_result_line(2, secret_output)],
    )

    Collector(lens_db, sessions_dir=sessions_dir, redactor=Redactor()).scan_once()

    row = lens_db.execute("SELECT result_summary, output_chars FROM tool_results").fetchone()
    assert "ghp_" not in row["result_summary"]
    assert "[REDACTED:github_token]" in row["result_summary"]
    assert row["output_chars"] == len(secret_output)


def test_reports_are_queued_when_a_queue_is_provided(lens_db, sessions_dir):
    write_lines(
        sessions_dir / "rollout-a.jsonl",
        [meta_line(), turn_context_line(0), usage_line(1, 100)],
    )
    queue = ReportQueue(lens_db)

    outcome = Collector(lens_db, sessions_dir=sessions_dir, queue=queue).scan_once()

    assert outcome.reports_queued == 1
    assert queue.pending(limit=1)[0].payload["model"] == "deepseek-v4-pro"


def test_queued_reports_are_not_duplicated_on_rescan(lens_db, sessions_dir):
    write_lines(sessions_dir / "rollout-a.jsonl", [meta_line(), usage_line(1, 100)])
    queue = ReportQueue(lens_db)
    Collector(lens_db, sessions_dir=sessions_dir, queue=queue).scan_once()

    lens_db.execute("DELETE FROM ingest_state")
    lens_db.commit()
    Collector(lens_db, sessions_dir=sessions_dir, queue=queue).scan_once()

    assert queue.counts()["pending"] == 1


def test_turn_granularity_queues_one_report_per_turn(lens_db, sessions_dir):
    write_lines(
        sessions_dir / "rollout-a.jsonl",
        [meta_line(), usage_line(1, 100), usage_line(2, 200)],
    )
    queue = ReportQueue(lens_db)

    Collector(lens_db, sessions_dir=sessions_dir, queue=queue, granularity="turn").scan_once()

    assert queue.counts()["pending"] == 1


def test_only_jsonl_files_are_scanned(lens_db, sessions_dir):
    (sessions_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
    write_lines(sessions_dir / "rollout-a.jsonl", [meta_line(), usage_line(1, 100)])

    outcome = Collector(lens_db, sessions_dir=sessions_dir).scan_once()

    assert outcome.files_scanned == 1
