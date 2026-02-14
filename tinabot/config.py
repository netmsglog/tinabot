"""Configuration management using pydantic-settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseModel):
    """Agent execution settings."""

    provider: str = "claude"  # "claude", "openai", "gemini", "grok"
    model: str = "claude-opus-4-6"
    max_thinking_tokens: int = 10000
    permission_mode: str = "acceptEdits"
    cwd: str = str(Path.home() / ".tinabot" / "workspace")
    api_key: str = ""  # ANTHROPIC_API_KEY, optional (falls back to claude login)
    base_url: str = ""  # Custom API endpoint (auto-resolved from provider if empty)
    max_tokens: int = 16384  # Max output tokens for OpenAI-compatible providers
    timeout_seconds: int = 300  # Max time per agent call (0 = no limit)
    allowed_tools: list[str] = Field(default_factory=lambda: [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
        "WebSearch", "WebFetch", "Task",
    ])

    def resolved_base_url(self) -> str:
        """Return the API base URL, auto-resolved from provider if not set."""
        if self.base_url:
            return self.base_url
        return {
            "openai": "https://api.openai.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "grok": "https://api.x.ai/v1",
        }.get(self.provider, "")

    @property
    def is_claude(self) -> bool:
        return self.provider == "claude"


class TelegramConfig(BaseModel):
    """Telegram bot settings."""

    enabled: bool = False
    token: str = ""
    allowed_users: list[int] = Field(default_factory=list)
    groq_api_key: str = ""  # For voice transcription (Whisper via Groq)


class MemoryConfig(BaseModel):
    """Task memory settings."""

    data_dir: str = str(Path.home() / ".tinabot" / "data")
    compress_after_turns: int = 20
    max_summary_tokens: int = 2000


class SkillsConfig(BaseModel):
    """Skills loader settings."""

    skills_dir: str = str(Path.home() / ".agents" / "skills")


class Config(BaseSettings):
    """Root configuration.

    Loads from ~/.tinabot/config.json and environment variables
    with prefix TINABOT_.
    """

    model_config = SettingsConfigDict(
        env_prefix="TINABOT_",
        env_nested_delimiter="__",
    )

    agent: AgentConfig = Field(default_factory=AgentConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)

    @classmethod
    def load(cls) -> Config:
        """Load config from file and environment."""
        config_path = Config.config_path()
        file_data: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path) as f:
                file_data = json.load(f)
        return cls(**file_data)

    @staticmethod
    def config_path() -> Path:
        return Path.home() / ".tinabot" / "config.json"

    @staticmethod
    def load_raw() -> dict[str, Any]:
        """Load raw JSON dict from config file."""
        path = Config.config_path()
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    @staticmethod
    def save_raw(data: dict[str, Any]):
        """Write raw JSON dict to config file."""
        path = Config.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
