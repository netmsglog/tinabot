"""Configuration management using pydantic-settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProfileConfig(BaseModel):
    """A named model profile — complete model configuration as a unit."""

    model: str = "claude-opus-4-6"
    provider: str = "claude"  # "claude" or "openai" (also covers OpenAI-compatible APIs)
    auth: str = "oauth"  # "oauth" or "api_key"
    api_key: str = ""
    base_url: str = ""
    input_price: float = 0.0  # $/MTok for input tokens
    output_price: float = 0.0  # $/MTok for output tokens
    cache_read_price: float = 0.0  # $/MTok for cached input tokens


class AgentConfig(BaseModel):
    """Agent execution settings."""

    # Model-related fields (populated from active profile on load)
    provider: str = "claude"
    model: str = "claude-opus-4-6"
    auth: str = "oauth"  # "oauth" or "api_key"
    api_key: str = ""
    base_url: str = ""
    input_price: float = 5.0  # $/MTok
    output_price: float = 25.0  # $/MTok
    cache_read_price: float = 0.5  # $/MTok

    # Tool API keys
    tavily_api_key: str = ""  # Tavily API key for WebSearch tool

    # Execution settings
    max_thinking_tokens: int = 10000
    permission_mode: str = "acceptEdits"
    cwd: str = str(Path.home() / ".tinabot" / "workspace")
    max_tokens: int = 16384  # Max output tokens for OpenAI-compatible providers
    timeout_seconds: int = 3600  # Max time per agent call (0 = no limit)
    allowed_tools: list[str] = Field(default_factory=lambda: [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
        "WebSearch", "WebFetch", "Task",
    ])

    def apply_profile(self, profile: ProfileConfig):
        """Apply a profile's settings to this agent config."""
        self.model = profile.model
        self.provider = profile.provider
        self.auth = profile.auth
        self.api_key = profile.api_key
        self.base_url = profile.base_url
        self.input_price = profile.input_price
        self.output_price = profile.output_price
        self.cache_read_price = profile.cache_read_price

    def resolved_base_url(self) -> str:
        """Return the API base URL, auto-resolved from provider if not set."""
        if self.base_url:
            return self.base_url
        if self.provider == "openai":
            return "https://api.openai.com/v1"
        return ""

    @property
    def is_claude(self) -> bool:
        return self.provider == "claude"


class TelegramConfig(BaseModel):
    """Telegram bot settings."""

    enabled: bool = False
    token: str = ""
    allowed_users: list[int] = Field(default_factory=list)
    groq_api_key: str = ""  # For voice transcription (Whisper via Groq)


class WebConfig(BaseModel):
    """Web chat interface settings."""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    auth_token: str = ""  # Required for access; empty = deny all


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

    active_profile: str = ""
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)

    @classmethod
    def load(cls) -> Config:
        """Load config from file and environment.

        If an active_profile is set and exists in profiles, its fields
        are merged into the agent section before construction.
        """
        config_path = Config.config_path()
        file_data: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path) as f:
                file_data = json.load(f)

        # Resolve active profile into agent section
        active = file_data.get("active_profile", "")
        profiles_raw = file_data.get("profiles", {})
        if active and active in profiles_raw:
            p = ProfileConfig(**profiles_raw[active])
            agent = file_data.setdefault("agent", {})
            agent["model"] = p.model
            agent["provider"] = p.provider
            agent["auth"] = p.auth
            agent["api_key"] = p.api_key
            agent["base_url"] = p.base_url
            agent["input_price"] = p.input_price
            agent["output_price"] = p.output_price
            agent["cache_read_price"] = p.cache_read_price

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
