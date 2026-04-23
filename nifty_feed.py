# instruments.py
"""
Run this script once before starting feed.py to generate nifty_sensex_instruments.json

It saves:
  - Futures (nearest expiry)
  - options_week1  →  nearest/current-week expiry options
  - options_week2  →  next-week expiry options

Usage:
    python c:\\nifty\\instruments.py
"""

import os
import json
from datetime import datetime
from kiteconnect import KiteConnect
from dotenv import load_dotenv

load_dotenv(dotenv_path='C:/nifty/.env', override=True)

API_KEY    = os.getenv("KITE_API_KEY")
TOKEN_FILE = "C:/nifty/token_store.json"
OUT_FILE   = "C:/nifty/nifty_sensex_instruments.json"

# How many strikes around ATM to keep (reduces file size)
# Set to None to keep all strikes
STRIKE_FILTER_RANGE = 6000   # keep strikes within ±6000 of a rough ATM
# Rough ATM estimates just for filtering — doesn't need to be exact
NIFTY_APPROX_SPOT  = 24000
SENSEX_APPROX_SPOT = 78000


def load_token():
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)


def filter_instruments(instruments, symbol_name, exchange):
    """Return all FUT and OPT contracts for the given symbol."""
    seg_fut = f"{exchange}-FUT"
    seg_opt = f"{exchange}-OPT"
    fut = [i for i in instruments
           if i["segment"] == seg_fut and i["name"] == symbol_name]
    opt = [i for i in instruments
           if i["segment"] == seg_opt and i["name"] == symbol_name]
    return fut, opt


def get_nearest_futures(futures_list):
    """Return the single nearest-expiry futures contract."""
    if not futures_list:
        return []
    sorted_fut = sorted(futures_list, key=lambda x: x["expiry"])
    nearest_expiry = sorted_fut[0]["expiry"]
    return [f for f in sorted_fut if f["expiry"] == nearest_expiry]


def get_two_expiry_options(options_list, approx_spot, strike_range):
    """
    Returns (week1_opts, week2_opts):
      week1 = options for the nearest (current-week) expiry
      week2 = options for the second-nearest (next-week) expiry

    Also filters strikes to ±strike_range around approx_spot to keep
    the JSON file small and subscription count manageable.
    """
    if not options_list:
        return [], []

    # Get all unique expiries sorted ascending
    expiries = sorted(set(o["expiry"] for o in options_list))

    if len(expiries) < 1:
        return [], []

    expiry_w1 = expiries[0]
    expiry_w2 = expiries[1] if len(expiries) > 1 else None

    def filter_by_expiry_and_strike(exp):
        if exp is None:
            return []
        opts = [o for o in options_list if o["expiry"] == exp]
        if strike_range and approx_spot:
            opts = [
                o for o in opts
                if abs(o["strike"] - approx_spot) <= strike_range
            ]
        return opts

    week1 = filter_by_expiry_and_strike(expiry_w1)
    week2 = filter_by_expiry_and_strike(expiry_w2)

    return week1, week2


def print_summary(name, fut, w1, w2):
    exp_w1 = w1[0]["expiry"] if w1 else "—"
    exp_w2 = w2[0]["expiry"] if w2 else "—"
    print(f"\n{'─'*50}")
    print(f"  {name}")
    print(f"{'─'*50}")
    print(f"  Futures      : {len(fut)} contract(s)  expiry={fut[0]['expiry'] if fut else '—'}")
    print(f"  Week 1 opts  : {len(w1)} contracts     expiry={exp_w1}")
    print(f"  Week 2 opts  : {len(w2)} contracts     expiry={exp_w2}")

    print(f"\n  Sample NIFTY futures tokens:")
    for f in fut[:3]:
        print(f"    {f['tradingsymbol']:<30} token={f['instrument_token']}  expiry={f['expiry']}")

    if w1:
        print(f"\n  Sample Week1 option tokens (first 4):")
        for o in w1[:4]:
            print(f"    {o['tradingsymbol']:<35} token={o['instrument_token']}  strike={o['strike']}  type={o['instrument_type']}")

    if w2:
        print(f"\n  Sample Week2 option tokens (first 4):")
        for o in w2[:4]:
            print(f"    {o['tradingsymbol']:<35} token={o['instrument_token']}  strike={o['strike']}  type={o['instrument_type']}")


def main():
    token_data   = load_token()
    access_token = token_data["access_token"]

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(access_token)

    print("⬇️  Fetching NFO instruments …")
    instruments_nfo = kite.instruments("NFO")
    print(f"    Got {len(instruments_nfo)} NFO instruments")

    print("⬇️  Fetching BFO instruments …")
    instruments_bfo = kite.instruments("BFO")
    print(f"    Got {len(instruments_bfo)} BFO instruments")

    # ── NIFTY ────────────────────────────────────────────────────────────────
    nifty_fut_all, nifty_opt_all = filter_instruments(instruments_nfo, "NIFTY", "NFO")
    nifty_fut  = get_nearest_futures(nifty_fut_all)
    nifty_w1, nifty_w2 = get_two_expiry_options(
        nifty_opt_all, NIFTY_APPROX_SPOT, STRIKE_FILTER_RANGE
    )
    print_summary("NIFTY", nifty_fut, nifty_w1, nifty_w2)

    # ── SENSEX ───────────────────────────────────────────────────────────────
    sensex_fut_all, sensex_opt_all = filter_instruments(instruments_bfo, "SENSEX", "BFO")
    sensex_fut  = get_nearest_futures(sensex_fut_all)
    sensex_w1, sensex_w2 = get_two_expiry_options(
        sensex_opt_all, SENSEX_APPROX_SPOT, STRIKE_FILTER_RANGE
    )
    print_summary("SENSEX", sensex_fut, sensex_w1, sensex_w2)

    # ── Spot token reminder ───────────────────────────────────────────────────
    print("\n" + "─"*50)
    print("  Spot tokens (hardcoded in feed.py — no action needed):")
    print("    NIFTY  50  →  token 256265  (NSE)")
    print("    SENSEX     →  token 265     (BSE)")
    print("─"*50)

    # ── Save ─────────────────────────────────────────────────────────────────
    payload = {
        "nifty": {
            "futures":        nifty_fut,
            "options_week1":  nifty_w1,
            "options_week2":  nifty_w2,
        },
        "sensex": {
            "futures":        sensex_fut,
            "options_week1":  sensex_w1,
            "options_week2":  sensex_w2,
        },
    }

    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    total_tokens = (
        len(nifty_fut) + len(nifty_w1) + len(nifty_w2) +
        len(sensex_fut) + len(sensex_w1) + len(sensex_w2) +
        2   # spot tokens
    )
    print(f"\n✅ Saved to {OUT_FILE}")
    print(f"   Total tokens to subscribe: ~{total_tokens} (+ 2 spot tokens)")
    print("\n▶  Now run:  python c:\\nifty\\feed.py")


if __name__ == "__main__":
    main()