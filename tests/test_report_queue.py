from datetime import UTC, datetime, timedelta

from agent_lens.reporter import ReportEnvelope, ReportQueue, derive_trace_id


def make_envelope(report_id: str = "api_call:/f.jsonl:3") -> ReportEnvelope:
    return ReportEnvelope(
        report_id=report_id,
        session_id="s1",
        turn_id="t1",
        kind="api_call",
        granularity="full",
        payload={"input_tokens": 10},
    )


def test_enqueue_then_pending_round_trips_the_payload(lens_db):
    queue = ReportQueue(lens_db)

    assert queue.enqueue_many([make_envelope()]) == 1

    pending = queue.pending(limit=10)
    assert len(pending) == 1
    assert pending[0].report_id == "api_call:/f.jsonl:3"
    assert pending[0].payload == {"input_tokens": 10}
    assert pending[0].attempts == 0


def test_duplicate_report_id_is_ignored(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])

    assert queue.enqueue_many([make_envelope()]) == 0
    assert queue.counts()["pending"] == 1


def test_pending_respects_limit_and_insertion_order(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope(f"api_call:/f.jsonl:{i}") for i in range(5)])

    pending = queue.pending(limit=2)

    assert [item.report_id for item in pending] == [
        "api_call:/f.jsonl:0",
        "api_call:/f.jsonl:1",
    ]


def test_mark_sent_removes_it_from_pending(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])

    queue.mark_sent("api_call:/f.jsonl:3")

    assert queue.pending(limit=10) == []
    assert queue.counts() == {"pending": 0, "sent": 1, "failed": 0}


def test_mark_failed_defers_until_next_attempt(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])
    failed_at = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    later = failed_at + timedelta(seconds=2)

    queue.mark_failed("api_call:/f.jsonl:3", "boom", next_attempt_at=later)

    assert queue.pending(limit=10, now=failed_at) == []
    assert queue.pending(limit=10, now=later) != []
    assert queue.counts() == {"pending": 0, "sent": 0, "failed": 1}


def test_failed_attempts_are_counted_across_retries(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])
    later = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    queue.mark_failed("api_call:/f.jsonl:3", "boom", next_attempt_at=later)
    queue.mark_failed("api_call:/f.jsonl:3", "boom again", next_attempt_at=later)

    assert queue.pending(limit=10, now=later)[0].attempts == 2


def test_reset_requeues_failed_items(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])
    queue.mark_failed(
        "api_call:/f.jsonl:3",
        "boom",
        next_attempt_at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
    )

    queue.reset()

    assert len(queue.pending(limit=10)) == 1
    assert queue.counts() == {"pending": 1, "sent": 0, "failed": 0}


def test_queue_survives_a_new_instance(lens_db):
    """进程被杀后重启：新实例看到的是同一批待发记录。"""
    ReportQueue(lens_db).enqueue_many([make_envelope()])

    recovered = ReportQueue(lens_db).pending(limit=10)

    assert [item.report_id for item in recovered] == ["api_call:/f.jsonl:3"]
    assert recovered[0].attempts == 0


def test_trace_id_is_stable_and_turn_scoped():
    assert derive_trace_id("s1", "t1") == derive_trace_id("s1", "t1")
    assert derive_trace_id("s1", "t1") != derive_trace_id("s1", "t2")
    assert len(derive_trace_id("s1", None)) == 32
