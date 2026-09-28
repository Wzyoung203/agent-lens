import time

import pytest

from agent_lens.reporter import (
    LangfuseSink,
    NullSink,
    RateLimiter,
    RecordingBackend,
    RecordingSink,
    ReportEnvelope,
    Reporter,
    ReportQueue,
    backoff_delay,
)
from tests.conftest import MutableNow


class FlakySink:
    """前 N 次发送抛错，之后成功。用来验证退避与「失败即停」。"""

    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls: list[ReportEnvelope] = []
        self.received: list[ReportEnvelope] = []

    def send(self, envelope: ReportEnvelope) -> None:
        self.calls.append(envelope)
        if len(self.calls) <= self.failures:
            raise RuntimeError("langfuse unavailable")
        self.received.append(envelope)


def make_envelope(index: int = 0) -> ReportEnvelope:
    return ReportEnvelope(
        report_id=f"api_call:/f.jsonl:{index}",
        session_id="s1",
        turn_id="t1",
        kind="api_call",
        payload={"input_tokens": index},
    )


def test_backoff_starts_at_one_second_and_doubles():
    assert backoff_delay(1) == 1.0
    assert backoff_delay(2) == 2.0
    assert backoff_delay(3) == 4.0


def test_backoff_is_capped_at_five_minutes():
    assert backoff_delay(20) == 300.0


def test_rate_limiter_spaces_out_acquisitions():
    clock_value = {"t": 0.0}
    limiter = RateLimiter(60, clock=lambda: clock_value["t"])
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock_value["t"] += seconds

    limiter.acquire(sleep=fake_sleep)
    limiter.acquire(sleep=fake_sleep)
    limiter.acquire(sleep=fake_sleep)

    assert slept == [pytest.approx(1.0), pytest.approx(1.0)]


def test_drain_once_sends_everything_and_marks_sent(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope(i) for i in range(3)])
    sink = RecordingSink()

    outcome = Reporter(queue, sink, batch_size=10).drain_once()

    assert (outcome.attempted, outcome.sent, outcome.failed) == (3, 3, 0)
    assert [item.report_id for item in sink.received] == [
        "api_call:/f.jsonl:0",
        "api_call:/f.jsonl:1",
        "api_call:/f.jsonl:2",
    ]
    assert queue.counts() == {"pending": 0, "sent": 3, "failed": 0}


def test_drain_once_respects_batch_size(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope(i) for i in range(5)])
    sink = RecordingSink()

    Reporter(queue, sink, batch_size=2).drain_once()

    assert len(sink.received) == 2
    assert queue.counts()["pending"] == 3


def test_sink_failure_is_swallowed_and_recorded(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])
    sink = FlakySink(failures=1)

    outcome = Reporter(queue, sink).drain_once()

    assert outcome.failed == 1
    assert queue.counts()["failed"] == 1
    assert queue.pending(limit=10) == []


def test_failure_stops_the_batch_instead_of_hammering_the_backend(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope(i) for i in range(3)])
    sink = FlakySink(failures=99)

    outcome = Reporter(queue, sink).drain_once()

    assert outcome.attempted == 3
    assert outcome.sent == 0
    assert outcome.failed == 1
    assert len(sink.calls) == 1


def test_item_is_retried_after_the_backoff_window(lens_db):
    clock = MutableNow()
    queue = ReportQueue(lens_db, now=clock)
    queue.enqueue_many([make_envelope()])
    sink = FlakySink(failures=1)
    limiter = RateLimiter(1000, clock=lambda: clock.value.timestamp())
    reporter = Reporter(queue, sink, now=clock, sleep=clock.advance, rate_limiter=limiter)

    reporter.drain_once()
    clock.advance(2.0)
    outcome = reporter.drain_once()

    assert outcome.sent == 1
    assert queue.counts() == {"pending": 0, "sent": 1, "failed": 0}


def test_retry_is_not_attempted_inside_the_backoff_window(lens_db):
    clock = MutableNow()
    queue = ReportQueue(lens_db, now=clock)
    queue.enqueue_many([make_envelope()])
    sink = FlakySink(failures=1)
    limiter = RateLimiter(1000, clock=lambda: clock.value.timestamp())
    reporter = Reporter(queue, sink, now=clock, sleep=clock.advance, rate_limiter=limiter)

    reporter.drain_once()
    clock.advance(0.2)
    outcome = reporter.drain_once()

    assert outcome.attempted == 0
    assert len(sink.calls) == 1


def test_null_sink_swallows_everything(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])

    outcome = Reporter(queue, NullSink()).drain_once()

    assert outcome.sent == 1


def test_run_returns_after_max_rounds(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])
    reporter = Reporter(queue, RecordingSink(), sleep=lambda _: None)

    assert reporter.run(max_rounds=3, idle_sleep=0) == 3


def test_run_stops_when_stop_event_is_set(lens_db):
    import threading

    stop = threading.Event()
    stop.set()
    reporter = Reporter(ReportQueue(lens_db), RecordingSink(), sleep=lambda _: None)

    assert reporter.run(stop_event=stop, max_rounds=10, idle_sleep=0) == 1


def test_langfuse_sink_maps_api_calls_to_generations():
    backend = RecordingBackend()
    envelope = ReportEnvelope(
        report_id="api_call:/f.jsonl:1",
        session_id="s1",
        turn_id="t1",
        kind="api_call",
        payload={"model": "deepseek-v4-pro", "usage": {"input_tokens": 5}},
    )

    LangfuseSink(backend).send(envelope)

    assert [call[0] for call in backend.calls] == ["start_trace", "add_generation"]
    assert backend.calls[0][1]["trace_id"] == envelope.trace_id
    assert backend.calls[0][1]["session_id"] == "s1"
    assert backend.calls[1][1]["model"] == "deepseek-v4-pro"


def test_langfuse_sink_maps_tool_calls_to_spans():
    backend = RecordingBackend()
    envelope = ReportEnvelope(
        report_id="tool_call:/f.jsonl:1",
        session_id="s1",
        turn_id="t1",
        kind="tool_call",
        payload={"name": "exec_command", "success": False},
    )

    LangfuseSink(backend).send(envelope)

    assert [call[0] for call in backend.calls] == ["start_trace", "add_span"]
    assert "exec_command" in backend.calls[1][1]["name"]
    assert backend.calls[1][1]["metadata"]["success"] is False


def test_disabled_langfuse_sink_does_nothing():
    backend = RecordingBackend()

    LangfuseSink(backend, enabled=False).send(make_envelope())

    assert backend.calls == []


def test_default_rate_limit_does_not_sleep_for_a_single_batch(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope()])
    slept: list[float] = []

    Reporter(queue, RecordingSink(), sleep=slept.append).drain_once()

    assert slept == []


def test_reporter_uses_the_injected_sleep(lens_db):
    queue = ReportQueue(lens_db)
    queue.enqueue_many([make_envelope(i) for i in range(2)])
    slept: list[float] = []
    limiter = RateLimiter(60, clock=time.monotonic)

    Reporter(queue, RecordingSink(), sleep=slept.append, rate_limiter=limiter).drain_once()

    assert len(slept) == 1
