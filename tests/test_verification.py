from agent_lens.parser import parse_session_file, verify_thread_totals
from tests.test_parser_session import meta_line, usage_line


def test_matches_when_last_cumulative_equals_sum(write_jsonl):
    path = write_jsonl(
        [meta_line(), usage_line(16, 18974, 18974), usage_line(28, 24207, 43181)]
    )

    result = verify_thread_totals(parse_session_file(path))

    assert result.matches is True
    assert result.summed_input_tokens == 43181
    assert result.last_thread_input_tokens == 43181


def test_reports_mismatch_when_cumulative_resets(write_jsonl):
    """模拟真实情况：累计字段在文件内被重置。"""
    path = write_jsonl([meta_line(), usage_line(16, 100, 100), usage_line(28, 200, 200)])

    result = verify_thread_totals(parse_session_file(path))

    assert result.matches is False
    assert result.summed_input_tokens == 300
    assert result.last_thread_input_tokens == 200


def test_session_without_usage_records_has_no_mismatch(write_jsonl):
    path = write_jsonl([meta_line()])

    result = verify_thread_totals(parse_session_file(path))

    assert result.matches is True
    assert result.summed_input_tokens == 0
    assert result.last_thread_input_tokens is None
