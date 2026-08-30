"""Sonda end-to-end (lectura): fetch_chain real -> build_spread -> spread armado."""
from optionwright.broker.alpaca import fetch_chain, nearest_expiry, get_spot
from optionwright.options.models import Direction, Right
from optionwright.options.select import build_spread, liquid_contracts

UND = "SPY"
spot = get_spot(UND)
exp = nearest_expiry(UND, min_days=1, max_days=10)
print(f"{UND} spot ~ {spot:.2f} | expiracion elegida: {exp}")

puts = fetch_chain(UND, exp, Right.PUT)
print(f"fetch_chain -> {len(puts)} puts con quote+greeks")
liq = liquid_contracts(puts, Right.PUT, exp)
print(f"liquidos (OI/vol/ancho): {len(liq)} de {len(puts)}")

spread = build_spread(puts, Direction.BULLISH, exp, short_delta=0.30, width=5.0)
if spread is None:
    print("\nNo se armo spread (esperable fin de semana: volumen=0 tumba el gate de liquidez).")
    # Reintento ignorando el gate de volumen para ver la geometria:
    from optionwright.options import select as S
    S.MIN_VOLUME = 0
    spread = build_spread(puts, Direction.BULLISH, exp, short_delta=0.30, width=5.0)
    print("Reintento con MIN_VOLUME=0 (solo para inspeccionar la geometria):")

if spread:
    print("\n=== BULL PUT SPREAD ===")
    print(f"short: {spread.short_leg.strike:.0f}P  d={spread.short_leg.delta:.3f}  mid={spread.short_leg.mid}")
    print(f"long : {spread.long_leg.strike:.0f}P  d={spread.long_leg.delta:.3f}  mid={spread.long_leg.mid}")
    print(f"width={spread.width}  credit={spread.credit}  max_loss=${spread.max_loss}  max_profit=${spread.max_profit}  R/R={spread.reward_risk}")
