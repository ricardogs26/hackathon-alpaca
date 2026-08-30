"""Dry-run en vivo: chain real + LLM real + gates. NO envia orden."""
from optionwright.options import select as S
S.MIN_VOLUME = 0  # fin de semana: sin volumen; OI + ancho siguen filtrando

from optionwright.broker.alpaca import fetch_chain, nearest_expiry, get_spot
from optionwright.options.models import Direction, Right
from optionwright.options.select import build_spread
from optionwright.agent.analyzer import propose
from optionwright.policy.gates import PolicyState, evaluate

UND = "SPY"
spot = get_spot(UND)
exp = nearest_expiry(UND, min_days=1, max_days=10)
puts = fetch_chain(UND, exp, Right.PUT)
calls = fetch_chain(UND, exp, Right.CALL)
bull_put = build_spread(puts, Direction.BULLISH, exp)
bear_call = build_spread(calls, Direction.BEARISH, exp)

def summ(sp):
    if not sp: return None
    return {"short": sp.short_leg.strike, "long": sp.long_leg.strike,
            "short_delta": round(sp.short_leg.delta,3), "credit": sp.credit,
            "max_loss": sp.max_loss, "reward_risk": sp.reward_risk}

ctx = {"underlying": UND, "expiry": exp, "spot": round(spot,2),
       "bull_put_spread": summ(bull_put), "bear_call_spread": summ(bear_call)}
print(f"SPY spot {spot:.2f} | exp {exp}")
print("bull_put :", summ(bull_put))
print("bear_call:", summ(bear_call))

print("\n=== LLM (qwen3.5:9b) decidiendo... ===")
p = propose(ctx)
print(f"direction : {p.direction.value}")
print(f"confidence: {p.confidence}")
print(f"rationale : {p.rationale}")

chosen = bull_put if p.direction is Direction.BULLISH else bear_call if p.direction is Direction.BEARISH else None
if chosen is None:
    print("\n=> ABSTENCION: no se abre nada.")
else:
    state = PolicyState(equity=100_000, open_positions=0, consecutive_losses=0, premium_at_risk_today=0.0)
    v = evaluate(chosen, 100, state)
    print(f"\n=== GATES (equity 100k) ===\napproved: {v.approved} | contracts: {v.contracts}\nreason: {v.reason}")
    if v.approved:
        print(f"\n=> HARIA: {p.direction.value} {chosen.right.value} spread "
              f"{chosen.short_leg.strike:.0f}/{chosen.long_leg.strike:.0f} x{v.contracts} "
              f"@ credit {chosen.credit} | riesgo total ${chosen.max_loss*v.contracts:.0f}")
        print("   (NO enviado — dry-run, mercado cerrado)")
