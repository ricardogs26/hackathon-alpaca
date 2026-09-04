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


def compute_intraday(
    bars: list[dict],
    spot: float,
    *,
    trend_pct: float = 0.25,
    vol_high_pct: float = 1.2,
    trend_bars: int = 30,
) -> dict:
    """
    Señales INTRADÍA (fase 3) a partir de las barras de 1 minuto de la sesión en
    curso: VWAP y posición del precio respecto a él, tendencia de los últimos
    `trend_bars` minutos, volatilidad realizada intradía (anualizada a escala
    diaria: desviación de retornos de 1 min × √390) y rango del día. La
    percepción diaria a 5 días vendió calls en el piso del retroceso del 1-2 de
    septiembre de 2026; estas señales ven el giro el mismo día.

    `bars`: dicts con open/high/low/close/volume, cronológicos. {} si faltan.
    """
    if not bars or spot <= 0 or len(bars) < 5:
        return {}
    closes = [float(b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0.0) for b in bars]
    typical = [(float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0 for b in bars]
    vol_sum = sum(vols)
    vwap = sum(t * v for t, v in zip(typical, vols)) / vol_sum if vol_sum > 0 else sum(closes) / len(closes)
    vs_vwap = (spot - vwap) / vwap * 100 if vwap else 0.0
    ref = closes[-trend_bars] if len(closes) >= trend_bars else closes[0]
    pct_30m = (spot - ref) / ref * 100 if ref else 0.0
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1]]
    vol = statistics.pstdev(rets) * (390 ** 0.5) * 100 if len(rets) >= 2 else 0.0
    day_open = float(bars[0]["open"])
    hi = max(float(b["high"]) for b in bars)
    lo = min(float(b["low"]) for b in bars)
    if pct_30m >= trend_pct:
        tendencia = "alza"
    elif pct_30m <= -trend_pct:
        tendencia = "baja"
    else:
        tendencia = "lateral"
    return {
        "vwap": round(vwap, 2),
        "vs_vwap_pct": round(vs_vwap, 2),
        "sobre_vwap": spot > vwap,
        "pct_30m": round(pct_30m, 2),
        "tendencia_30m": tendencia,
        "vol_intradia_pct": round(vol, 2),
        "regimen_intradia": "volatil" if vol > vol_high_pct else "tranquilo",
        "rango_dia_pct": round((hi - lo) / day_open * 100, 2) if day_open else 0.0,
    }


def merge_signals(daily: dict, intraday: dict) -> dict:
    """Una sola vista: lo diario + lo intradía. `regimen` es volátil si CUALQUIERA
    de los dos lo es; el LLM lee `regimen` y el código elige delta por él."""
    out = dict(daily)
    out.update(intraday)
    if intraday:
        d = daily.get("regimen", "tranquilo")
        out["regimen"] = "volatil" if "volatil" in (d, intraday.get("regimen_intradia")) else "tranquilo"
    return out
