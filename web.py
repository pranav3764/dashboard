import json
import time
import pandas as pd
import streamlit as st
 
DATA_FILE             = r"C:\nifty\live_data.json"
STRADDLE_HISTORY_FILE = r"C:\nifty\straddle_history.json"
OTM_LEVELS = 7
 
 
if "selected_data" not in st.session_state:
    st.session_state.selected_data = None
if "greeks_panel" not in st.session_state:
    # dict: strike, instrument, week, ce, pe, ce_iv, pe_iv,
    #       ce_greeks, pe_greeks, net_greeks  — or None
    st.session_state.greeks_panel = None

if "show_greeks_dialog" not in st.session_state:
    st.session_state.show_greeks_dialog = False
# Track which strike+instrument+week was last dismissed via X
if "dismissed_key" not in st.session_state:
    st.session_state.dismissed_key = None
# Was the dialog open on the previous rerun?
if "dialog_was_open" not in st.session_state:
    st.session_state.dialog_was_open = False
if "pause_updates" not in st.session_state:
    st.session_state.pause_updates = False

if "pause_until" not in st.session_state:
    st.session_state.pause_until = 0



# ── Detect X click ───────────────────────────────────────────────────────────
# While paused, st.stop() prevents any automatic reruns.
# The ONLY way a rerun happens during pause is when the user clicks X.
# So: if we were paused AND dialog_was_open last rerun → X was clicked.
if st.session_state.pause_updates and st.session_state.dialog_was_open:
    panel = st.session_state.greeks_panel
    if panel:
        st.session_state.dismissed_key = (
            panel.get("instrument"),
            panel.get("strike"),
            panel.get("week"),
        )
    st.session_state.greeks_panel = None
    st.session_state.show_greeks_dialog = False
    # ── Resume live updates immediately ──────────────────────────────────────
    st.session_state.pause_updates = False
    st.session_state.pause_until = 0
st.session_state.dialog_was_open = False  # reset; show_greeks_dialog() sets it True

st.set_page_config(
    page_title="NIFTY / SENSEX Live Options",
    layout="wide",
    page_icon="📈",
)
 
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Bebas+Neue&display=swap');
 
html, body, [class*="css"], .stApp {
    background-color: #090d18 !important;
    color: #dde4f0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px;
}
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 0.5rem; padding-bottom: 0.5rem; }
 
/* ── Remove whitespace inside dataframe containers ── */
[data-testid="stDataFrame"] > div,
[data-testid="stDataFrame"] iframe { overflow: hidden !important; }
[data-testid="stDataFrame"] { margin-bottom: 0 !important; padding-bottom: 0 !important; }
[data-testid="stDataFrame"] > div > div { overflow: hidden !important; }
 
/* Hide dataframe toolbar */
button[title="Download"] { display:none !important; }
 
/* ── Responsive ── */
@media (max-width: 1400px) {
    html, body, [class*="css"], .stApp { font-size: 14px !important; }
    .ohlc-row    { font-size: 0.62rem !important; gap: 8px !important; }
    .straddle-ltp-val { font-size: 0.85rem !important; }
    .s-key { font-size: 0.62rem !important; }
    .s-val { font-size: 0.72rem !important; }
    .sec-hdr, .sec-hdr-w2 { font-size: 1.1rem !important; }
    .otm-hdr, .otm-hdr-w2 { font-size: 0.75rem !important; }
    .info-row    { font-size: 0.7rem !important; }
    .info-lbl    { font-size: 0.65rem !important; }
}
 
@media (max-width: 1100px) {
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1),
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) {
        min-width: 48% !important; flex: 1 1 48% !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3) {
        min-width: 100% !important; flex: 1 1 100% !important; margin-top: 18px !important;
    }
}
@media (max-width: 680px) {
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        min-width: 100% !important; flex: 1 1 100% !important; margin-top: 14px !important;
    }
    .ticker-bar { flex-wrap: wrap; }
    .ticker-item { min-width: 48%; border-right: none !important; padding: 6px 10px; }
    .ticker-item:nth-child(odd) { border-right: 1px solid #1a2f55 !important; }
    .ticker-item:nth-child(1), .ticker-item:nth-child(2) { border-bottom: 1px solid #1a2f55; }
}
 
/* ── Ticker bar ── */
.ticker-bar {
    background: linear-gradient(90deg,#0d1830,#0a1528 60%,#0d1830);
    border:1px solid #1a2f55; border-radius:8px;
    padding:12px 20px; display:flex; align-items:stretch;
    gap:0; margin-bottom:14px;
}
.ticker-item { display:flex; flex-direction:column; gap:2px; flex:1 1 0; padding:0 18px; }
.ticker-item:not(:last-child) { border-right:1px solid #1a2f55; }
.ticker-name { font-size:0.7rem; letter-spacing:0.18em; text-transform:uppercase; color:#5a7aaa; margin-bottom:3px}
.ticker-ltp  { font-family:'Bebas Neue',sans-serif; font-size:1.5rem; letter-spacing:0.06em; line-height:1; color:#e8f0ff; }
.ticker-meta { font-size:0.7rem; display:flex; align-items:center; gap:8px; }
.pct-up   { color:#22dd88; font-weight:700; }
.pct-down { color:#ee5555; font-weight:700; }
.pct-na   { color:#5a7aaa; }
.ohlc-row { display:flex; gap:15px; margin-top:3px; font-size:0.70rem; flex-wrap:wrap;}
.ohlc-lbl { color:#AAAAAA; margin-right:3px }
.ohlc-val-high { color:#22dd88; font-weight:600; }
.ohlc-val-low  { color:#ee5555; font-weight:600; }
.ohlc-val-open { color:#f0c040; font-weight:600; }
.ohlc-val-pc   { color:#7a8aaa; font-weight:600; }
 
/* ── Section headers ── */
.sec-hdr {
    font-family:'Bebas Neue',sans-serif; font-size:1.5rem; letter-spacing:0.08em;
    padding:4px 10px; border-left:4px solid #f0c040;
    background:#0e1828; color:#e8d080; margin-bottom:3px;
}
.sec-hdr-w2 {
    font-family:'Bebas Neue',sans-serif; font-size:1.5rem; letter-spacing:0.08em;
    padding:4px 10px; border-left:4px solid #f0c040;
    background:#0e1828; color:#e8d080; margin-bottom:3px;
}
 
/* ── Straddle OHLC bar ── */
.straddle-bar {
    display:flex; align-items:center; gap:0;
    background:#07111f; border:1px solid #152840;
    border-radius:5px; padding:4px 10px; margin-bottom:4px; flex-wrap:wrap;
}
.straddle-lbl { font-size:0.65rem; color:#AAAAAA; letter-spacing:0.14em; text-transform:uppercase; margin-right:8px; white-space:nowrap; }
.straddle-ltp-val { font-family:'Bebas Neue',sans-serif; font-size:1rem; color:#AAAAAA; letter-spacing:0.05em; margin-right:7px; }
.straddle-divider { width:1px; height:16px; background:#152840; margin:0 8px; flex-shrink:0; }
.s-item { display:flex; align-items:center; gap:3px; }
.s-key  { font-size:0.7rem; color:#3a5878; letter-spacing:0.10em; }
.s-val  { font-size:0.8rem; font-weight:700; }
.s-open { color:#f0c040; }
.s-high { color:#22dd88; }
.s-low  { color:#ee5555; }
.s-pc   { color:#7a8aaa; }
 
/* ── Info row ── */
.info-row { display:flex; align-items:center; flex-wrap:wrap; gap:5px; padding:3px 2px; margin-bottom:4px; font-size:0.8rem; }
.info-item { display:flex; align-items:baseline; gap:3px; }
.info-lbl  { color:#AAAAAA; font-size:0.75rem; letter-spacing:0.10em; text-transform:uppercase; margin-right:3px}
.info-val  { color:#b8ddf8; font-weight:700; }
.fut-val   { color:#a8c8f0; }
.syn-val   { color:#f0d060; font-weight:700; }
.info-sep  { color:#1a3050; font-size:0.78rem; }
.info-time { margin-left:auto; color:#AAAAAA; font-size:0.8rem; }
 
/* ── OTM strangle headers ── */
.otm-hdr {
    background:#0c1c38; color:#6aa8d8; font-size:0.9rem; font-weight:700;
    padding:5px 10px; border-left:3px solid #2060a0; margin-bottom:3px; letter-spacing:0.04em;
}
.otm-hdr-w2 {
    background:#081828; color:#4a88a8; font-size:0.80rem; font-weight:700;
    padding:5px 10px; border-left:3px solid #184860; margin-bottom:3px; letter-spacing:0.04em;
}
 
/* ── Selects ── */
div[data-baseweb="select"] > div {
    background:#0c1c38 !important; border-color:#1a3660 !important;
    color:#6aa8d8 !important; font-family:'JetBrains Mono',monospace !important; font-size:0.82rem !important;
}
div[data-baseweb="select"] svg { fill:#6aa8d8 !important; }
 
/* ── Greeks Panel ── */
.greeks-panel {
    background: linear-gradient(135deg, #0a1828 0%, #0d1e35 100%);
    border: 1px solid #1e3a5a;
    border-radius: 8px;
    padding: 10px 14px;
    font-family: 'JetBrains Mono', monospace;
}
.greeks-title {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 1.1rem;
    letter-spacing: 0.10em;
    color: #f0c040;
    margin-bottom: 6px;
    border-bottom: 1px solid #1e3a5a;
    padding-bottom: 4px;
}
.greeks-subtitle {
    font-size: 0.62rem;
    color: #5a7aaa;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    margin-bottom: 3px;
    margin-top: 4px;
}
.greeks-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 3px 6px;
    margin-bottom: 2px;
}
.greek-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    background: #070f1e;
    border-radius: 4px;
    padding: 3px 4px;
    border: 1px solid #122040;
}
.greek-lbl { font-size: 0.55rem; color: #3a5878; letter-spacing: 0.10em; text-transform: uppercase; }
.greek-val { font-size: 0.74rem; font-weight: 700; }
.greek-ce  { color: #22dd88; }
.greek-pe  { color: #ee5555; }
.greek-net { color: #f0c040; }
.iv-row {
    display: flex; gap: 6px; justify-content: center; margin-bottom: 5px;
}
.iv-badge {
    display: flex; align-items: center; gap: 4px;
    background: #070f1e; border-radius: 4px; padding: 3px 10px;
    border: 1px solid #122040;
}
.iv-lbl { font-size: 0.60rem; color: #3a5878; letter-spacing: 0.10em; }
.iv-val { font-size: 0.80rem; font-weight: 700; }
.greeks-col-hdr {
    text-align: center; font-size: 0.58rem; letter-spacing: 0.12em;
    font-weight: 700; padding-bottom: 2px;
}
.greeks-close-hint { font-size: 0.55rem; color: #1e3050; text-align: right; margin-top: 4px; }
 
/* ── Header ── */
.main-title { font-family:'Bebas Neue',sans-serif; font-size:2.5rem; letter-spacing:0.10em; color:#f0c040; line-height:1; }
.sub-title  { font-size:0.8rem; color:#AAAAAA; letter-spacing:0.18em; text-transform:uppercase; }
.status-live {
    display:inline-block; background:#061510; border:1px solid #1a7a3a; color:#22dd88;
    border-radius:20px; padding:3px 14px; font-size:0.76rem; letter-spacing:0.14em;
    text-transform:uppercase; animation:blink 2s infinite;
}
.status-wait {
    display:inline-block; background:#160e00; border:1px solid #7a5500; color:#ddaa22;
    border-radius:20px; padding:3px 14px; font-size:0.76rem; letter-spacing:0.14em; text-transform:uppercase;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.35} }

/* ── ATM change annotation ── */
.atm-change-tag {
    display: inline-block;
    background: #1a1000;
    border: 1px solid #f0c04055;
    color: #f0c040;
    font-size: 0.65rem;
    border-radius: 4px;
    padding: 1px 7px;
    margin: 2px 3px;
    font-family: 'JetBrains Mono', monospace;
}
</style>
""", unsafe_allow_html=True)
 
 
# ─── Helpers ──────────────────────────────────────────────────────────────────
def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "Feed not running — start: python c:\\nifty\\feed.py"
    except json.JSONDecodeError:
        return None, None
    except Exception as e:
        return None, str(e)


def load_straddle_chart_series(series_key: str):
    """
    Load one unified series (e.g. "nifty_w1") from straddle_history.json.
    Returns list of {time, price, atm_strike} dicts, or [] on any error.
    The series is already a continuous record across ATM changes.
    """
    try:
        with open(STRADDLE_HISTORY_FILE, "r") as f:
            data = json.load(f)
        return data.get(series_key, [])
    except Exception:
        return []
 
def fmt(val, decimals=2):
    if val is None:            return "—"
    if isinstance(val, float): return f"{val:.{decimals}f}"
    if isinstance(val, int):   return str(val)
    return str(val)
 
def fmt_k(val):
    if val is None: return "—"
    try:   return f"{int(val):,}"
    except: return str(val)
 
def fmt_price(val):
    if val is None: return "—"
    try:   return f"{float(val):,.2f}"
    except: return str(val)
 
def sfmt(v):
    if v is None: return "—"
    try: return f"{float(v):,.2f}"
    except: return "—"
 
def gfmt(v, decimals=4):
    """Format a greek value; returns '—' for None."""
    if v is None: return "—"
    try: return f"{float(v):.{decimals}f}"
    except: return "—"
 
 
# ─── Pandas styler helpers ─────────────────────────────────────────────────────
def style_options_table(df, atm_strike):
    styles = []
    for i, row in df.iterrows():
        if row["_strike_raw"] == atm_strike:
            s = ["background-color:#26200a; color:#f5d020; font-weight:bold"] * len(df.columns)
        elif i % 2 == 0:
            s = ["background-color:#0d1624; color:#dde4f0"] * len(df.columns)
        else:
            s = ["background-color:#0a1018; color:#dde4f0"] * len(df.columns)
        styles.append(s)
    return pd.DataFrame(styles, index=df.index, columns=df.columns)
 
def style_otm_table(df):
    styles = []
    for i, row in df.iterrows():
        if row["_is_mid"]:
            s = ["background-color:#112440; color:#90c8f0; font-weight:bold"] * (len(df.columns) - 1)
            s += ["background-color:#112440; color:#f0c040; font-weight:bold"]
        else:
            s = ["background-color:#0c1828; color:#6880a0"] * len(df.columns)
        styles.append(s)
    return pd.DataFrame(styles, index=df.index, columns=df.columns)
 
TABLE_STYLES = [
    {"selector": "th", "props": [
        ("background-color","#152e5a"), ("color","#a8c8e8"),
        ("font-weight","700"), ("text-align","center"),
        ("font-family","JetBrains Mono,monospace"), ("font-size","13px"),
        ("padding","4px 5px"),
    ]},
    {"selector": "td", "props": [
        ("text-align","center"), ("font-family","JetBrains Mono,monospace"),
        ("font-size","12px"), ("padding","4px 5px"),
        ("border-bottom","1px solid #0d1624"),
    ]},
    {"selector": "table", "props": [("width","100%"), ("border-collapse","collapse")]},
]
 
OTM_TABLE_STYLES = [
    {"selector": "th", "props": [
        ("background-color","#0c1c38"), ("color","#608098"),
        ("font-weight","600"), ("text-align","center"),
        ("font-family","JetBrains Mono,monospace"), ("font-size","12px"),
        ("padding","3px 4px"),
    ]},
    {"selector": "td", "props": [
        ("text-align","center"), ("font-family","JetBrains Mono,monospace"),
        ("font-size","11px"), ("padding","3px 4px"),
        ("border-bottom","1px solid #0a1420"),
    ]},
    {"selector": "table", "props": [("width","100%"), ("border-collapse","collapse")]},
]
 
 
# ─── Ticker bar ───────────────────────────────────────────────────────────────
def render_ticker_bar(ticker_bar):
    def item_html(label, info, show_ohlc=True):
        spot = info.get("spot")
        pct  = info.get("pct_change")
        o    = info.get("open")
        h    = info.get("high")
        l    = info.get("low")
        pc   = info.get("day_prev_close")
        print(spot)
        ltp_str = fmt_price(spot)
        abs_chg = round(spot - pc, 2) if isinstance(spot, (int, float)) and isinstance(pc, (int, float)) else None
 
        if pct is None:
            chg_html = '<span class="pct-na">— %</span>' if show_ohlc else ""
        elif pct >= 0:
            abs_str  = f"+{abs_chg:,.2f} " if abs_chg is not None else ""
            chg_html = f'<span class="pct-up">▲ {abs_str}({pct:+.2f}%)</span>' if show_ohlc else ""
        else:
            abs_str  = f"{abs_chg:,.2f} " if abs_chg is not None else ""
            chg_html = f'<span class="pct-down">▼ {abs_str}({pct:.2f}%)</span>' if show_ohlc else ""
 
        ohlc_html = (
            f'<div class="ohlc-row">'
            f'<span><span class="ohlc-lbl">O </span><span class="ohlc-val-open">{fmt_price(o)}</span></span>'
            f'<span><span class="ohlc-lbl">H </span><span class="ohlc-val-high">{fmt_price(h)}</span></span>'
            f'<span><span class="ohlc-lbl">L </span><span class="ohlc-val-low">{fmt_price(l)}</span></span>'
            f'<span><span class="ohlc-lbl">PC </span><span class="ohlc-val-pc">{fmt_price(pc)}</span></span>'
            f'</div>'
        ) if show_ohlc else ""
        return (
            f'<div class="ticker-item">'
            f'<span class="ticker-name">{label}</span>'
            f'<span class="ticker-ltp">{ltp_str}</span>'
            f'<div class="ticker-meta">{chg_html}</div>'
            f'{ohlc_html}'
            f'</div>'
        )
 
    st.markdown(
        f'<div class="ticker-bar">'
        f'{item_html("NIFTY 50",   ticker_bar.get("nifty",     {}))}'
        f'{item_html("SENSEX",      ticker_bar.get("sensex",    {}))}'
        f'{item_html("BANK NIFTY", ticker_bar.get("banknifty", {}))}'
        f'{item_html("INDIA VIX",  ticker_bar.get("indiavix",  {}), show_ohlc=False)}'
        f'</div>',
        unsafe_allow_html=True,
    )
 
 
# ─── Straddle OHLC bar ────────────────────────────────────────────────────────
def render_straddle_bar(s):
    st.markdown(
        f'<div class="straddle-bar">'
        f'<span class="straddle-lbl">Straddle</span>'
        f'<span class="straddle-ltp-val">{sfmt(s.get("ltp"))}</span>'
        f'<div class="straddle-divider"></div>'
        f'<div class="s-item"><span class="s-key">O&nbsp;</span><span class="s-val s-open">{sfmt(s.get("open"))}</span></div>'
        f'<div class="straddle-divider"></div>'
        f'<div class="s-item"><span class="s-key">H&nbsp;</span><span class="s-val s-high">{sfmt(s.get("high"))}</span></div>'
        f'<div class="straddle-divider"></div>'
        f'<div class="s-item"><span class="s-key">L&nbsp;</span><span class="s-val s-low">{sfmt(s.get("low"))}</span></div>'
        f'<div class="straddle-divider"></div>'
        f'<div class="s-item"><span class="s-key">PC&nbsp;</span><span class="s-val s-pc">{sfmt(s.get("prev_close"))}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )
 
 
# ─── Info row (spot / fut / syn fut) ─────────────────────────────────────────
def render_info_row(state):
    st.markdown(
        f'<div class="info-row">'
        f'<span class="info-item"><span class="info-lbl">Spot</span>'
        f'<span class="info-val">{fmt_price(state.get("spot_close"))}</span></span>'
        f'<span class="info-sep">·</span>'
        f'<span class="info-item"><span class="info-lbl">Fut</span>'
        f'<span class="info-val fut-val">{fmt_price(state.get("fut_close"))}</span></span>'
        f'<span class="info-sep">·</span>'
        f'<span class="info-item"><span class="info-lbl">Syn Fut</span>'
        f'<span class="info-val syn-val">{fmt_price(state.get("synthetic_future"))}</span></span>'
        f'<span class="info-time">⏱ {state.get("updated_at","—")}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
 
 
# ─── Greeks panel ─────────────────────────────────────────────────────────────
def render_greeks_panel(panel):
    if panel is None:
        st.markdown(
            '<div class="greeks-panel" style="opacity:0.4;text-align:center;padding:20px 14px;">'
            '<div class="greeks-title">OPTION GREEKS</div>'
            '<div style="font-size:0.72rem;color:#3a5878;margin-top:10px;line-height:1.6;">'
            'Click any strike row<br>in the NIFTY or SENSEX<br>table to view greeks'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    strike = panel.get("strike")
    inst   = panel.get("instrument", "")
    week   = panel.get("week", "")
    ce_iv  = panel.get("ce_iv")
    pe_iv  = panel.get("pe_iv")
    ce_g   = panel.get("ce_greeks") or {}
    pe_g   = panel.get("pe_greeks") or {}
    net_g  = panel.get("net_greeks") or {}
    ce_ltp = panel.get("ce")
    pe_ltp = panel.get("pe")

    def greek_cells(ce_val, pe_val, net_val, dec=4):
        """Return three <div class='greek-item'> cells: CE / PE / NET."""
        return (
            f'<div class="greek-item">'
            f'<span class="greek-lbl">CE</span>'
            f'<span class="greek-val greek-ce">{gfmt(ce_val, dec)}</span>'
            f'</div>'
            f'<div class="greek-item">'
            f'<span class="greek-lbl">PE</span>'
            f'<span class="greek-val greek-pe">{gfmt(pe_val, dec)}</span>'
            f'</div>'
            f'<div class="greek-item">'
            f'<span class="greek-lbl">NET</span>'
            f'<span class="greek-val greek-net">{gfmt(net_val, dec)}</span>'
            f'</div>'
        )

    html = (
        f'<div class="greeks-panel">'
        f'<div class="greeks-title">'
        f'{inst} &nbsp;{fmt_k(strike)}'
        f'<span style="font-size:0.7rem;color:#5a7aaa;margin-left:8px;">{week.upper()}</span>'
        f'</div>'
        f'<div class="iv-row">'
        f'<div class="iv-badge">'
        f'<span class="iv-lbl">CE&nbsp;IV&nbsp;</span>'
        f'<span class="iv-val greek-ce">{gfmt(ce_iv, 2) if ce_iv is not None else "—"}%</span>'
        f'</div>'
        f'<div class="iv-badge">'
        f'<span class="iv-lbl">PE&nbsp;IV&nbsp;</span>'
        f'<span class="iv-val greek-pe">{gfmt(pe_iv, 2) if pe_iv is not None else "—"}%</span>'
        f'</div>'
        f'</div>'
        f'<div class="iv-row">'
        f'<div class="iv-badge">'
        f'<span class="iv-lbl">CE&nbsp;LTP&nbsp;</span>'
        f'<span class="iv-val greek-ce">{gfmt(ce_ltp, 2)}</span>'
        f'</div>'
        f'<div class="iv-badge">'
        f'<span class="iv-lbl">PE&nbsp;LTP&nbsp;</span>'
        f'<span class="iv-val greek-pe">{gfmt(pe_ltp, 2)}</span>'
        f'</div>'
        f'</div>'
        f'<div class="greeks-grid" style="margin-bottom:4px;">'
        f'<div class="greeks-col-hdr" style="color:#22dd88;">CALL</div>'
        f'<div class="greeks-col-hdr" style="color:#ee5555;">PUT</div>'
        f'<div class="greeks-col-hdr" style="color:#f0c040;">NET</div>'
        f'</div>'
        f'<div class="greeks-subtitle" style="text-align:center;">DELTA &nbsp;Δ</div>'
        f'<div class="greeks-grid">'
        f'{greek_cells(ce_g.get("delta"), pe_g.get("delta"), net_g.get("delta"), 4)}'
        f'</div>'
        f'<div class="greeks-subtitle" style="text-align:center;">GAMMA &nbsp;Γ</div>'
        f'<div class="greeks-grid">'
        f'{greek_cells(ce_g.get("gamma"), pe_g.get("gamma"), net_g.get("gamma"), 6)}'
        f'</div>'
        f'<div class="greeks-subtitle" style="text-align:center;">THETA &nbsp;Θ &nbsp;/ day</div>'
        f'<div class="greeks-grid">'
        f'{greek_cells(ce_g.get("theta"), pe_g.get("theta"), net_g.get("theta"), 2)}'
        f'</div>'
        f'<div class="greeks-subtitle" style="text-align:center;">VEGA &nbsp;V &nbsp;/ 1%</div>'
        f'<div class="greeks-grid">'
        f'{greek_cells(ce_g.get("vega"), pe_g.get("vega"), net_g.get("vega"), 2)}'
        f'</div>'
        f'<div class="greeks-subtitle" style="text-align:center;">RHO &nbsp;ρ &nbsp;/ 1%</div>'
        f'<div class="greeks-grid">'
        f'{greek_cells(ce_g.get("rho"), pe_g.get("rho"), net_g.get("rho"), 4)}'
        f'</div>'
        f'<div class="greeks-close-hint">auto-refreshes · click another strike to switch</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

@st.dialog("📊 Option Greeks")
def show_greeks_dialog():
    # Mark that the dialog is open this rerun so we can detect X click next rerun
    st.session_state.dialog_was_open = True
    panel = st.session_state.get("greeks_panel")
    render_greeks_panel(panel)
 
 
# ─── Options block (one instrument, one expiry) ───────────────────────────────
def render_options_block(state, name, week, is_w2=False):
    atm_strike = state.get("atm_strike")
    expiry_lbl = state.get("expiry_week1" if week == "w1" else "expiry_week2", "—")
    hdr_class  = "sec-hdr-w2" if is_w2 else "sec-hdr"
 
    st.markdown(
        f'<div class="{hdr_class}">{name} '
        f'<span style="font-size:0.9rem;color:#a0b8d0">EXP: {expiry_lbl}</span></div>',
        unsafe_allow_html=True,
    )
 
    render_straddle_bar(state.get(f"straddle_ohlc_{week}", {}))
 
    # Info row once per instrument — only on the w1 block to avoid repetition
    if not is_w2:
        render_info_row(state)
 
    rows = state.get(f"options_rows_{week}", [])
    if not rows:
        ref = state.get("spot_close") or state.get("fut_close")
        st.warning(f"⏳ No data — ATM={atm_strike}, Ref={fmt_price(ref)}")
        return
 
    records = [
        {
            "_strike_raw": r["strike"],
            "Strike":      fmt_k(r["strike"]) + (" ★" if r["is_atm"] else ""),
            "CE":          fmt(r["ce"]),
            "PE":          fmt(r["pe"]),
            "Sum":         fmt(r["sum"]),
        }
        for r in rows
    ]
    df = pd.DataFrame(records)
    display_cols = ["Strike", "CE", "PE", "Sum"]
 
    styled = (
        df[display_cols]
        .style
        .apply(lambda _: style_options_table(df, atm_strike)[display_cols], axis=None)
        .hide(axis="index")
        .set_table_styles(TABLE_STYLES)
    )
 
    # ── Row selection — clicking a row populates the Greeks panel ────────────
    sel_key = f"sel_{name}_{week}"
    event = st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        height=(len(records) + 1) * 35 + 3,
        on_select="rerun",
        selection_mode="single-row",
        key=sel_key,
    )
 
    sel_rows = event.selection.get("rows", []) if hasattr(event, "selection") else []
    if sel_rows:
        idx = sel_rows[0]
        if 0 <= idx < len(rows):
            r = rows[idx]
            candidate_key = (name, r["strike"], week)

            if candidate_key == st.session_state.get("dismissed_key"):
                pass
            else:
                st.session_state.dismissed_key = None
                st.session_state.greeks_panel = {
                    "strike":     r["strike"],
                    "instrument": name,
                    "week":       week,
                    "ce":         r.get("ce"),
                    "pe":         r.get("pe"),
                    "ce_iv":      r.get("ce_iv"),
                    "pe_iv":      r.get("pe_iv"),
                    "ce_greeks":  r.get("ce_greeks"),
                    "pe_greeks":  r.get("pe_greeks"),
                    "net_greeks": r.get("net_greeks"),
                }
                st.session_state.show_greeks_dialog = True
                if not st.session_state.pause_updates:
                    st.session_state.pause_updates = True
                    st.session_state.pause_until = time.time() + 20
 
 
# ─── Strangle OHLC bar ───────────────────────────────────────────────────────
def render_strangle_ohlc_bar(s):
    """Render the OHLC bar for a specific strangle (one OTM level)."""
    st.markdown(
        f'<div class="straddle-bar">'
        f'<span class="straddle-ltp-val">{sfmt(s.get("ltp"))}</span>'
        f'<div class="straddle-divider"></div>'
        f'<div class="s-item"><span class="s-key">O&nbsp;</span><span class="s-val s-open">{sfmt(s.get("open"))}</span></div>'
        f'<div class="straddle-divider"></div>'
        f'<div class="s-item"><span class="s-key">H&nbsp;</span><span class="s-val s-high">{sfmt(s.get("high"))}</span></div>'
        f'<div class="straddle-divider"></div>'
        f'<div class="s-item"><span class="s-key">L&nbsp;</span><span class="s-val s-low">{sfmt(s.get("low"))}</span></div>'
        f'<div class="straddle-divider"></div>'
        f'<div class="s-item"><span class="s-key">PC&nbsp;</span><span class="s-val s-pc">{sfmt(s.get("prev_close"))}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )
 
 
# ─── OTM strangle block (one instrument, one expiry) ─────────────────────────
def render_otm_block(state, name, otm_level_key, week, is_w2=False):
    atm_strike = state.get("atm_strike")
    expiry_lbl = state.get("expiry_week1" if week == "w1" else "expiry_week2", "—")
    hdr_class  = "otm-hdr-w2" if is_w2 else "otm-hdr"
 
    st.markdown(
        f'<div class="{hdr_class}">Strangle · {expiry_lbl} — ATM: {fmt_k(atm_strike)}</div>',
        unsafe_allow_html=True,
    )
 
    level = st.selectbox(
        "OTM Level",
        options=list(range(1, OTM_LEVELS + 1)),
        format_func=lambda x: f"OTM {x}",
        key=otm_level_key,
        label_visibility="collapsed",
    )
 
    # ── Strangle OHLC bar — updates when OTM level changes ───────────────────
    strangle_ohlc_all = state.get(f"strangle_ohlc_{week}", {})
    strangle_ohlc_lvl = strangle_ohlc_all.get(str(level), {})
    render_strangle_ohlc_bar(strangle_ohlc_lvl)
 
    rows = state.get(f"otm_levels_{week}", {}).get(str(level), [])
    if not rows:
        st.warning("⏳ No OTM data yet")
        return
 
    records = [
        {
            "_is_mid":   r.get("is_mid", False),
            "CE Strike": fmt_k(r["ce_strike"]),
            "CE LTP":    fmt(r["ce_ltp"]),
            "PE Strike": fmt_k(r["pe_strike"]),
            "PE LTP":    fmt(r["pe_ltp"]),
            "Sum":       fmt(r["sum"]),
        }
        for r in rows
    ]
    df = pd.DataFrame(records)
    display_cols = ["CE Strike", "CE LTP", "PE Strike", "PE LTP", "Sum"]
 
    styled = (
        df[display_cols]
        .style
        .apply(lambda _: style_otm_table(df)[display_cols], axis=None)
        .hide(axis="index")
        .set_table_styles(OTM_TABLE_STYLES)
    )
    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        height=(len(records) + 1) * 35 + 3,
    )


# ─── Straddle chart ───────────────────────────────────────────────────────────
def render_straddle_chart(series_key: str, label: str):
    """
    Render an ATM straddle price vs time line chart for one index (w1).

    Reads the unified series from straddle_history.json — each point has:
        {time: "HH:MM:SS", price: float, atm_strike: int}

    The series is continuous across ATM changes (feed.py tracks which strike
    was live at each moment).  ATM change moments are shown as annotations
    below the chart.
    """
    points = load_straddle_chart_series(series_key)

    if not points:
        st.info(f"No {label} data yet — waiting for feed…")
        return

    # Build DataFrame
    df = pd.DataFrame(points)
    df["time"] = pd.to_datetime(df["time"], format="%H:%M:%S", errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time")

    if df.empty:
        st.info(f"No valid {label} data yet")
        return

    # ── Stats ─────────────────────────────────────────────────────────────────
    latest     = df["price"].iloc[-1]
    open_price = df["price"].iloc[0]
    high_price = df["price"].max()
    low_price  = df["price"].min()
    chg        = round(latest - open_price, 2)
    chg_pct    = round(chg / open_price * 100, 2) if open_price else 0
    cur_strike = df["atm_strike"].iloc[-1]

    sign   = "+" if chg >= 0 else ""
    colour = "green" if chg >= 0 else "red"

    # Current ATM strike badge + stats row
    stats_html = (
        f'<div style="display:flex;gap:20px;align-items:baseline;flex-wrap:wrap;'
        f'font-family:JetBrains Mono,monospace;font-size:0.78rem;margin-bottom:6px;">'
        f'<span style="color:#f0c040;font-weight:700;font-size:0.9rem;">'
        f'ATM {int(cur_strike):,}</span>'
        f'<span style="color:#60a5fa;">LTP {latest:,.2f}</span>'
        f'<span style="color:#f0c040;">O {open_price:,.2f}</span>'
        f'<span style="color:#22dd88;">H {high_price:,.2f}</span>'
        f'<span style="color:#ee5555;">L {low_price:,.2f}</span>'
        f'<span style="color:{"#22dd88" if chg >= 0 else "#ee5555"};">'
        f'{sign}{chg:,.2f} ({sign}{chg_pct:.2f}%)</span>'
        f'</div>'
    )
    st.markdown(stats_html, unsafe_allow_html=True)

    # ── Line chart (time-indexed) ─────────────────────────────────────────────
    chart_df = df.set_index("time")[["price"]]
    chart_df.columns = [label]
    st.line_chart(chart_df, use_container_width=True, height=220)

    # ── ATM change annotations ────────────────────────────────────────────────
    # Find rows where atm_strike changed from the previous row
    atm_series = df["atm_strike"].tolist()
    time_series = df["time"].dt.strftime("%H:%M:%S").tolist()
    changes = []
    for i in range(1, len(atm_series)):
        if atm_series[i] != atm_series[i - 1]:
            changes.append({
                "time":  time_series[i],
                "from":  atm_series[i - 1],
                "to":    atm_series[i],
            })

    if changes:
        tags_html = "".join(
            f'<span class="atm-change-tag">⇄ {c["time"]} &nbsp;'
            f'{int(c["from"]):,} → {int(c["to"]):,}</span>'
            for c in changes
        )
        st.markdown(
            f'<div style="margin-top:4px;font-size:0.7rem;color:#64748b;">'
            f'<span style="color:#5a7aaa;margin-right:6px;">ATM changes:</span>'
            f'{tags_html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="font-size:0.7rem;color:#334155;margin-top:2px;">'
            'No ATM changes recorded today</div>',
            unsafe_allow_html=True,
        )


# ─── Load + render ────────────────────────────────────────────────────────────
data, err = load_data()
 
# ── Refresh greeks panel with latest live data BEFORE rendering ───────────────
if data and st.session_state.greeks_panel is not None and not st.session_state.pause_updates:
    gp         = st.session_state.greeks_panel
    nifty_st   = data.get("nifty",  {})
    sensex_st  = data.get("sensex", {})
    inst_state = nifty_st if gp.get("instrument") == "NIFTY" else sensex_st
    week_key   = gp.get("week", "w1")
    strike_val = gp.get("strike")
    for r in inst_state.get(f"options_rows_{week_key}", []):
        if r["strike"] == strike_val:
            st.session_state.greeks_panel = {
                "strike":     r["strike"],
                "instrument": gp["instrument"],
                "week":       week_key,
                "ce":         r.get("ce"),
                "pe":         r.get("pe"),
                "ce_iv":      r.get("ce_iv"),
                "pe_iv":      r.get("pe_iv"),
                "ce_greeks":  r.get("ce_greeks"),
                "pe_greeks":  r.get("pe_greeks"),
                "net_greeks": r.get("net_greeks"),
            }
            break

    
# ── Header row: title | status badge ─────────────────────────────────────────
tc1, tc2 = st.columns([3, 1])
with tc1:
    st.markdown('<div class="main-title">NIFTY &amp; SENSEX LIVE OPTIONS</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Real-time · 1-second refresh · Spot price</div>', unsafe_allow_html=True)
with tc2:
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<span class="status-live">⬤ Live</span>' if data
        else '<span class="status-wait">⬤ Waiting</span>',
        unsafe_allow_html=True,
    )
 
if err:
    st.error(f"⚠️ {err}")
    time.sleep(2)
    st.rerun()
elif data is None:
    time.sleep(0.5)
    st.rerun()
else:
    ticker_bar   = data.get("ticker_bar",  {})
    nifty_state  = data.get("nifty",       {})
    sensex_state = data.get("sensex",      {})
 
    render_ticker_bar(ticker_bar)
 
    for week, is_w2 in [("w1", False), ("w2", True)]:
        if is_w2:
            st.markdown(
                "<hr style='border:1px solid #1a2f40; margin:20px 0 12px 0'>",
                unsafe_allow_html=True,
            )
 
        col_left, col_mid, col_right = st.columns([2.3, 1.7, 2.3], gap="medium")
 
        with col_left:
            render_options_block(nifty_state,  "NIFTY",  week, is_w2=is_w2)
 
        with col_right:
            render_options_block(sensex_state, "SENSEX", week, is_w2=is_w2)
 
        with col_mid:
            render_otm_block(
                nifty_state,  "NIFTY",
                f"otm_nifty_{week}",  week, is_w2=is_w2,
            )
            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            render_otm_block(
                sensex_state, "SENSEX",
                f"otm_sensex_{week}", week, is_w2=is_w2,
            )

    # ── ATM Straddle Price vs Time charts ─────────────────────────────────────
    st.markdown(
        "<hr style='border:1px solid #1a2f40; margin:20px 0 12px 0'>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="font-family:Bebas Neue,sans-serif;font-size:1.6rem;'
        'letter-spacing:0.08em;color:#e8d080;margin-bottom:10px;">'
        '📊 ATM Straddle Price vs Time</div>',
        unsafe_allow_html=True,
    )

    chart_col1, chart_col2 = st.columns(2, gap="medium")

    with chart_col1:
        st.markdown(
            '<div style="font-family:Bebas Neue,sans-serif;font-size:1.1rem;'
            'color:#60a5fa;letter-spacing:0.06em;margin-bottom:4px;">NIFTY W1</div>',
            unsafe_allow_html=True,
        )
        render_straddle_chart("nifty_w1", "NIFTY W1")

    with chart_col2:
        st.markdown(
            '<div style="font-family:Bebas Neue,sans-serif;font-size:1.1rem;'
            'color:#c084fc;letter-spacing:0.06em;margin-bottom:4px;">SENSEX W1</div>',
            unsafe_allow_html=True,
        )
        render_straddle_chart("sensex_w1", "SENSEX W1")


# ── Greeks dialog ─────────────────────────────────────────────────────────────
if st.session_state.get("show_greeks_dialog", False) and st.session_state.get("greeks_panel") is not None:
    show_greeks_dialog()

if st.session_state.pause_updates:
    remaining = st.session_state.pause_until - time.time()

    if remaining > 0:
        st.stop()
    else:
        st.session_state.pause_updates = False
        st.session_state.pause_until = 0
        time.sleep(1)
        st.rerun()

else:
    # Normal 1 sec refresh
    time.sleep(1)
    st.rerun()