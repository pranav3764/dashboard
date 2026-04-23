import os
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from kiteconnect import KiteConnect, KiteTicker
from dotenv import load_dotenv

load_dotenv(dotenv_path=r'C:\nifty\.env', override=True)

# ─── Credentials ──────────────────────────────────────────────────────────────
API_KEY         = os.getenv("KITE_API_KEY")
TOKEN_FILE      = r"C:\nifty\token_store.json"
INSTRUMENT_FILE = r"C:\nifty\nifty_sensex_instruments.json"
DATA_FILE       = r"C:\nifty\live_data.json"
PREV_CLOSE_FILE = r"C:\nifty\prev_close.json"
STRADDLE_HISTORY_FILE = r"C:\nifty\straddle_history.json"

# ─── Spot instrument tokens ───────────────────────────────────────────────────
NIFTY_SPOT_TOKEN     = 256265
SENSEX_SPOT_TOKEN    = 265
BANKNIFTY_SPOT_TOKEN = 260105
INDIAVIX_SPOT_TOKEN  = 264969

KITE_SYMBOL_NIFTY     = "NSE:NIFTY 50"
KITE_SYMBOL_SENSEX    = "BSE:SENSEX"
KITE_SYMBOL_BANKNIFTY = "NSE:NIFTY BANK"

# ─── Configuration ────────────────────────────────────────────────────────────
STRIKE_INTERVAL_NIFTY  = 50
STRIKE_INTERVAL_SENSEX = 100
STRIKES_RANGE          = 5
OTM_LEVELS             = 7
REFRESH_INTERVAL       = 1

MARKET_OPEN_HOUR    = 9
MARKET_OPEN_MINUTE  = 15
MARKET_CLOSE_HOUR   = 15
MARKET_CLOSE_MINUTE = 30

PREMARKET_SEED_HOUR      = 9
PREMARKET_SEED_MIN_START = 8
PREMARKET_SEED_MIN_END   = 9

# Minimum seconds between chart history points per series
CHART_THROTTLE_SECONDS = 5
# Maximum points kept per series (safety cap)
CHART_MAX_POINTS = 5000

IST = timezone(timedelta(hours=5, minutes=30))

# Risk-free rate for IV calculation (approximate Indian 91-day T-bill yield)
RISK_FREE_RATE = 0.065

# ─── Helper: is market open? ──────────────────────────────────────────────────
def _is_market_open() -> bool:
    now = datetime.now(IST)
    return (now.hour, now.minute) >= (MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE)

# ─── Black-Scholes IV calculation ─────────────────────────────────────────────
def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erfc for no external dependencies."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _bs_price(S: float, K: float, T: float, r: float, sigma: float,
              option_type: str) -> float:
    """
    Black-Scholes option price.
    option_type: 'CE' or 'PE'
    """
    if T <= 0 or sigma <= 0:
        # At / past expiry — intrinsic value only
        intrinsic = max(S - K, 0) if option_type == "CE" else max(K - S, 0)
        return intrinsic

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    if option_type == "CE":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:  # PE
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def calc_greeks(S, K, T, r, sigma, option_type):
    if sigma is None or sigma <= 0 or T <= 0 or S <= 0:
        return None

    sqrt_T = math.sqrt(T)

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    # PDF (since you already have CDF)
    def norm_pdf(x):
        return (1 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x)

    if option_type == "CE":
        delta = _norm_cdf(d1)
        theta = (
            -S * norm_pdf(d1) * sigma / (2 * sqrt_T)
            - r * K * math.exp(-r * T) * _norm_cdf(d2)
        ) / 365
        rho = K * T * math.exp(-r * T) * _norm_cdf(d2) / 100
    else:
        delta = -_norm_cdf(-d1)
        theta = (
            -S * norm_pdf(d1) * sigma / (2 * sqrt_T)
            + r * K * math.exp(-r * T) * _norm_cdf(-d2)
        ) / 365
        rho = -K * T * math.exp(-r * T) * _norm_cdf(-d2) / 100

    gamma = norm_pdf(d1) / (S * sigma * sqrt_T)
    vega = S * norm_pdf(d1) * sqrt_T / 100

    return {
        "delta": delta,
        "theta": theta,
        "gamma": gamma,
        "vega": vega,
        "rho": rho,
    }


def calc_iv(option_price: float, S: float, K: float, T: float,
            r: float, option_type: str,
            lo: float = 1e-6, hi: float = 20.0,
            max_iter: int = 200, tol: float = 1e-5) -> float | None:
    """
    Bisection-based implied volatility solver.

    Returns annualised IV as a decimal (e.g. 0.18 means 18%) or None if:
      • inputs are invalid / zero
      • option_price is below intrinsic (no IV solution exists)
      • bisection does not converge within max_iter
    """
    if option_price is None or S is None or K is None:
        return None
    if option_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None

    # Price must exceed intrinsic for a real IV solution
    intrinsic = max(S - K, 0) if option_type == "CE" else max(K - S, 0)
    if option_price <= intrinsic:
        return None

    f_lo = _bs_price(S, K, T, r, lo, option_type) - option_price
    f_hi = _bs_price(S, K, T, r, hi, option_type) - option_price

    # Solution not bracketed — IV is outside [lo, hi]
    if f_lo * f_hi > 0:
        return None

    for _ in range(max_iter):
        mid   = (lo + hi) / 2.0
        f_mid = _bs_price(S, K, T, r, mid, option_type) - option_price
        if abs(f_mid) < tol or (hi - lo) / 2.0 < tol:
            return round(mid * 100, 2)   # return as percentage, e.g. 18.45
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo = mid
            f_lo = f_mid

    return None   # did not converge

def _time_to_expiry(expiry_str: str) -> float:
    """
    Returns time to expiry in years (T) from now.
    expiry_str is expected as '%d %b %Y', e.g. '17 Apr 2025'.
    Uses market-close time (15:30 IST) on expiry date as the expiry moment.
    Returns 0.0 if the expiry is today or in the past.
    """
    if not expiry_str or expiry_str == "N/A":
        return 0.0
    try:
        expiry_dt = datetime.strptime(expiry_str, "%d %b %Y")
        # Expiry moment = 15:30:00 IST on expiry date
        expiry_ist = expiry_dt.replace(
            hour=15, minute=30, second=0, microsecond=0,
            tzinfo=IST
        )
        now_ist  = datetime.now(IST)
        delta    = (expiry_ist - now_ist).total_seconds()
        return max(delta / (365.25 * 24 * 3600), 0.0)
    except Exception:
        return 0.0


# ─── VIX — LTP only ───────────────────────────────────────────────────────────
vix_ltp      = None
vix_ltp_lock = threading.Lock()

# ─── Loaders ──────────────────────────────────────────────────────────────────
def load_token():
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)

def load_instruments():
    with open(INSTRUMENT_FILE, "r") as f:
        return json.load(f)

# ─── Spot/index OHLC store ────────────────────────────────────────────────────
ohlc_store = {
    NIFTY_SPOT_TOKEN:     {"open": None, "high": None, "low": None, "prev_close": None, "seeded": False},
    SENSEX_SPOT_TOKEN:    {"open": None, "high": None, "low": None, "prev_close": None, "seeded": False},
    BANKNIFTY_SPOT_TOKEN: {"open": None, "high": None, "low": None, "prev_close": None, "seeded": False},
}
ohlc_lock = threading.Lock()

def seed_ohlc_from_rest(kite_obj, force=False):
    """Seed spot index OHLC from REST. Unchanged — correct for single instruments."""
    try:
        symbols = [KITE_SYMBOL_NIFTY, KITE_SYMBOL_SENSEX, KITE_SYMBOL_BANKNIFTY]
        quotes  = kite_obj.quote(symbols)
        mapping = {
            KITE_SYMBOL_NIFTY:     NIFTY_SPOT_TOKEN,
            KITE_SYMBOL_SENSEX:    SENSEX_SPOT_TOKEN,
            KITE_SYMBOL_BANKNIFTY: BANKNIFTY_SPOT_TOKEN,
        }
        with ohlc_lock:
            for sym, token in mapping.items():
                q = quotes.get(sym, {})
                if not q:
                    continue
                rec = ohlc_store[token]
                if rec["seeded"] and not force:
                    continue
                rest_open = q["ohlc"].get("open")
                rest_high = q["ohlc"].get("high")
                rest_low  = q["ohlc"].get("low")
                rest_prev = q["ohlc"].get("close")
                rest_ltp  = q.get("last_price")
                if not rest_open:
                    rest_open = rest_ltp
                rec["open"]       = rest_open if rest_open else rest_ltp
                rec["high"]       = rest_high if rest_high else rest_ltp
                rec["low"]        = rest_low  if rest_low  else rest_ltp
                rec["prev_close"] = rest_prev
                rec["seeded"]     = True
                print(
                    f"[SEED] {sym}: O={rec['open']}  H={rec['high']}  "
                    f"L={rec['low']}  PC={rec['prev_close']}"
                )
    except Exception as e:
        print(f"⚠️  REST seed error: {e}")


def update_ohlc_from_tick(token, ltp):
    """Called on every tick to keep spot index high/low current."""
    with ohlc_lock:
        rec = ohlc_store.get(token)
        if rec is None:
            return
        if not rec["seeded"]:
            rec["open"] = ltp
            rec["high"] = ltp
            rec["low"]  = ltp
            rec["seeded"] = True
        else:
            if rec["high"] is None or ltp > rec["high"]:
                rec["high"] = ltp
            if rec["low"]  is None or ltp < rec["low"]:
                rec["low"]  = ltp


# ─── Strangle OHLC store ──────────────────────────────────────────────────────
def _blank_strangle():
    return {"open": None, "high": None, "low": None,
            "prev_close": None, "ltp": None, "seeded": False}

strangle_ohlc      = {}
strangle_ohlc_lock = threading.Lock()

for _inst in ("nifty", "sensex"):
    for _wk in ("w1", "w2"):
        for _lv in range(1, OTM_LEVELS + 1):
            strangle_ohlc[f"{_inst}_{_wk}_otm{_lv}"] = _blank_strangle()


def update_strangle_from_tick(key, combined_ltp):
    """
    Sole source of open / high / low for strangles.
    Pre-market ticks (before 09:15) are ignored for OHLC.
    """
    if not _is_market_open():
        with strangle_ohlc_lock:
            rec = strangle_ohlc.get(key)
            if rec is not None:
                rec["ltp"] = combined_ltp
        return

    with strangle_ohlc_lock:
        rec = strangle_ohlc.get(key)
        if rec is None:
            return
        rec["ltp"] = combined_ltp
        if not rec["seeded"]:
            rec["open"]   = combined_ltp
            rec["high"]   = combined_ltp
            rec["low"]    = combined_ltp
            rec["seeded"] = True
        else:
            if rec["high"] is None or combined_ltp > rec["high"]:
                rec["high"] = combined_ltp
            if rec["low"]  is None or combined_ltp < rec["low"]:
                rec["low"]  = combined_ltp


def seed_strangle_prev_close_from_rest(kite_obj, inst, week_label, opts,
                                        atm_strike, strike_interval, exchange_prefix):
    """Seeds only prev_close from REST. Never touches open/high/low."""
    if not opts or atm_strike is None:
        return

    sym_map = {}
    for o in opts:
        s   = o["strike"]
        typ = o.get("instrument_type", "")
        sym_map.setdefault(s, {})
        sym_map[s][typ] = f"{exchange_prefix}:{o['tradingsymbol']}"

    for lv in range(1, OTM_LEVELS + 1):
        key       = f"{inst}_{week_label}_otm{lv}"
        ce_strike = atm_strike + strike_interval * lv
        pe_strike = atm_strike - strike_interval * lv
        ce_sym    = sym_map.get(ce_strike, {}).get("CE")
        pe_sym    = sym_map.get(pe_strike, {}).get("PE")
        if not ce_sym or not pe_sym:
            continue
        try:
            quotes = kite_obj.quote([ce_sym, pe_sym])
            ce_q   = quotes.get(ce_sym, {})
            pe_q   = quotes.get(pe_sym, {})
            if not ce_q or not pe_q:
                continue

            ce_prev = ce_q.get("ohlc", {}).get("close") or 0
            pe_prev = pe_q.get("ohlc", {}).get("close") or 0
            s_prev  = round(ce_prev + pe_prev, 2) if (ce_prev + pe_prev) > 0 else None

            with strangle_ohlc_lock:
                strangle_ohlc[key]["prev_close"] = s_prev

        except Exception as e:
            print(f"⚠️  Strangle prev_close seed error ({key}): {e}")


# ─── Straddle OHLC store ──────────────────────────────────────────────────────
def _blank_straddle():
    return {"open": None, "high": None, "low": None,
            "prev_close": None, "ltp": None, "seeded": False}

straddle_ohlc = {
    "nifty_w1":  _blank_straddle(),
    "nifty_w2":  _blank_straddle(),
    "sensex_w1": _blank_straddle(),
    "sensex_w2": _blank_straddle(),
}
straddle_lock        = threading.Lock()
straddle_atm_symbols = {}


def seed_straddle_prev_close_from_rest(kite_obj, force=False):
    """Seeds only prev_close from REST. Never touches open/high/low."""
    with straddle_lock:
        keys_to_seed = list(straddle_atm_symbols.keys())

    for key in keys_to_seed:
        syms   = straddle_atm_symbols.get(key, {})
        ce_sym = syms.get("ce_symbol")
        pe_sym = syms.get("pe_symbol")
        if not ce_sym or not pe_sym:
            print(f"⚠️  Straddle prev_close seed skipped ({key}) — ATM symbols not set yet")
            continue
        try:
            quotes = kite_obj.quote([ce_sym, pe_sym])
            ce_q   = quotes.get(ce_sym, {})
            pe_q   = quotes.get(pe_sym, {})
            if not ce_q or not pe_q:
                print(f"⚠️  Straddle prev_close seed ({key}) — empty quote")
                continue

            ce_prev = ce_q.get("ohlc", {}).get("close") or 0
            pe_prev = pe_q.get("ohlc", {}).get("close") or 0
            straddle_prev = round(ce_prev + pe_prev, 2) if (ce_prev + pe_prev) > 0 else None

            with straddle_lock:
                rec = straddle_ohlc[key]
                rec["prev_close"] = straddle_prev

            print(
                f"[STRADDLE PREV_CLOSE SEED] {key} ({ce_sym} / {pe_sym}): "
                f"PC={straddle_prev}"
            )
        except Exception as e:
            print(f"⚠️  Straddle prev_close seed error ({key}): {e}")


# ─── Chart history — in-memory state ─────────────────────────────────────────
# Stores two unified series: "nifty_w1" and "sensex_w1".
# Each point: {"time": "HH:MM:SS", "price": float, "atm_strike": int}
# Throttled: at most one point per CHART_THROTTLE_SECONDS per series.

_chart_data        = {}          # in-memory mirror; loaded/reset in init_chart_history()
_chart_lock        = threading.Lock()
_chart_last_ts     = {}          # series_key → monotonic timestamp of last written point


def _today_str_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def init_chart_history() -> None:
    """
    Load today's chart history from disk, or start fresh.
    Call once at the start of main().
    """
    global _chart_data
    today = _today_str_ist()
    try:
        with open(STRADDLE_HISTORY_FILE, "r") as f:
            loaded = json.load(f)
        if loaded.get("date") == today:
            _chart_data = loaded
            counts = {k: len(v) for k, v in loaded.items() if k != "date"}
            print(f"📈 Chart history loaded — {counts}")
        else:
            print(f"📈 Chart history date mismatch ({loaded.get('date')} vs {today}) — resetting")
            _chart_data = {"date": today, "nifty_w1": [], "sensex_w1": []}
            _flush_chart_to_disk()
    except FileNotFoundError:
        print("📈 No chart history file — starting fresh")
        _chart_data = {"date": today, "nifty_w1": [], "sensex_w1": []}
        _flush_chart_to_disk()
    except Exception as e:
        print(f"⚠️  Chart history load error: {e} — starting fresh")
        _chart_data = {"date": today, "nifty_w1": [], "sensex_w1": []}
        _flush_chart_to_disk()


def _flush_chart_to_disk() -> None:
    """Atomic write. Must be called while _chart_lock is held (or at startup)."""
    try:
        tmp = STRADDLE_HISTORY_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_chart_data, f)
        os.replace(tmp, STRADDLE_HISTORY_FILE)
    except Exception as e:
        print(f"⚠️  Chart history write error: {e}")


def append_straddle_history(series_key: str, strike_key: str, price: float) -> None:
    """
    Record one straddle price point into the unified series.

    Parameters
    ──────────
    series_key  : "nifty_w1" or "sensex_w1"
    strike_key  : full key like "nifty_w1_24000.0" — used to extract atm_strike
    price       : combined CE+PE LTP (already rounded to 2 dp)

    Throttled to at most one point per CHART_THROTTLE_SECONDS.
    Writes to disk atomically after every accepted point.
    """
    now_ts = time.monotonic()
    last   = _chart_last_ts.get(series_key, 0.0)
    if now_ts - last < CHART_THROTTLE_SECONDS:
        return  # too soon — skip this tick

    # Parse ATM strike from tail: "nifty_w1_24000.0" → 24000
    try:
        atm_strike = int(float(strike_key.rsplit("_", 1)[-1]))
    except (ValueError, IndexError):
        atm_strike = None

    now_ist  = datetime.now(IST)
    time_str = now_ist.strftime("%H:%M:%S")
    today    = now_ist.strftime("%Y-%m-%d")

    with _chart_lock:
        # Auto-reset on date rollover (defensive — normally shouldn't happen intraday)
        if _chart_data.get("date") != today:
            _chart_data.clear()
            _chart_data.update({"date": today, "nifty_w1": [], "sensex_w1": []})

        series = _chart_data.setdefault(series_key, [])
        series.append({
            "time":       time_str,
            "price":      round(price, 2),
            "atm_strike": atm_strike,
        })

        # Trim to cap
        if len(series) > CHART_MAX_POINTS:
            _chart_data[series_key] = series[-CHART_MAX_POINTS:]

        _flush_chart_to_disk()

    _chart_last_ts[series_key] = now_ts


def update_straddle_from_tick(series_key, ce_ltp, pe_ltp):

    combined = round(ce_ltp + pe_ltp, 2)

    if ce_ltp <= 0 or pe_ltp <= 0:
        return

    if not _is_market_open():
        with straddle_lock:
            rec = straddle_ohlc.get(series_key)
            if rec is not None:
                rec["ltp"] = combined
        return

    with straddle_lock:
        rec = straddle_ohlc.get(series_key)
        if rec is None:
            return

        prev_ltp = rec.get("ltp")

        if prev_ltp is not None and abs(combined - prev_ltp) > 150:
            return

        rec["ltp"] = combined

        if not rec["seeded"]:
            rec["open"]   = combined
            rec["high"]   = combined
            rec["low"]    = combined
            rec["seeded"] = True
        else:
            if rec["high"] is None or combined > rec["high"]:
                rec["high"] = combined
            if rec["low"]  is None or combined < rec["low"]:
                rec["low"]  = combined

    


# ─── Previous-day close ───────────────────────────────────────────────────────
prev_close      = {"date": "", "nifty": None, "sensex": None}
prev_close_lock = threading.Lock()

def load_prev_close():
    global prev_close
    try:
        with open(PREV_CLOSE_FILE, "r") as f:
            data = json.load(f)
        with prev_close_lock:
            prev_close.update(data)
        print(
            f"📂 Prev close loaded  "
            f"NIFTY={prev_close['nifty']}  "
            f"SENSEX={prev_close['sensex']}  "
            f"date={prev_close['date']}"
        )
    except FileNotFoundError:
        print("📂 No prev_close.json — % change will be N/A until 15:30 today")
    except Exception as e:
        print(f"⚠️  prev_close load error: {e}")

def save_prev_close(nifty_ltp, sensex_ltp):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    data  = {"date": today, "nifty": nifty_ltp, "sensex": sensex_ltp}
    try:
        tmp = PREV_CLOSE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, PREV_CLOSE_FILE)
        with prev_close_lock:
            prev_close.update(data)
        print(f"\n💾 Close saved  NIFTY={nifty_ltp}  SENSEX={sensex_ltp}")
    except Exception as e:
        print(f"\n⚠️  prev_close save error: {e}")

# ─── Globals ──────────────────────────────────────────────────────────────────
fut_token_nifty        = None
fut_token_sensex       = None
token_to_option_nifty  = {}
token_to_option_sensex = {}
expiry_labels_nifty    = {"week1": "", "week2": ""}
expiry_labels_sensex   = {"week1": "", "week2": ""}

data_lock  = threading.Lock()
last_known = {}

_close_saved_today   = False
_close_saved_lock    = threading.Lock()

_premarket_seeded    = False
_premarket_seed_lock = threading.Lock()

_kite_obj = None

# ─── Expiry helper ────────────────────────────────────────────────────────────
def parse_expiry_str(instruments):
    try:
        exp = instruments[0].get("expiry", "")
        if not exp:
            return ""
        dt = datetime.strptime(str(exp)[:10], "%Y-%m-%d") if isinstance(exp, str) else exp
        return dt.strftime("%d %b %Y")
    except Exception:
        return ""

# ─── OTM strangle builder ─────────────────────────────────────────────────────
def build_otm_levels(atm_strike, strikes_map, strike_interval,
                     inst_prefix, week_label):
    otm_levels = {}
    for lv in range(1, OTM_LEVELS + 1):
        ce_deltas = (lv - 1, lv, lv + 1)
        pe_deltas = (lv + 1, lv, lv - 1)
        rows = []
        for ce_d, pe_d in zip(ce_deltas, pe_deltas):
            ce_s   = atm_strike + strike_interval * ce_d
            pe_s   = atm_strike - strike_interval * pe_d
            ce_ltp = strikes_map.get(ce_s, {}).get("CE", {}).get("last_price", None)
            pe_ltp = strikes_map.get(pe_s, {}).get("PE", {}).get("last_price", None)
            total  = round(ce_ltp + pe_ltp, 2) \
                     if isinstance(ce_ltp, (int, float)) and isinstance(pe_ltp, (int, float)) \
                     else None
            is_mid = (ce_d == lv)
            rows.append({
                "ce_strike": ce_s, "ce_ltp": ce_ltp,
                "pe_strike": pe_s, "pe_ltp": pe_ltp,
                "sum":       total,
                "is_mid":    is_mid,
            })
            if is_mid and total is not None:
                key = f"{inst_prefix}_{week_label}_otm{lv}"
                update_strangle_from_tick(key, total)

        otm_levels[str(lv)] = rows
    return otm_levels

# ─── Per-instrument state builder ─────────────────────────────────────────────
def build_state(spot_token, fut_token, token_to_option,
                strike_interval, expiry_labels, straddle_key):
    with data_lock:
        snapshot = last_known.copy()

    spot_data  = snapshot.get(spot_token, {})
    spot_close = spot_data.get("last_price") if isinstance(spot_data, dict) else None

    fut_data   = snapshot.get(fut_token, {})
    fut_close  = fut_data.get("last_price") if isinstance(fut_data, dict) else None

    ref_price  = spot_close if spot_close is not None else fut_close

    strikes_week1, strikes_week2 = {}, {}
    for token, tick_data in snapshot.items():
        if token not in token_to_option:
            continue
        strike, typ, week = token_to_option[token]
        target = strikes_week1 if week == "week1" else strikes_week2
        target.setdefault(strike, {"CE": {}, "PE": {}})
        if isinstance(tick_data, dict):
            ltp  = tick_data.get("last_price", 0)
            ohlc = tick_data.get("ohlc")
        else:
            ltp  = tick_data
            ohlc = None
        entry = {"last_price": ltp}
        if ohlc:
            entry["ohlc"] = ohlc
        target[strike][typ] = entry

    atm_strike  = None
    all_strikes = set(strikes_week1.keys()) | set(strikes_week2.keys())
    if ref_price is not None and all_strikes:
        atm_strike = min(all_strikes, key=lambda s: abs(s - ref_price))

    # ── Synthetic Futures ─────────────────────────────────────────────────────
    synthetic_future = None
    try:
        if atm_strike is not None:
            ce_ltp = strikes_week1.get(atm_strike, {}).get("CE", {}).get("last_price")
            pe_ltp = strikes_week1.get(atm_strike, {}).get("PE", {}).get("last_price")
            if isinstance(ce_ltp, (int, float)) and isinstance(pe_ltp, (int, float)) \
                    and ce_ltp > 0 and pe_ltp > 0:
                synthetic_future = round(atm_strike + ce_ltp - pe_ltp, 2)
        if synthetic_future is None:
            for strike in sorted(strikes_week1.keys(),
                                 key=lambda s: abs(s - (ref_price or 0))):
                ce_ltp = strikes_week1[strike].get("CE", {}).get("last_price")
                pe_ltp = strikes_week1[strike].get("PE", {}).get("last_price")
                if isinstance(ce_ltp, (int, float)) and isinstance(pe_ltp, (int, float)) \
                        and ce_ltp > 0 and pe_ltp > 0:
                    synthetic_future = round(strike + ce_ltp - pe_ltp, 2)
                    break
    except Exception:
        pass

    # ── Time to expiry for IV (computed once per build_state call) ────────────
    T_w1 = _time_to_expiry(expiry_labels.get("week1", ""))
    T_w2 = _time_to_expiry(expiry_labels.get("week2", ""))

    # ── Update straddle running OHLC from live ticks ──────────────────────────
    for wk_label, wk_map in (("w1", strikes_week1), ("w2", strikes_week2)):
        series_key = f"{straddle_key}_{wk_label}"
        strike_key = f"{series_key}_{atm_strike}"

        if atm_strike is not None:
            ce_ltp = wk_map.get(atm_strike, {}).get("CE", {}).get("last_price")
            pe_ltp = wk_map.get(atm_strike, {}).get("PE", {}).get("last_price")
            if isinstance(ce_ltp, (int, float)) and isinstance(pe_ltp, (int, float)) \
                    and ce_ltp > 0 and pe_ltp > 0:
                update_straddle_from_tick(series_key, ce_ltp, pe_ltp)
                append_straddle_history(series_key, strike_key, ce_ltp + pe_ltp)

    def build_for_week(strikes_map, week_label, T_expiry):
        """
        Build options rows for one expiry week.
        Each row now includes ce_iv and pe_iv (annualised %, or None).
        IV is calculated using Black-Scholes bisection with spot as underlying.
        """
        if atm_strike is None:
            return [], {}
        options_rows = []
        for i in range(-STRIKES_RANGE, STRIKES_RANGE + 1):
            strike  = atm_strike + i * strike_interval
            ce_data = strikes_map.get(strike, {}).get("CE", {})
            pe_data = strikes_map.get(strike, {}).get("PE", {})
            ce      = ce_data.get("last_price", None)
            pe      = pe_data.get("last_price", None)
            tot     = round(ce + pe, 2) \
                      if isinstance(ce, (int, float)) and isinstance(pe, (int, float)) \
                      else None

            # ── IV calculation ─────────────────────────────────────────────────
            # Use spot price (ref_price) as underlying for index options (European)
            ce_iv = calc_iv(
                option_price=ce,
                S=ref_price,
                K=strike,
                T=T_expiry,
                r=RISK_FREE_RATE,
                option_type="CE",
            ) if isinstance(ce, (int, float)) and ce > 0 and ref_price else None

            pe_iv = calc_iv(
                option_price=pe,
                S=ref_price,
                K=strike,
                T=T_expiry,
                r=RISK_FREE_RATE,
                option_type="PE",
            ) if isinstance(pe, (int, float)) and pe > 0 and ref_price else None


            # ── IV → sigma ────────────────────────────────────────────────
            DEFAULT_IV = 20  # fallback IV %

            ce_sigma = (ce_iv / 100) if ce_iv is not None else (DEFAULT_IV / 100)
            pe_sigma = (pe_iv / 100) if pe_iv is not None else (DEFAULT_IV / 100)


            # ── Greeks calculation ────────────────────────────────────────
            ce_greeks = calc_greeks(
                S=ref_price,
                K=strike,
                T=T_expiry,
                r=RISK_FREE_RATE,
                sigma=ce_sigma,
                option_type="CE"
            ) if ce_sigma is not None else None

            pe_greeks = calc_greeks(
                S=ref_price,
                K=strike,
                T=T_expiry,
                r=RISK_FREE_RATE,
                sigma=pe_sigma,
                option_type="PE"
            ) if pe_sigma is not None else None

            if ce_iv is None:
                print(f"IV failed at strike {strike}, CE={ce}, S={ref_price}")

            # ── NET Greeks ────────────────────────────────────────────────
            net_greeks = None
            if ce_greeks is not None and pe_greeks is not None:
                net_greeks = {
                    "delta": ce_greeks["delta"] + pe_greeks["delta"],
                    "theta": ce_greeks["theta"] + pe_greeks["theta"],
                    "gamma": ce_greeks["gamma"] + pe_greeks["gamma"],
                    "vega":  ce_greeks["vega"]  + pe_greeks["vega"],
                    "rho":   ce_greeks["rho"]   + pe_greeks["rho"],
                }

            options_rows.append({
                "strike": strike,
                "ce": ce,
                "pe": pe,
                "sum": tot,
                "is_atm": (strike == atm_strike),

                "ce_iv": ce_iv,
                "pe_iv": pe_iv,

                "ce_greeks":  ce_greeks,
                "pe_greeks":  pe_greeks,
                "net_greeks": net_greeks,
            })

        otm_levels = build_otm_levels(
            atm_strike, strikes_map, strike_interval,
            straddle_key, week_label
        )
        return options_rows, otm_levels

    opt_w1, otm_w1 = build_for_week(strikes_week1, "w1", T_w1)
    opt_w2, otm_w2 = build_for_week(strikes_week2, "w2", T_w2)

    def snap_straddle(wk):
        with straddle_lock:
            r = dict(straddle_ohlc.get(f"{straddle_key}_{wk}", {}))
        return {
            "open":       r.get("open"),
            "high":       r.get("high"),
            "low":        r.get("low"),
            "prev_close": r.get("prev_close"),
            "ltp":        r.get("ltp"),
        }

    def snap_strangle_all(wk):
        result = {}
        with strangle_ohlc_lock:
            for lv in range(1, OTM_LEVELS + 1):
                key = f"{straddle_key}_{wk}_otm{lv}"
                r   = dict(strangle_ohlc.get(key, {}))
                result[str(lv)] = {
                    "open":       r.get("open"),
                    "high":       r.get("high"),
                    "low":        r.get("low"),
                    "prev_close": r.get("prev_close"),
                    "ltp":        r.get("ltp"),
                }
        return result

    return {
        "spot_close":           spot_close,
        "fut_close":            fut_close,
        "atm_strike":           atm_strike,
        "synthetic_future":     synthetic_future,
        "expiry_week1":         expiry_labels.get("week1", ""),
        "expiry_week2":         expiry_labels.get("week2", ""),
        "options_rows_w1":      opt_w1,
        "options_rows_w2":      opt_w2,
        "otm_levels_w1":        otm_w1,
        "otm_levels_w2":        otm_w2,
        "straddle_ohlc_w1":     snap_straddle("w1"),
        "straddle_ohlc_w2":     snap_straddle("w2"),
        "strangle_ohlc_w1":     snap_strangle_all("w1"),
        "strangle_ohlc_w2":     snap_strangle_all("w2"),
        "updated_at":           datetime.now(IST).strftime("%H:%M:%S"),
        "date_str":             datetime.now(IST).strftime("%d %b %Y"),
    }

# ─── Refresh loop ─────────────────────────────────────────────────────────────
_first_write = True

def refresh_loop():
    global _close_saved_today, _first_write, _premarket_seeded

    print("⏰ Refresh loop started")

    while True:
        try:
            now_ist = datetime.now(IST)

            # ── Pre-market seed at 9:08–9:09 ──────────────────────────────────
            in_premarket_window = (
                now_ist.hour == PREMARKET_SEED_HOUR
                and PREMARKET_SEED_MIN_START <= now_ist.minute <= PREMARKET_SEED_MIN_END
            )
            with _premarket_seed_lock:
                if in_premarket_window and not _premarket_seeded and _kite_obj:
                    print("\n🕘 Pre-market window — seeding prev_close from REST …")
                    seed_ohlc_from_rest(_kite_obj, force=False)
                    seed_straddle_prev_close_from_rest(_kite_obj)
                    _premarket_seeded = True
                if now_ist.hour == 0 and now_ist.minute == 0:
                    _premarket_seeded = False

            # ── Save 15:30 close ───────────────────────────────────────────────
            with _close_saved_lock:
                is_close_time = (
                    now_ist.hour   == MARKET_CLOSE_HOUR
                    and now_ist.minute == MARKET_CLOSE_MINUTE
                )
                if is_close_time and not _close_saved_today:
                    _close_saved_today = True
                    with data_lock:
                        snap = last_known.copy()
                    n_ltp = snap.get(NIFTY_SPOT_TOKEN,  {}).get("last_price")
                    s_ltp = snap.get(SENSEX_SPOT_TOKEN, {}).get("last_price")
                    if n_ltp and s_ltp:
                        save_prev_close(n_ltp, s_ltp)
                elif not is_close_time:
                    _close_saved_today = False

            # ── Spot prices ───────────────────────────────────────────────────
            with data_lock:
                snap = last_known.copy()

            n_spot  = snap.get(NIFTY_SPOT_TOKEN,     {}).get("last_price")
            s_spot  = snap.get(SENSEX_SPOT_TOKEN,    {}).get("last_price")
            bn_spot = snap.get(BANKNIFTY_SPOT_TOKEN, {}).get("last_price")

            with vix_ltp_lock:
                current_vix_ltp = vix_ltp

            with prev_close_lock:
                n_prev = prev_close.get("nifty")
                s_prev = prev_close.get("sensex")

            def pct(ltp, prev):
                if ltp is None or prev is None or prev == 0:
                    return None
                return round((ltp - prev) / prev * 100, 2)

            with ohlc_lock:
                n_ohlc  = dict(ohlc_store[NIFTY_SPOT_TOKEN])
                s_ohlc  = dict(ohlc_store[SENSEX_SPOT_TOKEN])
                bn_ohlc = dict(ohlc_store[BANKNIFTY_SPOT_TOKEN])

            ticker_bar = {
                "nifty": {
                    "spot":           n_spot,
                    "prev_close":     n_ohlc["prev_close"],
                    "pct_change":     pct(n_spot, n_ohlc["prev_close"]),
                    "open":           n_ohlc["open"],
                    "high":           n_ohlc["high"],
                    "low":            n_ohlc["low"],
                    "day_prev_close": n_ohlc["prev_close"],
                },
                "sensex": {
                    "spot":           s_spot,
                    "prev_close":     s_ohlc["prev_close"],
                    "pct_change":     pct(s_spot, s_ohlc["prev_close"]),
                    "open":           s_ohlc["open"],
                    "high":           s_ohlc["high"],
                    "low":            s_ohlc["low"],
                    "day_prev_close": s_ohlc["prev_close"],
                },
                "banknifty": {
                    "spot":           bn_spot,
                    "prev_close":     bn_ohlc["prev_close"],
                    "pct_change":     pct(bn_spot, bn_ohlc.get("prev_close")),
                    "open":           bn_ohlc["open"],
                    "high":           bn_ohlc["high"],
                    "low":            bn_ohlc["low"],
                    "day_prev_close": bn_ohlc["prev_close"],
                },
                "indiavix": {
                    "spot":           current_vix_ltp,
                    "prev_close":     None,
                    "pct_change":     None,
                    "open":           None,
                    "high":           None,
                    "low":            None,
                    "day_prev_close": None,
                },
            }

            nifty_state  = build_state(
                NIFTY_SPOT_TOKEN, fut_token_nifty,
                token_to_option_nifty, STRIKE_INTERVAL_NIFTY, expiry_labels_nifty,
                "nifty"
            )
            sensex_state = build_state(
                SENSEX_SPOT_TOKEN, fut_token_sensex,
                token_to_option_sensex, STRIKE_INTERVAL_SENSEX, expiry_labels_sensex,
                "sensex"
            )

            if _first_write:
                _first_write = False
                print(f"\n{'─'*55}")
                print(f"  FIRST DATA SNAPSHOT")
                print(f"{'─'*55}")
                print(f"  Tokens in last_known : {len(snap)}")
                print(f"  NIFTY  spot          : {n_spot}")
                print(f"  SENSEX spot          : {s_spot}")
                print(f"  BANKNIFTY spot       : {bn_spot}")
                print(f"  INDIAVIX ltp         : {current_vix_ltp}")
                print(f"  NIFTY  ATM           : {nifty_state.get('atm_strike')}")
                print(f"  SENSEX ATM           : {sensex_state.get('atm_strike')}")
                print(f"{'─'*55}\n")

            payload = {
                "ticker_bar": ticker_bar,
                "nifty":      nifty_state,
                "sensex":     sensex_state,
                "config":     {"strikes_range": STRIKES_RANGE, "otm_levels": OTM_LEVELS},
            }

            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, DATA_FILE)

            print(
                f"✅ {now_ist.strftime('%H:%M:%S')} "
                f"| N={n_spot or 'NO TICK'} "
                f"| S={s_spot or 'NO TICK'} "
                f"| BN={bn_spot or 'NO TICK'} "
                f"| VIX={current_vix_ltp or 'NO TICK'}",
                end="\r",
            )

        except Exception as e:
            print(f"\n⚠️  Refresh error: {e}")

        time.sleep(REFRESH_INTERVAL)

# ─── WebSocket callbacks ──────────────────────────────────────────────────────
def on_ticks(ws_obj, ticks):
    global vix_ltp

    with data_lock:
        for t in ticks:
            token = t.get("instrument_token")
            ltp   = t.get("last_price")
            if ltp is None:
                continue
            entry = {"last_price": ltp}
            ohlc = t.get("ohlc")
            if ohlc:
                entry["ohlc"] = ohlc
            last_known[token] = entry

    for t in ticks:
        token = t.get("instrument_token")
        ltp   = t.get("last_price")
        if ltp is None:
            continue
        if token in ohlc_store:
            update_ohlc_from_tick(token, ltp)
        if token == INDIAVIX_SPOT_TOKEN:
            with vix_ltp_lock:
                vix_ltp = ltp


def on_connect(ws_obj, response):
    print("🔗 WebSocket connected")

    spot_tokens    = [NIFTY_SPOT_TOKEN, SENSEX_SPOT_TOKEN,
                      BANKNIFTY_SPOT_TOKEN, INDIAVIX_SPOT_TOKEN]
    futures_tokens = [fut_token_nifty, fut_token_sensex]
    option_tokens  = (list(token_to_option_nifty.keys())
                      + list(token_to_option_sensex.keys()))
    all_tokens     = list(set(spot_tokens + futures_tokens + option_tokens))

    print(
        f"   Subscribing {len(all_tokens)} tokens total "
        f"({len(option_tokens)} options + 2 futures + 4 spot/index)"
    )

    ws_obj.subscribe(all_tokens)
    ws_obj.set_mode(ws_obj.MODE_LTP, spot_tokens)
    ws_obj.set_mode(ws_obj.MODE_FULL, futures_tokens + option_tokens)

    threading.Thread(target=refresh_loop, daemon=True).start()


def on_reconnect(ws_obj, attempts_count):
    print(f"\n🔄 Reconnect attempt #{attempts_count} — reseeding prev_close from REST …")
    if _kite_obj:
        seed_ohlc_from_rest(_kite_obj, force=True)
        seed_straddle_prev_close_from_rest(_kite_obj)


def on_close(ws_obj, code, reason):
    print(f"\n🔌 WebSocket closed: {code} {reason}")

def on_error(ws_obj, code, reason):
    print(f"\n❌ WebSocket error: {code} {reason}")

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    global fut_token_nifty,  token_to_option_nifty,  expiry_labels_nifty
    global fut_token_sensex, token_to_option_sensex, expiry_labels_sensex
    global _kite_obj

    load_prev_close()

    # ── Load/reset chart history from disk ────────────────────────────────────
    init_chart_history()

    token_data   = load_token()
    access_token = token_data["access_token"]

    _kite_obj = KiteConnect(api_key=API_KEY)
    _kite_obj.set_access_token(access_token)

    print("🔍 Seeding spot index OHLC from REST …")
    seed_ohlc_from_rest(_kite_obj, force=True)

    inst = load_instruments()

    # NIFTY
    nifty_fut = inst["nifty"]["futures"]
    nifty_w1  = inst["nifty"]["options_week1"]
    nifty_w2  = inst["nifty"].get("options_week2", [])

    fut_token_nifty = nifty_fut[0]["instrument_token"]
    token_to_option_nifty = {}
    for o in nifty_w1:
        token_to_option_nifty[o["instrument_token"]] = (
            o["strike"], o["instrument_type"], "week1"
        )
    for o in nifty_w2:
        token_to_option_nifty[o["instrument_token"]] = (
            o["strike"], o["instrument_type"], "week2"
        )
    expiry_labels_nifty = {
        "week1": parse_expiry_str(nifty_w1),
        "week2": parse_expiry_str(nifty_w2) if nifty_w2 else "N/A",
    }

    # SENSEX
    sensex_fut = inst["sensex"]["futures"]
    sensex_w1  = inst["sensex"]["options_week1"]
    sensex_w2  = inst["sensex"].get("options_week2", [])

    fut_token_sensex = sensex_fut[0]["instrument_token"]
    token_to_option_sensex = {}
    for o in sensex_w1:
        token_to_option_sensex[o["instrument_token"]] = (
            o["strike"], o["instrument_type"], "week1"
        )
    for o in sensex_w2:
        token_to_option_sensex[o["instrument_token"]] = (
            o["strike"], o["instrument_type"], "week2"
        )
    expiry_labels_sensex = {
        "week1": parse_expiry_str(sensex_w1),
        "week2": parse_expiry_str(sensex_w2) if sensex_w2 else "N/A",
    }

    print(f"📅 NIFTY   week1={expiry_labels_nifty['week1']}  week2={expiry_labels_nifty['week2']}")
    print(f"📅 SENSEX  week1={expiry_labels_sensex['week1']}  week2={expiry_labels_sensex['week2']}")
    print(
        f"📊 Options loaded: "
        f"NIFTY w1={len(nifty_w1)} w2={len(nifty_w2)} | "
        f"SENSEX w1={len(sensex_w1)} w2={len(sensex_w2)}"
    )

    # ── Populate straddle ATM symbols for prev_close seed ─────────────────────
    def find_atm_symbols(opts, index_token):
        with ohlc_lock:
            spot = ohlc_store.get(index_token, {}).get("open")
        if spot is None:
            strikes = sorted(set(o["strike"] for o in opts))
            spot = strikes[len(strikes) // 2] if strikes else None
        if spot is None:
            return {}
        strike_map = {}
        for o in opts:
            s   = o["strike"]
            typ = o.get("instrument_type", "")
            strike_map.setdefault(s, {})
            strike_map[s][typ] = o
        atm     = min(strike_map.keys(), key=lambda s: abs(s - spot))
        ce_inst = strike_map[atm].get("CE")
        pe_inst = strike_map[atm].get("PE")
        ce_sym  = f"NFO:{ce_inst['tradingsymbol']}" if ce_inst else None
        pe_sym  = f"NFO:{pe_inst['tradingsymbol']}" if pe_inst else None
        print(f"  ATM strike for straddle prev_close seed: {atm}  CE={ce_sym}  PE={pe_sym}")
        return {"ce_symbol": ce_sym, "pe_symbol": pe_sym}

    def find_atm_symbols_bse(opts, index_token):
        syms = find_atm_symbols(opts, index_token)
        if syms.get("ce_symbol"):
            syms["ce_symbol"] = syms["ce_symbol"].replace("NFO:", "BFO:")
        if syms.get("pe_symbol"):
            syms["pe_symbol"] = syms["pe_symbol"].replace("NFO:", "BFO:")
        return syms

    straddle_atm_symbols["nifty_w1"]  = find_atm_symbols(nifty_w1,  NIFTY_SPOT_TOKEN)
    straddle_atm_symbols["nifty_w2"]  = find_atm_symbols(nifty_w2,  NIFTY_SPOT_TOKEN)  if nifty_w2  else {}
    straddle_atm_symbols["sensex_w1"] = find_atm_symbols_bse(sensex_w1, SENSEX_SPOT_TOKEN)
    straddle_atm_symbols["sensex_w2"] = find_atm_symbols_bse(sensex_w2, SENSEX_SPOT_TOKEN) if sensex_w2 else {}

    print("🔍 Seeding straddle prev_close from REST …")
    seed_straddle_prev_close_from_rest(_kite_obj)

    # ── Seed strangle prev_close for all OTM levels from REST ─────────────────
    def get_atm(opts, index_token):
        with ohlc_lock:
            spot = ohlc_store.get(index_token, {}).get("open")
        if spot is None:
            return None
        strikes = sorted(set(o["strike"] for o in opts))
        if not strikes:
            return None
        return min(strikes, key=lambda s: abs(s - spot))

    nifty_atm_w1  = get_atm(nifty_w1,  NIFTY_SPOT_TOKEN)
    nifty_atm_w2  = get_atm(nifty_w2,  NIFTY_SPOT_TOKEN)  if nifty_w2  else None
    sensex_atm_w1 = get_atm(sensex_w1, SENSEX_SPOT_TOKEN)
    sensex_atm_w2 = get_atm(sensex_w2, SENSEX_SPOT_TOKEN) if sensex_w2 else None

    print("🔍 Seeding strangle prev_close for all OTM levels from REST …")
    if nifty_atm_w1:
        seed_strangle_prev_close_from_rest(_kite_obj, "nifty", "w1", nifty_w1,
                                            nifty_atm_w1,  STRIKE_INTERVAL_NIFTY,  "NFO")
    if nifty_atm_w2:
        seed_strangle_prev_close_from_rest(_kite_obj, "nifty", "w2", nifty_w2,
                                            nifty_atm_w2,  STRIKE_INTERVAL_NIFTY,  "NFO")
    if sensex_atm_w1:
        seed_strangle_prev_close_from_rest(_kite_obj, "sensex", "w1", sensex_w1,
                                            sensex_atm_w1, STRIKE_INTERVAL_SENSEX, "BFO")
    if sensex_atm_w2:
        seed_strangle_prev_close_from_rest(_kite_obj, "sensex", "w2", sensex_w2,
                                            sensex_atm_w2, STRIKE_INTERVAL_SENSEX, "BFO")

    kws = KiteTicker(API_KEY, access_token)
    kws.on_ticks     = on_ticks
    kws.on_connect   = on_connect
    kws.on_close     = on_close
    kws.on_error     = on_error
    kws.on_reconnect = on_reconnect

    print("🚀 Connecting to WebSocket …")
    kws.connect(threaded=False)


if __name__ == "__main__":
    main()