import pytest

from agent_lens.parser import derive_success, extract_exec_metadata


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Chunk ID: 7649ee\nWall time: 0.2849 seconds\nOutput:\nhello", (None, 0.2849)),
        ("Exit code: 1\nWall time: 5 seconds\nOutput:\n", (1, 5.0)),
        ("Exit code: 0\nWall time: 12.5 seconds\n", (0, 12.5)),
        ("纯粹的文本，没有任何元数据", (None, None)),
        ("Wall time: 3 seconds", (None, 3.0)),
    ],
)
def test_extract_exec_metadata(text, expected):
    assert extract_exec_metadata(text) == expected


def test_exit_code_must_be_at_line_start():
    """输出正文里出现的 Exit code 字样不应被误认。"""
    text = "输出内容里提到 Exit code: 1 这个词\nWall time: 1 seconds"

    assert extract_exec_metadata(text) == (None, 1.0)


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(0, True), (1, False), (127, False), (None, None)],
)
def test_derive_success(exit_code, expected):
    assert derive_success(exit_code) is expected
