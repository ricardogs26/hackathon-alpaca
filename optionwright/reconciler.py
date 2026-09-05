"""
Automated reconciler (0.11.0). When the DB book and the broker book disagree,
decide — deterministically and with the broker as the source of truth — what
the DB must look like, using the broker's own evidence: the order history and
the account activities (fills, expirations, assignments, exercises).

One rule above all: the reconciler only ever makes the DATABASE match the
BROKER. It never places an order. What it cannot explain from evidence, or
what would need an order to be safe (a single naked leg), goes to a human.

Pure: `resolve()` takes the live DB rows, the broker's legs, the orders and the
activities, and returns Resolutions. The runner applies them and logs each one.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from optionwright.agent.state import parse_occ

HUMAN = "human"


@dataclass(frozen=True)
class Resolution:
    kind: str                      # close_fill | close_expired | adjust_qty | adopt | reactivate | human
    symbols: tuple[str, ...]
    evidence: str
    position_id: int | None = None
    payload: dict = field(default_factory=dict)
    urgent: bool = False

    def as_row(self) -> dict:
        return asdict(self)


def _ts(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _legs(order: dict) -> dict[str, str]:
    """{symbol: side} of a filled multi-leg order."""
    return {leg["symbol"]: str(leg.get("side", "")).lower() for leg in order.get("legs", [])}


def _find_order(orders: list[dict], want: dict[str, str], after: datetime | None) -> dict | None:
    """The latest FILLED order whose legs are exactly `want` ({symbol: 'buy'|'sell'}) submitted after `after`."""
    hits = []
    for o in orders:
        if str(o.get("status", "")).lower() != "filled":
            continue
        if _legs(o) != want:
            continue
        t = _ts(o.get("submitted_at"))
        if after is not None and t is not None and t < after:
            continue
        hits.append((t or datetime.min, o))
    return max(hits, key=lambda x: x[0])[1] if hits else None


def _acts(activities: list[dict], symbols: set[str], types: tuple[str, ...]) -> list[dict]:
    return [a for a in activities if a.get("symbol") in symbols and str(a.get("type", "")).upper() in types]


def resolve(live_rows: list[dict], broker: dict[str, int], orders: list[dict], activities: list[dict],
            unfilled_rows: list[dict] | None = None) -> list[Resolution]:
    """
    live_rows: DB positions with status open/closing (id, short_symbol, long_symbol, contracts, credit,
               fill_credit, ts_open, underlying, expiry, option_right).
    broker:    {option symbol: signed contracts} at the broker.
    orders:    dicts {id, status, filled_avg_price, submitted_at, legs:[{symbol, side, filled_qty}]}.
    activities: dicts {type, symbol, qty, price, side, time}.
    unfilled_rows: DB rows the agent gave up on (unfilled/pending) — candidates to reactivate.
    """
    out: list[Resolution] = []
    remaining = dict(broker)

    for r in live_rows:
        S, L, n = r["short_symbol"], r["long_symbol"], int(r["contracts"])
        bs, bl = int(remaining.get(S, 0)), int(remaining.get(L, 0))
        opened = _ts(r.get("ts_open"))
        if bs <= -n and bl >= n:                                   # legs present: consume them
            remaining[S] = bs + n
            remaining[L] = bl - n
            continue
        if bs == 0 and bl == 0:                                    # both legs gone
            close = _find_order(orders, {S: "buy", L: "sell"}, opened)
            if close is not None and close.get("filled_avg_price") is not None:
                out.append(Resolution("close_fill", (S, L), f"closing order {close['id']} filled at {close['filled_avg_price']}",
                                      r.get("id"), {"fill_price": float(close["filled_avg_price"]), "reason": "reconciled: close order found at broker"}))
                continue
            dangerous = _acts(activities, {S, L}, ("OPASN", "OPEXC"))
            if dangerous:
                out.append(Resolution(HUMAN, (S, L), f"assignment/exercise activity on {sorted({a['symbol'] for a in dangerous})}: "
                                      "a stock position may exist; needs an order", r.get("id"), urgent=True))
                continue
            expired = _acts(activities, {S, L}, ("OPEXP",))
            if {a["symbol"] for a in expired} >= {S, L}:
                out.append(Resolution("close_expired", (S, L), "both legs expired (OPEXP)", r["id"],
                                      {"fill_price": 0.0, "reason": "reconciled: expired worthless"}))
                continue
            out.append(Resolution(HUMAN, (S, L), "legs gone at the broker; no closing order, expiration or assignment explains it", r.get("id")))
            continue
        if bs < 0 and bl > 0 and -bs == bl and -bs < n:            # partial: fewer contracts than the DB says
            m = bl
            remaining[S] = bs + m
            remaining[L] = bl - m
            out.append(Resolution("adjust_qty", (S, L), f"broker holds {m} of {n} contracts", r.get("id"), {"contracts": m}))
            continue
        out.append(Resolution(HUMAN, (S, L), f"unexpected legs at the broker: {S}={bs}, {L}={bl} for a {n}-lot spread "
                              "(single leg?) — needs a person and possibly an order", r.get("id"), urgent=True))

    # Legs the DB does not know about: pair them into spreads and look for the agent's own filled entry.
    leftovers = {sym: q for sym, q in remaining.items() if q != 0}
    used: set[str] = set()
    unfilled_by_legs = {(u["short_symbol"], u["long_symbol"]): u for u in (unfilled_rows or [])}
    for S, qs in sorted(leftovers.items()):
        if qs >= 0 or S in used:
            continue
        try:
            root, expiry, right, ks = parse_occ(S)
        except ValueError:
            continue
        partner = None
        for L, ql in leftovers.items():
            if L in used or ql <= 0 or ql != -qs:
                continue
            try:
                r2, e2, rt2, kl = parse_occ(L)
            except ValueError:
                continue
            if r2 == root and e2 == expiry and rt2 == right and ((right == "C" and kl > ks) or (right == "P" and kl < ks)):
                partner = L
                break
        if partner is None:
            out.append(Resolution(HUMAN, (S,), f"short leg {S} ({qs}) with no protective long leg at the broker — naked; needs an order", urgent=True))
            used.add(S)
            continue
        used.update({S, partner})
        n = -qs
        entry = _find_order(orders, {S: "sell", partner: "buy"}, None)
        if entry is None or entry.get("filled_avg_price") is None:
            out.append(Resolution(HUMAN, (S, partner), f"spread {S}/{partner} x{n} at the broker with no filled entry order of the agent — unknown origin"))
            continue
        payload = {"underlying": root, "expiry": expiry, "option_right": "call" if right == "C" else "put",
                   "short_symbol": S, "long_symbol": partner, "contracts": n,
                   "fill_credit": float(entry["filled_avg_price"]), "order_id": entry["id"]}
        prev = unfilled_by_legs.get((S, partner))
        if prev is not None:
            out.append(Resolution("reactivate", (S, partner), f"entry {entry['id']} did fill at {entry['filled_avg_price']}; DB row #{prev['id']} had given up on it",
                                  prev["id"], payload))
        else:
            out.append(Resolution("adopt", (S, partner), f"entry {entry['id']} filled at {entry['filled_avg_price']}; not in the DB", None, payload))
    for L, ql in sorted(leftovers.items()):
        if ql > 0 and L not in used:
            out.append(Resolution(HUMAN, (L,), f"long leg {L} (+{ql}) with no short at the broker — harmless but unexplained"))
    return out
