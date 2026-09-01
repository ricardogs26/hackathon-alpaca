"""
Percepción de mercado: el CÓDIGO (no el LLM) resume la acción del precio en
señales numéricas + banderas categóricas. El LLM lee categorías ("alza"/"baja")
para no caer en comparaciones numéricas alucinadas. Función pura y testeable.
"""
from __future__ import annotations

import statistics


def compute_signals(
    closes: list[float],
    spot: float,
    *,
    trend_flat_pct: float = 1.0,
    vol_high_pct: float = 1.2,
    sma_short: int = 5,
    sma_medium: int = 20,
) -> dict:
    """Resume cierres diarios (viejo→nuevo) + spot en señales. {} si faltan datos."""
    if not closes or len(closes) < sma_short + 1 or spot <= 0:
        return {}

    ref = closes[-sma_short]
    pct_5d = (spot - ref) / ref * 100 if ref else 0.0
    sma_c = sum(closes[-sma_short:]) / sma_short
    med_n = min(sma_medium, len(closes))
    sma_m = sum(closes[-med_n:]) / med_n
    rets = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]
    vol = statistics.pstdev(rets) * 100 if len(rets) >= 2 else 0.0

    if pct_5d >= trend_flat_pct:
        tendencia = "alza"
    elif pct_5d <= -trend_flat_pct:
        tendencia = "baja"
    else:
        tendencia = "lateral"

    return {
        "pct_5d": round(pct_5d, 2),
        "sma_corta": round(sma_c, 2),
        "sma_media": round(sma_m, 2),
        "vol_realizada_pct": round(vol, 2),
        "tendencia_5d": tendencia,
        "momentum_positivo": spot > sma_c,
        "regimen": "volatil" if vol > vol_high_pct else "tranquilo",
    }
