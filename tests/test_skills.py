import json

from agent_lens import skills


def _call(name: str, arguments: str, ordinal: int = 1) -> dict:
    return {
        "type": "response_item",
        "ordinal": ordinal,
        "payload": {"type": "function_call", "name": name, "arguments": arguments},
    }


def test_find_skill_hits_extracts_name_and_path():
    lines = [
        json.dumps(
            _call(
                "exec_command",
                '{"command": "sed -n 1,40p /root/.codex/skills/brainstorming/SKILL.md"}',
            )
        )
    ]
    (hit,) = skills.find_skill_hits(lines, file_path="f.jsonl")
    assert hit.skill_name == "brainstorming"
    assert hit.skill_path.endswith("/skills/brainstorming/SKILL.md")
    assert hit.tool_name == "exec_command"


def test_non_skill_calls_are_ignored():
    lines = [json.dumps(_call("exec_command", '{"command": "ls -la"}'))]
    assert skills.find_skill_hits(lines, file_path="f.jsonl") == []


def test_ordinal_is_taken_from_the_line():
    row = _call("exec_command", '{"command": "cat /a/skills/index/SKILL.md"}', ordinal=42)
    (hit,) = skills.find_skill_hits([json.dumps(row)], file_path="f.jsonl")
    assert hit.ordinal == 42


def test_two_skills_on_one_line_yield_two_hits():
    row = _call("exec_command", '{"command": "diff /x/skills/a/SKILL.md /y/skills/b/SKILL.md"}')
    hits = skills.find_skill_hits([json.dumps(row)], file_path="f.jsonl")
    assert [hit.skill_name for hit in hits] == ["a", "b"]


def test_same_skill_twice_on_one_line_yields_one_hit():
    row = _call("exec_command", '{"command": "cat /x/skills/a/SKILL.md /x/skills/a/SKILL.md"}')
    hits = skills.find_skill_hits([json.dumps(row)], file_path="f.jsonl")
    assert [hit.skill_name for hit in hits] == ["a"]


def test_hits_without_an_ordinal_are_skipped():
    """没有 ordinal 就无法做幂等键，宁可不记也不要猜一个。"""
    line = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": '{"command": "cat /x/skills/a/SKILL.md"}',
            },
        }
    )
    assert skills.find_skill_hits([line], file_path="f.jsonl") == []


def test_find_skill_hits_in_file_reads_from_disk(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        json.dumps(_call("exec_command", '{"command": "cat /x/skills/z/SKILL.md"}')),
        encoding="utf-8",
    )
    (hit,) = skills.find_skill_hits_in_file(path)
    assert hit.skill_name == "z"
