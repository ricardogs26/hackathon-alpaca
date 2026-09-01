"""
Central configuration for optionwright. Single source of truth, read from the
environment via pydantic-settings. See .env.example for every variable.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Alpaca (paper only) ───────────────────────────────────────────────────
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field(default="", alias="ALPACA_SECRET_KEY")
    alpaca_paper: bool = Field(default=True, alias="ALPACA_PAPER")

    # ── LLM (OpenAI-compatible) ───────────────────────────────────────────────
    llm_base_url: str = Field(default="http://localhost:11434/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="not-needed", alias="LLM_API_KEY")
    llm_model: str = Field(default="qwen3.5:9b", alias="LLM_MODEL")
    llm_timeout_seconds: int = Field(default=60, alias="LLM_TIMEOUT_SECONDS")
    # Ollama serves qwen3.5 in "thinking" mode by default (~22s/call and it can
    # eat the whole token budget on hidden reasoning, returning empty content).
    # Its OpenAI-compatible endpoint ignores think=false, so for Ollama we call
    # the NATIVE /api/chat (think=false -> ~0.6s). Set false to use the plain
    # OpenAI-compatible path for Featherless / real OpenAI / other hosts.
    llm_native_ollama: bool = Field(default=True, alias="LLM_NATIVE_OLLAMA")
    # Optional fallback: a local Ollama used only if the primary endpoint (e.g.
    # Featherless) fails or returns empty. Empty base_url disables the fallback.
    llm_fallback_base_url: str = Field(default="", alias="FALLBACK_LLM_BASE_URL")
    llm_fallback_model: str = Field(default="qwen3.5:9b", alias="FALLBACK_LLM_MODEL")

    # ── Datastores ────────────────────────────────────────────────────────────
    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="optionwright", alias="POSTGRES_DB")
    postgres_user: str = Field(default="optionwright", alias="POSTGRES_USER")
    postgres_password: str = Field(default="change-me", alias="POSTGRES_PASSWORD")
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")

    # ── Agent behavior ────────────────────────────────────────────────────────
    cycle_seconds: int = Field(default=300, alias="CYCLE_SECONDS")
    underlyings: str = Field(default="SPY,QQQ,IWM", alias="UNDERLYINGS")
    # Exit management: trailing take-profit + stop + hard cap
    stop_loss_mult: float = Field(default=2.0, alias="STOP_LOSS_MULT")
    hard_take_profit: float = Field(default=0.60, alias="HARD_TAKE_PROFIT")
    trail_activation: float = Field(default=0.20, alias="TRAIL_ACTIVATION")
    trail_giveback: float = Field(default=0.10, alias="TRAIL_GIVEBACK")
    expiry_min_days: int = Field(default=3, alias="EXPIRY_MIN_DAYS")   # 3-5 DTE
    expiry_max_days: int = Field(default=5, alias="EXPIRY_MAX_DAYS")
    # Risk gates — deployment raised to ~20% (6 positions x ~3.3%) across 3 symbols
    max_open_positions: int = Field(default=6, alias="MAX_OPEN_POSITIONS")
    max_per_underlying: int = Field(default=2, alias="MAX_PER_UNDERLYING")  # anti-concentration
    max_loss_pct: float = Field(default=0.033, alias="MAX_LOSS_PCT")
    cooldown_seconds: float = Field(default=2700.0, alias="COOLDOWN_SECONDS")  # 45 min
    daily_budget_pct: float = Field(default=0.22, alias="DAILY_BUDGET_PCT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }

    @field_validator("alpaca_paper")
    @classmethod
    def _refuse_live(cls, v: bool) -> bool:
        # Hard guardrail: this project is paper-only by design.
        if v is False:
            raise ValueError("ALPACA_PAPER=false is not allowed — optionwright is paper-only")
        return v

    @property
    def underlyings_list(self) -> list[str]:
        return [s.strip().upper() for s in self.underlyings.split(",") if s.strip()]

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
