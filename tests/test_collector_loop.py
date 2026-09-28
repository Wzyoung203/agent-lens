import threading

from agent_lens.collector import Collector


def test_run_stops_after_max_scans(lens_db, sessions_dir):
    collector = Collector(lens_db, sessions_dir=sessions_dir)
    seen = []

    scans = collector.run(max_scans=3, sleep=lambda _: None, on_scan=seen.append)

    assert scans == 3
    assert len(seen) == 3


def test_run_exits_as_soon_as_the_stop_event_is_set(lens_db, sessions_dir):
    stop = threading.Event()
    collector = Collector(lens_db, sessions_dir=sessions_dir)
    polls: list[float] = []

    def on_scan(_outcome):
        stop.set()

    scans = collector.run(
        stop_event=stop,
        sleep=polls.append,
        on_scan=on_scan,
        max_scans=10,
        poll_interval=2.0,
    )

    assert scans == 1
    assert polls == []


def test_run_polls_between_scans(lens_db, sessions_dir):
    collector = Collector(lens_db, sessions_dir=sessions_dir)
    polls: list[float] = []

    collector.run(max_scans=3, sleep=polls.append, poll_interval=1.5)

    assert polls == [1.5, 1.5]
