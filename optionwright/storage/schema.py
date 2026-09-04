"""Postgres DDL for optionwright. Idempotent; run at startup."""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    underlying    TEXT NOT NULL,
    direction     TEXT NOT NULL,              -- bullish | bearish | abstain
    confidence    DOUBLE PRECISION,
    rationale     TEXT,
    approved      BOOLEAN NOT NULL,
    contracts     INTEGER NOT NULL DEFAULT 0,
    reason        TEXT,                        -- the gate verdict reason
    spread        JSONB,                       -- legs, strikes, credit, max_loss
    position_id   BIGINT
);

CREATE TABLE IF NOT EXISTS positions (
    id            BIGSERIAL PRIMARY KEY,
    ts_open       TIMESTAMPTZ NOT NULL DEFAULT now(),
    underlying    TEXT NOT NULL,
    expiry        DATE NOT NULL,
    option_right  TEXT NOT NULL,              -- "right" is a reserved word in SQL
    short_symbol  TEXT NOT NULL,
    long_symbol   TEXT NOT NULL,
    contracts     INTEGER NOT NULL,
    credit        DOUBLE PRECISION NOT NULL,
    max_loss      DOUBLE PRECISION NOT NULL,   -- total, all contracts
    order_id      TEXT,
    status        TEXT NOT NULL DEFAULT 'open',-- open | closed
    ts_close      TIMESTAMPTZ,
    realized_pnl  DOUBLE PRECISION,
    exit_reason   TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_underlying ON positions(underlying);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS peak_captured DOUBLE PRECISION DEFAULT 0;

-- Phase 0 instrumentation: one row per open position per exits tick (~60s).
-- The state a premium seller reads (delta, sigma distance, time left, sleeps)
-- next to the P&L. Prometheus keeps the same gauge only for a few days; this
-- table is the ground truth the state-based rules engine is designed on.
CREATE TABLE IF NOT EXISTS position_ticks (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    position_id     BIGINT NOT NULL,
    underlying      TEXT NOT NULL,
    spot            DOUBLE PRECISION,
    credit          DOUBLE PRECISION NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    captured        DOUBLE PRECISION NOT NULL,
    peak_captured   DOUBLE PRECISION NOT NULL,
    pnl_now         DOUBLE PRECISION NOT NULL,
    short_strike    DOUBLE PRECISION NOT NULL,
    option_right    TEXT NOT NULL,
    short_delta     DOUBLE PRECISION,
    short_iv        DOUBLE PRECISION,
    sigma_dist      DOUBLE PRECISION,
    hours_to_expiry DOUBLE PRECISION NOT NULL,
    hours_to_close  DOUBLE PRECISION,
    sleeps_tonight  BOOLEAN,
    decision        TEXT NOT NULL,
    reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticks_position ON position_ticks(position_id, ts);

-- Phase 1: rule parameters with precedence (global / group:<name> / underlying:<SYM>)
-- and a history of every change. The environment only seeds the global scope.
CREATE TABLE IF NOT EXISTS rules (
    scope       TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS rules_history (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    scope       TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    reason      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_curve (
    ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    equity   DOUBLE PRECISION NOT NULL,
    cash     DOUBLE PRECISION
);
"""
