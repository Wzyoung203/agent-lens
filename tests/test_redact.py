import pytest

from agent_lens.redact import RedactionRule, Redactor


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("github_token", "token 是 ghp_" + "a" * 36),
        ("github_pat", "token 是 github_pat_" + "A1_" * 8),
        ("openai_key", "export OPENAI_API_KEY=sk-" + "b" * 32),
        ("aws_access_key", "AKIAIOSFODNN7EXAMPLE"),
        ("slack_token", "xoxb-" + "1234567890abc"),
        ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"),
        ("bearer", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"),
        (
            "private_key",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
        ),
    ],
)
def test_named_secret_shapes_are_replaced(name, text):
    result = Redactor().redact(text)

    assert result.text.count(f"[REDACTED:{name}]") == 1
    assert result.total == 1
    assert result.hits[0].rule == name


@pytest.mark.parametrize(
    "text",
    [
        "password=hunter2secret",
        "password: hunter2secret",
        'token = "abcdef123456"',
        "api_key=abcdef1234567890",
        "SECRET=topsecretvalue",
        "client_secret: shhh-very-secret",
    ],
)
def test_assignments_replace_only_the_value(text):
    result = Redactor().redact(text)

    assert "[REDACTED:assignment]" in result.text


def test_assignment_keeps_the_key_name():
    assert Redactor().redact_text("password=hunter2secret") == "password=[REDACTED:assignment]"


def test_plain_text_is_untouched():
    text = "这段中文没有任何密钥，只是说明文字。"

    result = Redactor().redact(text)

    assert result.text == text
    assert result.hits == []


def test_short_value_after_password_is_not_a_secret():
    """误报与漏报的取舍：太短的值不匹配，避免把正常文本打成马赛克。"""
    text = "password=abc"

    assert Redactor().redact_text(text) == text


def test_dry_run_reports_hits_without_returning_the_secret():
    text = "ghp_" + "a" * 36

    hits = Redactor().dry_run(text)

    assert len(hits) == 1
    assert hits[0].rule == "github_token"
    assert hits[0].count == 1


def test_extra_rules_are_applied():
    redactor = Redactor([RedactionRule(name="employee_id", pattern=r"EMP-\d{6}")])

    assert redactor.redact_text("负责人 EMP-123456") == "负责人 [REDACTED:employee_id]"


def test_disabled_redactor_is_a_passthrough():
    redactor = Redactor(enabled=False)

    assert redactor.redact_text("ghp_" + "a" * 36) == "ghp_" + "a" * 36


def test_multiple_hits_are_counted_per_rule():
    text = "ghp_" + "a" * 36 + " 和 ghp_" + "b" * 36

    result = Redactor().redact(text)

    assert result.text == "[REDACTED:github_token] 和 [REDACTED:github_token]"
    assert result.hits[0].count == 2


def test_summarize_collapses_whitespace():
    text = "第一行\n\n第二行\t\t第三行"

    assert Redactor().summarize(text) == "第一行 第二行 第三行"


def test_summarize_truncates_on_a_word_boundary_with_a_marker():
    text = " ".join(["word"] * 100)

    summary = Redactor().summarize(text, limit=20)

    assert summary.endswith("…[截断]")
    assert "  " not in summary


def test_summarize_redacts_secrets_even_far_past_the_limit():
    """先脱敏再截断：落在截断点之外的密钥也必须先被替换掉。"""
    text = "x " * 300 + "ghp_" + "a" * 36

    summary = Redactor().summarize(text, limit=50)

    assert "ghp_" not in summary
    assert "…[截断]" in summary
