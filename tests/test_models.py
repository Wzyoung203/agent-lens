import pytest
from pydantic import ValidationError

from agent_lens.models import TokenUsage, to_utc


def test_cache_hit_rate_is_cached_over_input():
    usage = TokenUsage(input_tokens=24207, cached_input_tokens=19200, output_tokens=653)

    assert usage.cache_hit_rate == pytest.approx(19200 / 24207)


def test_cache_hit_rate_is_zero_when_input_is_zero():
    usage = TokenUsage(input_tokens=0, cached_input_tokens=0)

    assert usage.cache_hit_rate == 0.0


def test_uncached_input_excludes_cached_part():
    usage = TokenUsage(input_tokens=24207, cached_input_tokens=19200)

    assert usage.uncached_input_tokens == 5007


def test_cached_input_may_not_exceed_input():
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=100, cached_input_tokens=101)


def test_reasoning_output_may_not_exceed_output():
    with pytest.raises(ValidationError):
        TokenUsage(output_tokens=50, reasoning_output_tokens=51)


def test_reasoning_tokens_are_a_subset_of_output_tokens():
    usage = TokenUsage(output_tokens=263, reasoning_output_tokens=92)

    assert usage.output_tokens == 263


def test_to_utc_normalizes_z_suffix_to_utc():
    result = to_utc("2026-09-24T15:04:55.745Z")

    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 0
    assert result.hour == 15
