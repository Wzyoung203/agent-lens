import json
from pathlib import Path

from agent_lens.parser import parse_session_file, verify_thread_totals

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_real_fixture_totals_match_recorded_meta():
    fixture = FIXTURE_DIR / "real_session_sample.jsonl"
    meta = json.loads((FIXTURE_DIR / "real_session_sample.meta.json").read_text(encoding="utf-8"))

    parsed = parse_session_file(fixture)

    assert len(parsed.api_calls) == meta["api_call_count"]
    assert parsed.total_input_tokens == meta["summed_input_tokens"]


def test_real_fixture_passes_self_check():
    parsed = parse_session_file(FIXTURE_DIR / "real_session_sample.jsonl")

    result = verify_thread_totals(parsed)

    assert result.matches is True


def test_real_fixture_contains_no_obvious_secrets():
    text = (FIXTURE_DIR / "real_session_sample.jsonl").read_text(encoding="utf-8")

    for marker in ("ghp_", "github_pat_", "sk-", "AKIA", "BEGIN RSA PRIVATE KEY"):
        assert marker not in text
