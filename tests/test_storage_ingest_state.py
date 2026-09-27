import pytest

from agent_lens.storage import get_ingest_state, update_ingest_state, write_parsed_session
from tests.test_storage_write import minimal_session


def test_ingest_state_tracks_watermark(lens_db):
    write_parsed_session(lens_db, minimal_session("f.jsonl"))
    update_ingest_state(
        lens_db,
        file_path="f.jsonl",
        byte_offset=4096,
        file_size=8192,
        mtime=123.5,
        last_ordinal=20,
    )

    state = get_ingest_state(lens_db, "f.jsonl")

    assert state.last_ordinal == 20
    assert state.byte_offset == 4096
    assert state.file_size == 8192
    assert state.mtime == pytest.approx(123.5)
    assert state.cli_version == "0.157.1"


def test_watermark_never_goes_backwards(lens_db):
    update_ingest_state(lens_db, file_path="f.jsonl", last_ordinal=50)
    update_ingest_state(lens_db, file_path="f.jsonl", last_ordinal=30)

    assert get_ingest_state(lens_db, "f.jsonl").last_ordinal == 50


def test_unknown_file_has_no_state(lens_db):
    assert get_ingest_state(lens_db, "never-seen.jsonl") is None
