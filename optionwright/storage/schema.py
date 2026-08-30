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

CREATE TABLE IF NOT EXISTS equity_curve (
    ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    equity   DOUBLE PRECISION NOT NULL,
    cash     DOUBLE PRECISION
);
"""
