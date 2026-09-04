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

    # ── Agent behavior ────────────────────────────────────────────────────────
    cycle_seconds: int = Field(default=180, alias="CYCLE_SECONDS")  # entries pass: chains + LLM
    exit_check_seconds: int = Field(default=60, alias="EXIT_CHECK_SECONDS")  # exits pass: quotes only
    underlyings: str = Field(default="SPY,QQQ,IWM", alias="UNDERLYINGS")   # used only when UNDERLYING_GROUPS is empty
    # Correlation groups (phase 2): "name:SYM,SYM;name:SYM". Caps and cooldowns
    # count per group and rule parameters may carry a per-group value. The
    # 4-Sep-2026 liquidity probe: sector ETFs, TLT and GLD have no tradable
    # 2-3-session spreads (OI 20-140, bid-ask 27-86%); megacaps do.
    underlying_groups: str = Field(default="index:SPY,QQQ,IWM;megacap:AAPL,NVDA,AMZN,TSLA", alias="UNDERLYING_GROUPS")
    chain_prefetch_workers: int = Field(default=3, alias="CHAIN_PREFETCH_WORKERS")
    # ── Rule parameters: SEEDS ONLY ───────────────────────────────────────────
    # Since 0.5.0 the gates and the exits read their thresholds from the `rules`
    # table in Postgres (scopes global / group / underlying, with history). These
    # env values seed the global scope on first start; editing them afterwards
    # does nothing once the row exists — change rules through the API or SQL.
    # Registry with types, bounds and meaning: optionwright/policy/params.py.
    # exits
    stop_delta: float = Field(default=0.45, alias="STOP_DELTA")
    stop_mult: float = Field(default=1.0, alias="STOP_LOSS_MULT")
    take_profit_far: float = Field(default=0.50, alias="TAKE_PROFIT_FAR")
    take_profit_near: float = Field(default=0.25, alias="TAKE_PROFIT_NEAR")
    take_profit_step_hours: float = Field(default=24.0, alias="TAKE_PROFIT_STEP_HOURS")
    trail_activation: float = Field(default=0.30, alias="TRAIL_ACTIVATION")
    trail_giveback: float = Field(default=0.07, alias="TRAIL_GIVEBACK")
    trail_vol_ref_pct: float = Field(default=0.8, alias="TRAIL_VOL_REF_PCT")
    overnight_mode: str = Field(default="flat", alias="OVERNIGHT_MODE")
    flatten_minutes_before_close: float = Field(default=30.0, alias="FLATTEN_MINUTES_BEFORE_CLOSE")
    overnight_max_short_delta: float = Field(default=0.35, alias="OVERNIGHT_MAX_SHORT_DELTA")
    overnight_net_delta_pct: float = Field(default=0.03, alias="OVERNIGHT_NET_DELTA_PCT")
    # entries
    expiry_min_days: int = Field(default=2, alias="EXPIRY_MIN_DAYS")   # trading sessions
    expiry_max_days: int = Field(default=3, alias="EXPIRY_MAX_DAYS")
    max_open_positions: int = Field(default=6, alias="MAX_OPEN_POSITIONS")
    max_per_underlying: int = Field(default=2, alias="MAX_PER_UNDERLYING")
    max_per_group: int = Field(default=2, alias="MAX_PER_GROUP")
    group_cooldown_seconds: float = Field(default=1800.0, alias="GROUP_COOLDOWN_SECONDS")
    # selection
    short_delta: float = Field(default=0.30, alias="SHORT_DELTA")
    short_delta_volatile: float = Field(default=0.20, alias="SHORT_DELTA_VOLATILE")
    volatile_mode: str = Field(default="neutral", alias="VOLATILE_MODE")
    intraday_trend_pct: float = Field(default=0.25, alias="INTRADAY_TREND_PCT")
    intraday_vol_high_pct: float = Field(default=1.2, alias="INTRADAY_VOL_HIGH_PCT")
    width_pct: float = Field(default=0.0065, alias="WIDTH_PCT")
    width_tolerance: float = Field(default=0.5, alias="WIDTH_TOLERANCE")
    min_open_interest: int = Field(default=100, alias="MIN_OPEN_INTEREST")
    max_quote_spread_pct: float = Field(default=0.15, alias="MAX_QUOTE_SPREAD_PCT")
    max_loss_pct: float = Field(default=0.01, alias="MAX_LOSS_PCT")
    daily_budget_pct: float = Field(default=0.05, alias="DAILY_BUDGET_PCT")
    cooldown_seconds: float = Field(default=2700.0, alias="COOLDOWN_SECONDS")
    max_consecutive_losses: int = Field(default=3, alias="MAX_CONSECUTIVE_LOSSES")
    breaker_lookback_hours: float = Field(default=24.0, alias="BREAKER_LOOKBACK_HOURS")
    max_daily_loss_pct: float = Field(default=0.02, alias="MAX_DAILY_LOSS_PCT")
    opening_blackout_minutes: float = Field(default=30.0, alias="OPENING_BLACKOUT_MINUTES")
    no_entry_minutes_before_close: float = Field(default=60.0, alias="NO_ENTRY_MINUTES_BEFORE_CLOSE")
    macro_blackout_minutes: float = Field(default=60.0, alias="MACRO_BLACKOUT_MINUTES")
    min_confidence: float = Field(default=0.6, alias="MIN_CONFIDENCE")
    max_direction_share: float = Field(default=0.60, alias="MAX_DIRECTION_SHARE")
    max_net_delta_pct: float = Field(default=0.03, alias="MAX_NET_DELTA_PCT")
    min_reward_risk: float = Field(default=0.20, alias="MIN_REWARD_RISK")
    # Bearer token required by PATCH /api/rules and the proposal decisions. Empty = disabled.
    rules_token: str = Field(default="", alias="RULES_TOKEN")
    # ── Order lifecycle (tech-debt 1.1) ──────────────────────────────────────
    entry_fill_wait_s: float = Field(default=10.0, alias="ENTRY_FILL_WAIT_S")        # wait this long for an entry to fill
    entry_order_max_age_s: float = Field(default=120.0, alias="ENTRY_ORDER_MAX_AGE_S")  # then cancel a pending entry
    close_fill_wait_s: float = Field(default=5.0, alias="CLOSE_FILL_WAIT_S")
    close_order_max_age_s: float = Field(default=60.0, alias="CLOSE_ORDER_MAX_AGE_S")   # then cancel and retry wider
    close_limit_step: float = Field(default=0.05, alias="CLOSE_LIMIT_STEP")            # limit = price + step × (attempts + 1)
    close_limit_max_steps: int = Field(default=6, alias="CLOSE_LIMIT_MAX_STEPS")
    reconcile_alert_minutes: float = Field(default=30.0, alias="RECONCILE_ALERT_MINUTES")  # WhatsApp at most this often
    # ── Phase 4: nightly statistical memory ──────────────────────────────────
    learning_cron_utc: str = Field(default="30 22 * * 1-5", alias="LEARNING_CRON_UTC")   # 17:30 CST, weekdays; "" = off
    whatsapp_send_url: str = Field(default="", alias="WHATSAPP_SEND_URL")   # Amael bridge, e.g. http://whatsapp-bridge-service.amael-ia:3000
    whatsapp_to: str = Field(default="", alias="WHATSAPP_TO")               # destination number; empty = notifications off
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Contexto agéntico: percepción + memoria + portafolio inyectados al LLM
    agent_rich_context: bool = Field(default=True, alias="AGENT_RICH_CONTEXT")
    # perception thresholds — seeds for the rules table (per-group values live there)
    trend_flat_pct: float = Field(default=1.0, alias="PERCEPTION_TREND_FLAT_PCT")
    vol_high_pct: float = Field(default=1.2, alias="PERCEPTION_VOL_HIGH_PCT")

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
    def universe(self):
        from optionwright.universe import flat_universe, parse_groups

        if self.underlying_groups.strip():
            return parse_groups(self.underlying_groups)
        return flat_universe([s.strip().upper() for s in self.underlyings.split(",") if s.strip()])

    @property
    def underlyings_list(self) -> list[str]:
        return self.universe.symbols

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
