"""Sonda de arranque: verifica cuenta, nivel de opciones y lectura del chain."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
KEY = os.environ["ALPACA_API_KEY"]
SEC = os.environ["ALPACA_SECRET_KEY"]

from alpaca.trading.client import TradingClient

tc = TradingClient(KEY, SEC, paper=True)
acct = tc.get_account()
print("=== CUENTA ===")
print("account_number      :", acct.account_number)
print("status              :", acct.status)
print("equity              :", acct.equity)
print("cash                :", acct.cash)
print("buying_power        :", acct.buying_power)
print("options_approved_lvl:", getattr(acct, "options_approved_level", None))
print("options_trading_lvl :", getattr(acct, "options_trading_level", None))
