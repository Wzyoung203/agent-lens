"""运行期配置：默认值 + TOML 文件覆盖 + 环境变量补凭据。

设计文档 5.5 / 7 节给出了配置项清单（示例是 YAML）。本实现对应用 TOML：
Python 3.12 标准库自带 tomllib，零新增依赖，且与 pyproject.toml 同构。
优先级：--config 参数 > AGENT_LENS_CONFIG 环境变量 > ~/.agent-lens/config.toml。
配置文件不存在时全部走默认值，不报错。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path.home() / ".agent-lens" / "config.toml"
CONFIG_PATH_ENV = "AGENT_LENS_CONFIG"


class SessionsConfig(BaseModel):
    """会话日志的扫描位置与节奏（设计文档 5.3 节）。"""

    dir: str = "~/.codex/sessions"
    poll_interval_seconds: float = 2.0
    patterns: list[str] = Field(default_factory=lambda: ["*.jsonl"])


class StorageConfig(BaseModel):
    db_path: str = "~/.agent-lens/agent-lens.db"


class LangfuseConfig(BaseModel):
    """上报配置（设计文档 4.9 / 5.5 / 6.2 / 10 节）。"""

    enabled: bool = False
    granularity: Literal["full", "turn"] = "full"
    batch_size: int = 50
    max_requests_per_minute: int = 1000
    public_key: str | None = None
    secret_key: str | None = None
    host: str = "https://cloud.langfuse.com"


class RedactionConfig(BaseModel):
    """脱敏配置（设计文档第 8 节）。"""

    enabled: bool = True
    summary_chars: int = 200


class AppConfig(BaseModel):
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)

    @property
    def sessions_dir(self) -> Path:
        return Path(self.sessions.dir).expanduser()

    @property
    def db_path(self) -> Path:
        return Path(self.storage.db_path).expanduser()


def resolve_config_path(path: str | Path | None = None) -> Path:
    """按 参数 > 环境变量 > 默认路径 决定配置文件位置。"""
    if path is not None:
        return Path(path).expanduser()
    from_env = os.environ.get(CONFIG_PATH_ENV)
    if from_env:
        return Path(from_env).expanduser()
    return DEFAULT_CONFIG_PATH


def load_config(path: str | Path | None = None) -> AppConfig:
    """读配置；文件缺失用默认值；Langfuse 凭据可从环境变量补齐。"""
    config_path = resolve_config_path(path)
    data: dict = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config = AppConfig.model_validate(data)
    if config.langfuse.public_key is None:
        config.langfuse.public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    if config.langfuse.secret_key is None:
        config.langfuse.secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    return config
