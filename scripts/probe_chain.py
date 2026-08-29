"""Sonda del option chain: lee puts de SPY con delta, bid/ask y open interest."""
import os
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
KEY = os.environ["ALPACA_API_KEY"]
SEC = os.environ["ALPACA_SECRET_KEY"]

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType, AssetStatus
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest

tc = TradingClient(KEY, SEC, paper=True)

# spot de SPY
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
sc = StockHistoricalDataClient(KEY, SEC)
spot = sc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols="SPY"))["SPY"].price
print(f"SPY spot ~ {spot:.2f}")

# contratos put cercanos, expiracion a <= 10 dias
today = date.today()
req = GetOptionContractsRequest(
    underlying_symbols=["SPY"],
    status=AssetStatus.ACTIVE,
    type=ContractType.PUT,
    expiration_date_gte=today,
    expiration_date_lte=today + timedelta(days=10),
    strike_price_gte=str(spot - 20),
    strike_price_lte=str(spot + 2),
    limit=100,
)
contracts = tc.get_option_contracts(req).option_contracts
exps = sorted({c.expiration_date for c in contracts})
print("expiraciones disponibles (<=10d):", [str(e) for e in exps][:6])
target_exp = exps[0]
near = [c for c in contracts if c.expiration_date == target_exp]
print(f"expiracion elegida: {target_exp} — {len(near)} puts")

# quotes + greeks del chain (feed indicative = gratis)
odc = OptionHistoricalDataClient(KEY, SEC)
chain = odc.get_option_chain(OptionChainRequest(underlying_symbol="SPY", feed="indicative"))
print(f"\nchain devuelto: {len(chain)} contratos con quote/greeks")

rows = []
for c in near:
    snap = chain.get(c.symbol)
    if not snap or not snap.latest_quote:
        continue
    q = snap.latest_quote
    delta = snap.greeks.delta if snap.greeks else None
    oi = c.open_interest
    rows.append((float(c.strike_price), q.bid_price, q.ask_price, delta, oi))

rows.sort()
print(f"\n{'strike':>8} {'bid':>7} {'ask':>7} {'delta':>7} {'OI':>7}")
for strike, bid, ask, delta, oi in rows:
    d = f"{delta:.3f}" if delta is not None else "  -  "
    print(f"{strike:>8.0f} {bid:>7.2f} {ask:>7.2f} {d:>7} {str(oi or '-'):>7}")
print(f"\n✓ chain legible: {len(rows)} puts con precio en {target_exp}")
