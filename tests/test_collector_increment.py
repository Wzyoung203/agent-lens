from agent_lens.collector import read_increment


def test_reads_complete_lines_with_their_byte_offsets(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a":1}\n{"b":2}\n')

    result = read_increment(path, 0)

    assert [offset for offset, _ in result.lines] == [0, 8]
    assert [text for _, text in result.lines] == ['{"a":1}', '{"b":2}']
    assert result.byte_offset == 16
    assert result.partial_tail is None


def test_only_reads_bytes_after_the_given_offset(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a":1}\n{"b":2}\n')

    result = read_increment(path, 8)

    assert [offset for offset, _ in result.lines] == [8]
    assert [text for _, text in result.lines] == ['{"b":2}']
    assert result.byte_offset == 16


def test_tail_without_newline_is_kept_and_offset_is_not_advanced(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a":1}\n{"b":')

    result = read_increment(path, 0)

    assert [text for _, text in result.lines] == ['{"a":1}']
    assert result.byte_offset == 8
    assert result.partial_tail == '{"b":'


def test_completing_the_tail_yields_the_whole_line(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a":1}\n{"b":')
    first = read_increment(path, 0)
    with path.open("ab") as handle:
        handle.write(b'2}\n')

    second = read_increment(path, first.byte_offset)

    assert [text for _, text in second.lines] == ['{"b":2}']
    assert second.byte_offset == 16
    assert second.partial_tail is None


def test_empty_file_yields_nothing(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_bytes(b"")

    result = read_increment(path, 0)

    assert result.lines == []
    assert result.byte_offset == 0
    assert result.partial_tail is None


def test_offset_at_end_of_file_yields_nothing(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a":1}\n')

    result = read_increment(path, 8)

    assert result.lines == []
    assert result.byte_offset == 8


def test_blank_lines_are_skipped_but_still_advance_the_offset(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a":1}\n\n   \n{"b":2}\n')

    result = read_increment(path, 0)

    assert [text for _, text in result.lines] == ['{"a":1}', '{"b":2}']
    assert result.byte_offset == 21


def test_invalid_utf8_is_replaced_not_raised(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b"\xff\xfe\n")

    result = read_increment(path, 0)

    assert len(result.lines) == 1
    assert "\ufffd" in result.lines[0][1]


def test_multibyte_characters_do_not_break_offsets(tmp_path):
    """中文是多字节：字节偏移必须按字节算，不能按字符算。"""
    path = tmp_path / "cn.jsonl"
    first = '{"m":"你好"}\n'.encode()
    path.write_bytes(first + b'{"b":2}\n')

    result = read_increment(path, len(first))

    assert [text for _, text in result.lines] == ['{"b":2}']


def test_very_long_line_is_returned_as_one_line(tmp_path):
    path = tmp_path / "long.jsonl"
    payload = b'{"text":"' + b"x" * 300_000 + b'"}\n'
    path.write_bytes(payload)

    result = read_increment(path, 0)

    assert len(result.lines) == 1
    assert len(result.lines[0][1]) == len(payload) - 1
    assert result.byte_offset == len(payload)
