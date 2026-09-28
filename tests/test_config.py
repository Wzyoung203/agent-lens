from pathlib import Path

from agent_lens.config import AppConfig, load_config, resolve_config_path


def test_defaults_are_usable_without_a_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LENS_CONFIG", str(tmp_path / "missing.toml"))

    config = load_config()

    assert config.sessions.poll_interval_seconds == 2.0
    assert config.langfuse.granularity == "full"
    assert config.langfuse.enabled is False
    assert config.redaction.enabled is True
    assert config.sessions_dir.name == "sessions"


def test_explicit_path_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LENS_CONFIG", str(tmp_path / "env.toml"))
    explicit = tmp_path / "explicit.toml"

    assert resolve_config_path(explicit) == explicit
    assert resolve_config_path() == tmp_path / "env.toml"


def test_file_values_override_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[sessions]
dir = "/data/sessions"
poll_interval_seconds = 1.5

[langfuse]
enabled = true
granularity = "turn"
batch_size = 10

[redaction]
summary_chars = 120
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.sessions.dir == "/data/sessions"
    assert config.sessions.poll_interval_seconds == 1.5
    assert config.langfuse.enabled is True
    assert config.langfuse.granularity == "turn"
    assert config.langfuse.batch_size == 10
    assert config.redaction.summary_chars == 120


def test_langfuse_keys_fall_back_to_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    config = load_config(tmp_path / "missing.toml")

    assert config.langfuse.public_key == "pk-test"
    assert config.langfuse.secret_key == "sk-test"


def test_config_file_keys_win_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env")
    path = tmp_path / "config.toml"
    path.write_text('[langfuse]\npublic_key = "pk-file"\n', encoding="utf-8")

    assert load_config(path).langfuse.public_key == "pk-file"


def test_sessions_dir_expands_user():
    config = AppConfig()
    config.sessions.dir = "~/somewhere"

    assert config.sessions_dir == Path.home() / "somewhere"
