import json

from agent_lens import context


def _meta(base: str, session_id: str = "s1") -> dict:
    return {
        "type": "session_meta",
        "payload": {"session_id": session_id, "base_instructions": {"text": base}},
    }


def _ri(payload: dict) -> dict:
    return {"type": "response_item", "payload": payload}


def _tur(ordinal: int, input_tokens: int, turn_id: str = "t1") -> dict:
    return {
        "type": "token_usage_record",
        "ordinal": ordinal,
        "payload": {
            "session_id": "s1",
            "turn_id": turn_id,
            "usage": {"input_tokens": input_tokens},
        },
    }


def _lines(*rows: dict) -> list[str]:
    return [json.dumps(r, ensure_ascii=False) for r in rows]


def test_split_chars_separates_cjk_from_ascii():
    assert context.split_chars("abc") == (0, 3)
    assert context.split_chars("中文") == (2, 0)
    assert context.split_chars("a中") == (1, 1)


def test_attribute_sums_to_real_input_tokens():
    blocks = {
        context.BLOCK_FIXED_INSTRUCTIONS: context.BlockChars(other=100),
        context.BLOCK_HISTORY: context.BlockChars(other=100),
    }
    attributed, unattributed = context.attribute(blocks, input_tokens=1000)
    assert attributed[context.BLOCK_FIXED_INSTRUCTIONS] == 30.0
    assert attributed[context.BLOCK_HISTORY] == 30.0
    # 940 是纯字符估算覆盖不到的部分，必须单列而不是摊到四块里
    assert unattributed == 940.0
    assert sum(attributed.values()) + unattributed == 1000


def test_attribute_scales_down_when_estimate_exceeds_real():
    blocks = {context.BLOCK_HISTORY: context.BlockChars(other=1000)}
    attributed, unattributed = context.attribute(blocks, input_tokens=100)
    assert attributed[context.BLOCK_HISTORY] == 100.0
    assert unattributed == 0.0


def test_attribute_handles_zero_input_tokens():
    blocks = {context.BLOCK_HISTORY: context.BlockChars(other=1000)}
    attributed, unattributed = context.attribute(blocks, input_tokens=0)
    assert attributed[context.BLOCK_HISTORY] == 0.0
    assert unattributed == 0.0


def test_snapshot_excludes_output_produced_by_the_same_call():
    """本次调用的 reasoning/function_call 不进本次快照；工具输出之后才进上下文。"""
    lines = _lines(
        _meta("BASE"),
        _ri({
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "U" * 10}],
        }),
        _ri({"type": "reasoning", "content": [{"type": "reasoning_text", "text": "R" * 100}]}),
        _ri({"type": "function_call", "name": "exec_command", "arguments": "A" * 100}),
        _tur(4, 1000),
        _ri({"type": "function_call_output", "output": "O" * 100}),
        _ri({"type": "reasoning", "content": [{"type": "reasoning_text", "text": "R" * 100}]}),
        _tur(7, 2000),
    )
    first, second = context.decompose_lines(lines, file_path="f.jsonl")

    # 第一次调用：只有固定指令 + 用户消息进上下文，本次的 reasoning/function_call 不算
    assert first.blocks[context.BLOCK_HISTORY].other == 10
    assert first.blocks[context.BLOCK_TOOL_OUTPUT].other == 0

    # 第二次调用：上一轮的 reasoning(100)+function_call(100)+工具输出(100) 都已提交
    assert second.blocks[context.BLOCK_HISTORY].other == 10 + 100 + 100
    assert second.blocks[context.BLOCK_TOOL_OUTPUT].other == 100
    assert (first.ordinal, second.ordinal) == (4, 7)


def test_developer_message_goes_to_skill_catalog_block():
    lines = _lines(
        _meta("BASE"),
        _ri({
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "D" * 20}],
        }),
        _tur(2, 100),
    )
    (only,) = context.decompose_lines(lines, file_path="f.jsonl")
    assert only.blocks[context.BLOCK_SKILL_CATALOG].other == 20
    assert only.blocks[context.BLOCK_HISTORY].other == 0


def test_base_instructions_object_shape_is_read_from_text_field():
    lines = _lines(_meta("中文" * 5), _tur(1, 10))
    (only,) = context.decompose_lines(lines, file_path="f.jsonl")
    assert only.blocks[context.BLOCK_FIXED_INSTRUCTIONS].cjk == 10
    assert only.blocks[context.BLOCK_FIXED_INSTRUCTIONS].other == 0


def test_decompose_file_reads_from_disk(tmp_path):
    path = tmp_path / "rollout-x.jsonl"
    path.write_text("\n".join(_lines(_meta("BASE"), _tur(1, 50))), encoding="utf-8")
    (only,) = context.decompose_file(path)
    assert only.input_tokens == 50
    assert only.session_id == "s1"
