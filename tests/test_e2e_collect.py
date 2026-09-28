"""端到端冒烟：临时目录模拟 ~/.codex/sessions，跑通采集 -> 入库 -> 出队上报。

Reporter 用 RecordingBackend 假实现替换，不依赖 Langfuse（设计文档 11 节第 6 条）。
"""

from pathlib import Path

from agent_lens.collector import Collector
from agent_lens.redact import Redactor
from agent_lens.reporter import (
    LangfuseSink,
    RecordingBackend,
    Reporter,
    ReportQueue,
)
from agent_lens.storage import connect, counts, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "real_session_sample.jsonl"


def _prepare(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "rollout-real.jsonl").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    conn = connect(tmp_path / "lens.db")
    init_db(conn)
    return sessions, conn


def test_real_fixture_flows_end_to_end_into_the_queue(tmp_path):
    sessions, conn = _prepare(tmp_path)
    queue = ReportQueue(conn)

    outcome = Collector(
        conn, sessions_dir=sessions, queue=queue, redactor=Redactor()
    ).scan_once()

    assert outcome.api_calls_written == 17
    assert counts(conn)["api_calls"] == 17
    assert queue.counts()["pending"] == 17 + 24  # 17 条 api_call + 24 条工具调用


def test_full_chain_delivers_traces_to_the_backend(tmp_path):
    sessions, conn = _prepare(tmp_path)
    queue = ReportQueue(conn)
    backend = RecordingBackend()

    Collector(conn, sessions_dir=sessions, queue=queue).scan_once()
    outcome = Reporter(queue, LangfuseSink(backend), batch_size=1000).drain_once()

    assert outcome.sent == queue.counts()["sent"]
    assert queue.counts()["pending"] == 0
    kinds = [call[0] for call in backend.calls]
    assert kinds.count("start_trace") == outcome.sent
    assert "add_generation" in kinds


def test_second_scan_and_redelivery_change_nothing(tmp_path):
    sessions, conn = _prepare(tmp_path)
    queue = ReportQueue(conn)
    collector = Collector(conn, sessions_dir=sessions, queue=queue)
    collector.scan_once()
    before = counts(conn)
    queue_before = queue.counts()["pending"]

    collector.scan_once()

    assert counts(conn) == before
    assert queue.counts()["pending"] == queue_before


def test_no_secret_shapes_survive_into_the_queue(tmp_path):
    """上报 payload 是脱敏后的：库里与队列里都不应出现密钥原文。"""
    sessions, conn = _prepare(tmp_path)
    queue = ReportQueue(conn)

    Collector(conn, sessions_dir=sessions, queue=queue).scan_once()

    payloads = "\n".join(
        row["payload"] for row in conn.execute("SELECT payload FROM report_queue").fetchall()
    )
    summaries = "\n".join(
        row["result_summary"] or ""
        for row in conn.execute("SELECT result_summary FROM tool_results").fetchall()
    )
    for marker in ("ghp_", "github_pat_", "sk-", "AKIA"):
        assert marker not in payloads
        assert marker not in summaries
