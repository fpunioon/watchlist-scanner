"""
Watchlist Scanner — Quant Engine v2
Señales institucionales: Breakout, Momentum, Gap Rally, Pullback, Oversold Bounce
Calcula automáticamente Entry / Stop / Target / R:R por cada ticker
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
import warnings
warnings.filterwarnings("ignore")

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

st.set_page_config(page_title="Watchlist Scanner", layout="wide")

# ── Constantes ────────────────────────────────────────────────────────────────

# Posiciones propias — se marcan en la tabla
MIS_POSICIONES = {
    "NVDA": {"acc": 216.13, "pm": 167.59},  # post-venta 16 acc lunes
    "MSFT": {"acc": 50.53,  "pm": 374.50},
    "GOOG": {"acc": 54.27,  "pm": 274.09},
    "VOO":  {"acc": 97.70,  "pm": 601.51},
    # LLY y AMZN vendidas el lunes 09/06
}

WATCHLIST = [
    "V", "LHX", "TSM", "NRG", "ARM", "RTX", "LLY", "SNOW", "XOM",
    "NVDA", "GOOG", "MSFT", "QQQ", "AMD", "SLV", "UEC",
    "UBER", "MU", "LITE", "CLS", "BOTZ",
    "GC=F", "LMT", "GD", "TSLA", "AMZN", "GS", "PANW", "JBL", "VOO",
    "SMCI", "ASML", "JPM", "BAC", "BRK-B",
    "ABBV", "JNJ", "MRNA", "AMAT", "MRVL", "AVGO",
    "SAP", "NVO", "MSTR", "COIN", "PLTR", "ETH-USD",
]

TICKER_NAMES = {
    "ETH-USD": "Ethereum", "GC=F": "Gold",
    "MU": "Micron", "BRK-B": "Berkshire",
}

SIGNAL_LABELS = {
    "BREAKOUT":       "🚀 Breakout 52w",
    "MOMENTUM":       "⚡ Momentum",
    "GAP_RALLY":      "📈 Gap Rally",
    "PULLBACK_BUY":   "🔄 Pullback EMA",
    "OVERSOLD":       "🏹 Oversold Bounce",
    "PREMARKET_MOVE": "⏰ Pre-mkt Move",
    "RELATIVE_STRONG":"💪 Rel. Strength",
    "VOL_SPIKE":      "🔥 Vol Spike",
    "NEAR_52W":       "📌 Near 52w High",
}

# ── Hora CH ───────────────────────────────────────────────────────────────────
ch_tz       = pytz.timezone("Europe/Zurich")
now_ch      = datetime.now(ch_tz)
t_min       = now_ch.hour * 60 + now_ch.minute
es_finde    = now_ch.weekday() >= 5  # 5=sábado, 6=domingo
premarket   = not es_finde and 9*60 <= t_min < 15*60+30
market_open = not es_finde and 15*60+30 <= t_min <= 22*60

# ── Header ───────────────────────────────────────────────────────────────────
st.title("Watchlist Scanner")
if es_finde:
    dia = "domingo" if now_ch.weekday() == 6 else "sábado"
    badge = f"📅 {dia.upper()} — Mercado abre el lunes 15:30 CH"
    bcol  = "#3a3a3a"
elif premarket:
    mins = (15*60+30) - t_min
    badge = f"⏰ PREMARKET — Apertura en {mins} min"
    bcol  = "#a85a00"
elif market_open:
    badge, bcol = "🟢 MERCADO ABIERTO", "#1a7a1a"
else:
    badge, bcol = "🔴 MERCADO CERRADO", "#5c1a1a"

st.markdown(f'<div style="background:{bcol};padding:8px 16px;border-radius:8px;'
            f'display:inline-block;margin-bottom:8px">'
            f'<span style="color:white;font-weight:bold">{badge} · {now_ch.strftime("%H:%M")} CH</span>'
            f'</div>', unsafe_allow_html=True)

c1, c2, c3 = st.columns([2, 1, 1])
with c1: min_score = st.slider("Score mínimo para alertas", 10, 80, 20, 5)
with c2: auto      = st.checkbox("Auto-refresh 5min", value=market_open or premarket)
with c3: st.button("🔄 Actualizar", use_container_width=True)

if auto and HAS_AUTOREFRESH:
    st_autorefresh(interval=5*60*1000, key="auto")
st.caption(f"Actualizado: {now_ch.strftime('%H:%M:%S')}")


# ── Fetch ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=290)
def fetch_ohlcv():
    """60 días diarios — suficiente para todos los indicadores."""
    df = yf.download(WATCHLIST, period="60d", interval="1d",
                     progress=False, auto_adjust=True, group_by="ticker")
    return df

@st.cache_data(ttl=290)
def fetch_52w():
    df = yf.download(WATCHLIST, period="1y", interval="1d",
                     progress=False, auto_adjust=True, group_by="ticker")
    return df

@st.cache_data(ttl=120)
def fetch_info_all():
    out = {}
    for t in WATCHLIST:
        try:
            out[t] = yf.Ticker(t).info
        except Exception:
            out[t] = {}
    return out

@st.cache_data(ttl=120)
def fetch_news_bulk():
    out = {}
    for t in WATCHLIST:
        try:
            news = yf.Ticker(t).news
            if news:
                title = news[0].get("content", {}).get("title", "") or news[0].get("title", "")
                out[t] = title[:70] + ("..." if len(title) > 70 else "")
            else:
                out[t] = "-"
        except Exception:
            out[t] = "-"
    return out

@st.cache_data(ttl=3600)
def fetch_earnings_dates():
    """Próximas fechas de earnings para todos los tickers."""
    out = {}
    for t in WATCHLIST:
        try:
            tk = yf.Ticker(t)
            cal = tk.calendar
            if cal is not None and not cal.empty and 'Earnings Date' in cal.index:
                ed = cal.loc['Earnings Date']
                val = ed.values[0] if hasattr(ed, 'values') else ed
                if hasattr(val, 'date'):
                    out[t] = val.date()
                else:
                    out[t] = None
            else:
                out[t] = None
        except Exception:
            out[t] = None
    return out

def sentiment_noticia(titulo):
    """Sentiment básico del titular: positivo/negativo/neutro."""
    if not titulo or titulo == "-":
        return "neutro"
    titulo_lower = titulo.lower()
    pos = ["surges","soars","jumps","beats","rally","record","strong","growth",
           "upgrade","buy","bullish","rises","gains","higher","top"]
    neg = ["falls","drops","misses","cut","downgrade","sell","bearish","lower",
           "weak","decline","crash","plunges","warns","loss","disappoints"]
    score = sum(1 for w in pos if w in titulo_lower) - sum(1 for w in neg if w in titulo_lower)
    return "positivo" if score > 0 else "negativo" if score < 0 else "neutro"

def dias_para_earnings(fecha_earnings):
    """Días hasta el próximo earnings."""
    if not fecha_earnings:
        return None
    try:
        hoy = datetime.now().date()
        delta = (fecha_earnings - hoy).days
        return delta if delta >= 0 else None
    except Exception:
        return None


# ── Indicadores ──────────────────────────────────────────────────────────────

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi(series, n=14):
    delta  = series.diff()
    gain   = delta.clip(lower=0).rolling(n).mean()
    loss   = (-delta.clip(upper=0)).rolling(n).mean()
    rs     = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def atr(high, low, close, n=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def macd_signal(close):
    """Returns True if MACD line > signal line."""
    m = ema(close, 12) - ema(close, 26)
    s = ema(m, 9)
    return (m.iloc[-1] > s.iloc[-1]), float(m.iloc[-1] - s.iloc[-1])

def vol_zscore(vol_series):
    """Z-score del volumen hoy vs últimos 20 días."""
    if len(vol_series) < 5:
        return 0.0
    hist  = vol_series.iloc[:-1].tail(20)
    mu    = hist.mean()
    sigma = hist.std()
    if sigma == 0:
        return 0.0
    return float((vol_series.iloc[-1] - mu) / sigma)


# ── Motor de señales ──────────────────────────────────────────────────────────

def analyze_ticker(ticker, raw60, raw1y, info):
    """
    Devuelve dict con score, señales, entry, stop, target, indicadores.
    """
    en_cartera = ticker in MIS_POSICIONES or ticker.replace("GOOG","GOOGL") in MIS_POSICIONES
    pm_propio  = MIS_POSICIONES.get(ticker, MIS_POSICIONES.get(ticker.replace("GOOG","GOOGL"), {})).get("pm")

    result = {
        "ticker": ticker,
        "nombre": TICKER_NAMES.get(ticker, ticker),
        "score":  0,
        "signals": [],
        "entry": None, "stop": None,
        "target1": None, "target2": None,
        "rr": None,
        "price": None,
        "pre_pct": None,
        "rsi_val": None,
        "vol_z": None,
        "dist52": None,
        "trend": "-",
        "dia_pct": None,
        "atr_val": None,
        "rel_str": None,
        "en_cartera": en_cartera,
        "pm_propio": pm_propio,
        "vs_pm_propio": None,
        "earnings_dias": None,
        "sentiment": "neutro",
    }

    try:
        if ticker not in raw60.columns.get_level_values(0):
            return result
        df = raw60[ticker].dropna()
        if len(df) < 20:
            return result

        close  = df["Close"].astype(float)
        high   = df["High"].astype(float)
        low    = df["Low"].astype(float)
        volume = df["Volume"].astype(float)

        price      = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        open_today = float(df["Open"].iloc[-1])

        result["price"]   = price
        result["dia_pct"] = round((price - prev_close) / prev_close * 100, 2)

        # ── Indicadores ──────────────────────────────────────────────────────
        rsi14    = rsi(close, 14)
        ema9_s   = ema(close, 9)
        ema21_s  = ema(close, 21)
        ema50_s  = ema(close, 50)
        atr14    = atr(high, low, close, 14)
        vol_z    = vol_zscore(volume)
        macd_bull, macd_diff = macd_signal(close)

        rsi_val  = float(rsi14.iloc[-1])
        ema9     = float(ema9_s.iloc[-1])
        ema21    = float(ema21_s.iloc[-1])
        ema50    = float(ema50_s.iloc[-1])
        atr_val  = float(atr14.iloc[-1]) if not pd.isna(atr14.iloc[-1]) else price * 0.02

        result["rsi_val"] = round(rsi_val, 1)
        result["vol_z"]   = round(vol_z, 2)
        result["atr_val"] = round(atr_val, 2)

        # ── Fuerza relativa vs QQQ (calculada aquí para poder usarla abajo) ──
        qqq_pct = 0.0
        try:
            if "QQQ" in raw60.columns.get_level_values(0):
                dq = raw60["QQQ"].dropna()
                if len(dq) >= 2:
                    qqq_pct = float((dq["Close"].iloc[-1] - dq["Close"].iloc[-2]) / dq["Close"].iloc[-2] * 100)
        except Exception:
            pass
        rel_strength = round((result["dia_pct"] or 0) - qqq_pct, 2)
        result["rel_str"] = rel_strength

        # ── 52w distancia ─────────────────────────────────────────────────────
        high52 = None
        if ticker in raw1y.columns.get_level_values(0):
            df1y = raw1y[ticker].dropna()
            if not df1y.empty and "High" in df1y.columns:
                high52 = float(df1y["High"].astype(float).max())
        if high52 and high52 > 0:
            result["dist52"] = round((price - high52) / high52 * 100, 1)

        # ── Tendencia (alineación EMAs) ───────────────────────────────────────
        if price > ema9 > ema21 > ema50:
            result["trend"] = "↑↑↑"
        elif price > ema9 > ema21:
            result["trend"] = "↑↑"
        elif price > ema9:
            result["trend"] = "↑"
        elif price < ema9 < ema21 < ema50:
            result["trend"] = "↓↓↓"
        elif price < ema9 < ema21:
            result["trend"] = "↓↓"
        else:
            result["trend"] = "→"

        # ── Premarket ─────────────────────────────────────────────────────────
        pre_price = info.get("preMarketPrice")
        if pre_price and prev_close and prev_close > 0:
            result["pre_pct"] = round((pre_price - prev_close) / prev_close * 100, 2)

        # ── Vol ratio (premarket) ─────────────────────────────────────────────
        avg_vol   = info.get("averageVolume") or 0
        pre_vol   = info.get("preMarketVolume") or 0
        vol_ratio = round(pre_vol / avg_vol, 2) if avg_vol > 0 else 0

        # ── Pendiente EMA50 ───────────────────────────────────────────────────
        ema50_slope = 0.0
        if len(ema50_s) >= 5:
            ema50_slope = float((ema50_s.iloc[-1] - ema50_s.iloc[-5]) / ema50_s.iloc[-5] * 100)

        prev_rsi = float(rsi14.iloc[-2]) if len(rsi14) >= 2 else rsi_val
        in_uptrend = ema50 > float(ema50_s.iloc[-10]) if len(ema50_s) >= 10 else False
        near_ema9  = abs(price - ema9)  / ema9  < 0.015
        near_ema21 = abs(price - ema21) / ema21 < 0.025

        # ═════════════════════════════════════════════════════════════════════
        # MOTOR DE SEÑALES — funciona en mercados alcistas Y bajistas
        # ═════════════════════════════════════════════════════════════════════
        score   = 0
        signals = []

        # ── 1. BREAKOUT 52w ───────────────────────────────────────────────────
        if (result["dist52"] is not None and result["dist52"] >= -3
                and vol_z >= 0.8 and 45 <= rsi_val <= 82 and macd_bull):
            s = 40 + min(10, int(vol_z * 3))
            s += 5 if result["dist52"] >= -1 else 0
            score += s
            signals.append("BREAKOUT")

        # ── 2. MOMENTUM ──────────────────────────────────────────────────────
        if (result["trend"] in ("↑↑↑", "↑↑")
                and 50 <= rsi_val <= 78
                and macd_bull
                and result["dia_pct"] and result["dia_pct"] >= 1.0):
            s = 30
            s += 10 if result["trend"] == "↑↑↑" else 0
            s += 5  if vol_z >= 1.0 else 0
            s += 5  if rel_strength > 2 else 0
            score += s
            signals.append("MOMENTUM")

        # ── 3. GAP RALLY premarket ────────────────────────────────────────────
        if (result["pre_pct"] and result["pre_pct"] >= 1.5 and rsi_val < 78):
            s = 25 + min(20, int(abs(result["pre_pct"]) * 3))
            s += 5 if result["trend"] in ("↑↑↑", "↑↑") else 0
            score += s
            signals.append("GAP_RALLY")

        # ── 4. PULLBACK A EMA (buy the dip) ──────────────────────────────────
        if (in_uptrend and ema50_slope > 0
                and (near_ema9 or near_ema21)
                and 35 <= rsi_val <= 60):
            s = 25 + (5 if near_ema9 else 0) + (5 if ema50_slope > 0.5 else 0)
            score += s
            signals.append("PULLBACK_BUY")

        # ── 5. OVERSOLD BOUNCE ────────────────────────────────────────────────
        if (rsi_val < 38 and rsi_val > prev_rsi and vol_z >= 0.8):
            s = 25 + (10 if rsi_val < 28 else 0) + (5 if vol_z >= 1.5 else 0)
            score += s
            signals.append("OVERSOLD")

        # ── 6. FUERZA RELATIVA (aguanta cuando el mercado cae) ────────────────
        if (rel_strength >= 3 and result["dia_pct"] and result["dia_pct"] > 0
                and qqq_pct < -0.5):
            s = 20 + min(15, int(rel_strength * 2))
            score += s
            signals.append("RELATIVE_STRONG")

        # ── 7. VOL SPIKE con movimiento (catalizador sin confirmar) ───────────
        if (vol_z >= 2.0 and result["dia_pct"] and abs(result["dia_pct"]) >= 2
                and "MOMENTUM" not in signals and "BREAKOUT" not in signals):
            score += 20
            signals.append("VOL_SPIKE")

        # ── 8. CERCA DEL MÁXIMO ANUAL (resistencia → soporte potencial) ──────
        if (result["dist52"] is not None and -5 <= result["dist52"] <= -1
                and result["trend"] in ("↑↑↑", "↑↑", "↑")
                and "BREAKOUT" not in signals):
            score += 15
            signals.append("NEAR_52W")

        # ── 9. PREMARKET MOVE genérico ────────────────────────────────────────
        if (result["pre_pct"] and abs(result["pre_pct"]) >= 1.0
                and "GAP_RALLY" not in signals):
            score += 12
            signals.append("PREMARKET_MOVE")

        # ═════════════════════════════════════════════════════════════════════
        # NIVELES DE ENTRADA / STOP / TARGET (basados en ATR)
        # ═════════════════════════════════════════════════════════════════════
        if score >= 20 and signals:
            entry = pre_price if (pre_price and result["pre_pct"] and result["pre_pct"] > 0) else price

            # Stop: 1.5 ATR por debajo de entrada
            stop    = round(entry - 1.5 * atr_val, 2)
            # Target1: 2x ATR (R:R 1.33)
            target1 = round(entry + 2.0 * atr_val, 2)
            # Target2: 3.5x ATR (R:R 2.33)
            target2 = round(entry + 3.5 * atr_val, 2)
            rr      = round((target1 - entry) / (entry - stop), 2) if entry > stop else 0

            result["entry"]   = round(entry, 2)
            result["stop"]    = stop
            result["target1"] = target1
            result["target2"] = target2
            result["rr"]      = rr

        result["score"]   = min(score, 100)
        result["signals"] = signals

        # ── vs PM propio ──────────────────────────────────────────────────────
        if pm_propio and price:
            result["vs_pm_propio"] = round((price - pm_propio) / pm_propio * 100, 2)

    except Exception as e:
        pass

    return result


# ── Render helpers ────────────────────────────────────────────────────────────

def score_bar(score):
    if score >= 70:   color = "#0a7a0a"
    elif score >= 50: color = "#7a7a0a"
    elif score >= 30: color = "#7a4a0a"
    else:             color = "#3a3a3a"
    return (f'<div style="background:#1a1a1a;border-radius:4px;height:8px;width:80px;display:inline-block">'
            f'<div style="background:{color};width:{score}%;height:8px;border-radius:4px"></div></div> '
            f'<span style="color:white;font-weight:bold">{score}</span>')

def rating(score):
    if score >= 70: return '<span style="color:#7dff7d;font-weight:bold">STRONG BUY</span>'
    if score >= 50: return '<span style="color:#2db82d">BUY</span>'
    if score >= 35: return '<span style="color:#ffd700">WATCH</span>'
    return '<span style="color:#888">-</span>'

def pct_cell(v, bold=False):
    if v is None: return "-"
    fw = "font-weight:bold;" if bold else ""
    if v >= 3:    return f'<span style="color:#7dff7d;{fw}">{v:+.2f}%</span>'
    if v >= 1:    return f'<span style="color:#2db82d;{fw}">{v:+.2f}%</span>'
    if v > 0:     return f'<span style="color:#90ee90;{fw}">{v:+.2f}%</span>'
    if v >= -1:   return f'<span style="color:#ff9999;{fw}">{v:+.2f}%</span>'
    return f'<span style="color:#ff6666;{fw}">{v:+.2f}%</span>'

def rsi_cell(v):
    if v is None: return "-"
    if v >= 75:   return f'<span style="color:#ff9999">{v:.0f} ⚠️</span>'
    if v >= 60:   return f'<span style="color:#7dff7d">{v:.0f}</span>'
    if v >= 45:   return f'<span style="color:#90ee90">{v:.0f}</span>'
    if v <= 30:   return f'<span style="color:#ff6666;font-weight:bold">{v:.0f} 🏹</span>'
    return f'<span style="color:#aaa">{v:.0f}</span>'

def volz_cell(v):
    if v is None: return "-"
    if v >= 2.0:  return f'<span style="color:#ffd700;font-weight:bold">{v:.1f}σ 🔥</span>'
    if v >= 1.0:  return f'<span style="color:#ffd700">{v:.1f}σ</span>'
    if v >= 0.0:  return f'<span style="color:#888">{v:.1f}σ</span>'
    return f'<span style="color:#555">{v:.1f}σ</span>'

def trend_cell(t):
    m = {"↑↑↑": "#7dff7d", "↑↑": "#2db82d", "↑": "#90ee90",
         "↓↓↓": "#ff6666", "↓↓": "#ff9999", "↓": "#ffaaaa", "→": "#888"}
    c = m.get(t, "#888")
    return f'<span style="color:{c};font-weight:bold">{t}</span>'


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

with st.spinner("Analizando mercado..."):
    raw60    = fetch_ohlcv()
    raw1y    = fetch_52w()
    infos    = fetch_info_all()
    news     = fetch_news_bulk()
    earnings = fetch_earnings_dates()

# Analizar todos los tickers
results = []
for t in WATCHLIST:
    r = analyze_ticker(t, raw60, raw1y, infos.get(t, {}))
    titulo = news.get(t, "-")
    r["noticia"]       = titulo
    r["sentiment"]     = sentiment_noticia(titulo)
    r["earnings_dias"] = dias_para_earnings(earnings.get(t))
    results.append(r)

df_all = pd.DataFrame(results)
df_all = df_all.sort_values("score", ascending=False).reset_index(drop=True)

# ── TAB layout ────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["🎯 Oportunidades", "🌅 Radar Premarket", "📋 Todas las posiciones"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OPORTUNIDADES
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    ops = df_all[df_all["score"] >= min_score].copy()

    if ops.empty:
        st.info(f"Sin oportunidades con score ≥ {min_score}. Baja el umbral o espera a premarket.")
    else:
        st.markdown(f"### {len(ops)} oportunidades detectadas")

        # Cards top 4
        top4 = ops.head(4)
        cols = st.columns(4)
        for idx, (_, row) in enumerate(top4.iterrows()):
            with cols[idx]:
                sigs      = " · ".join([SIGNAL_LABELS.get(s, s) for s in row["signals"]])
                color     = "#0a3a0a" if row["score"] >= 50 else "#3a3a0a"
                pre_str   = f"Pre: {row['pre_pct']:+.2f}%" if row["pre_pct"] else ""
                entry_str = f"Entry: ${row['entry']}" if row["entry"] else ""
                rr_str    = f"R:R {row['rr']}:1" if row["rr"] else ""
                price_disp = f"{row['price']:.2f}" if row["price"] else "-"
                cartera_badge = "🏦 EN CARTERA" if row.get("en_cartera") else ""
                earn_str = f"⚠️ Earnings en {row['earnings_dias']}d" if row.get("earnings_dias") and row["earnings_dias"] <= 14 else ""
                sent_icon = "📰🟢" if row.get("sentiment") == "positivo" else "📰🔴" if row.get("sentiment") == "negativo" else ""
                st.markdown(f"""
                    <div style="background:{color};padding:14px;border-radius:10px;
                                border:1px solid #2a5a2a;margin-bottom:8px">
                        <div style="color:white;font-size:1.2em;font-weight:bold">{row['nombre']} {cartera_badge}</div>
                        <div style="color:#7dff7d;font-size:1.4em;font-weight:bold">Score: {row['score']}</div>
                        <div style="color:#ffd700;font-size:0.75em;margin:4px 0">{sigs}</div>
                        <div style="color:#ccc;font-size:0.8em">${price_disp} · RSI {row['rsi_val'] or '-'}</div>
                        <div style="color:#aaa;font-size:0.75em">{entry_str} · {rr_str}</div>
                        <div style="color:#90ee90;font-size:0.7em">{pre_str} {earn_str} {sent_icon}</div>
                    </div>
                """, unsafe_allow_html=True)

        st.divider()

        # Tabla detallada de oportunidades
        st.markdown("#### Detalle de señales y niveles operativos")

        COLS = ["Ticker", "Score", "Rating", "Señales", "Precio", "vs PM",
                "Pre %", "Entry", "Stop", "T1", "R:R",
                "RSI", "Vol σ", "Trend", "Earnings", "Noticia"]

        header_html = "".join(
            f'<th style="padding:7px 10px;text-align:left;border-bottom:2px solid #333;'
            f'color:#aaa;white-space:nowrap;font-size:0.8em">{c}</th>'
            for c in COLS
        )

        rows_html = ""
        for _, row in ops.iterrows():
            sigs_html = " ".join([
                f'<span style="background:#1a3a1a;color:#7dff7d;padding:1px 5px;'
                f'border-radius:3px;font-size:0.7em">{SIGNAL_LABELS.get(s,s)}</span>'
                for s in row["signals"]
            ])
            price_str  = f"${row['price']:.2f}"  if row["price"]   else "-"
            entry_str  = f"${row['entry']:.2f}"  if row["entry"]   else "-"
            stop_str   = f"${row['stop']:.2f}"   if row["stop"]    else "-"
            t1_str     = f"${row['target1']:.2f}" if row["target1"] else "-"
            t2_str     = f"${row['target2']:.2f}" if row["target2"] else "-"
            rr_str     = f"{row['rr']:.1f}:1"    if row["rr"]      else "-"
            noticia    = (row["noticia"] or "-")[:55]

            # Color fila por score
            if row["score"] >= 70:   bg = "background:#0a1a0a;"
            elif row["score"] >= 50: bg = "background:#111a0a;"
            elif row["score"] >= 35: bg = "background:#1a1a0a;"
            else:                    bg = ""

            # Campos nuevos
            vs_pm_str = pct_cell(row.get("vs_pm_propio")) if row.get("en_cartera") else "-"
            cartera_label = f'🏦 {row["nombre"]}' if row.get("en_cartera") else row["nombre"]

            earn_d = row.get("earnings_dias")
            if earn_d is not None and earn_d <= 7:
                earn_str = f'<span style="color:#ff6666;font-weight:bold">⚠️ {earn_d}d</span>'
            elif earn_d is not None and earn_d <= 14:
                earn_str = f'<span style="color:#ffd700">{earn_d}d</span>'
            elif earn_d is not None:
                earn_str = f'<span style="color:#888">{earn_d}d</span>'
            else:
                earn_str = "-"

            sent = row.get("sentiment", "neutro")
            sent_html = (f'<span style="color:#7dff7d">🟢</span>' if sent == "positivo"
                        else f'<span style="color:#ff6666">🔴</span>' if sent == "negativo"
                        else "")

            cells = (
                f'<td style="padding:7px 10px;white-space:nowrap;font-weight:bold;color:white">{cartera_label}</td>'
                f'<td style="padding:7px 10px">{score_bar(row["score"])}</td>'
                f'<td style="padding:7px 10px">{rating(row["score"])}</td>'
                f'<td style="padding:7px 10px;min-width:180px">{sigs_html}</td>'
                f'<td style="padding:7px 10px;color:#ccc">{price_str}</td>'
                f'<td style="padding:7px 10px">{vs_pm_str}</td>'
                f'<td style="padding:7px 10px">{pct_cell(row["pre_pct"])}</td>'
                f'<td style="padding:7px 10px;color:#7dff7d;font-weight:bold">{entry_str}</td>'
                f'<td style="padding:7px 10px;color:#ff9999">{stop_str}</td>'
                f'<td style="padding:7px 10px;color:#90ee90">{t1_str}</td>'
                f'<td style="padding:7px 10px;color:#ffd700;font-weight:bold">{rr_str}</td>'
                f'<td style="padding:7px 10px">{rsi_cell(row["rsi_val"])}</td>'
                f'<td style="padding:7px 10px">{volz_cell(row["vol_z"])}</td>'
                f'<td style="padding:7px 10px">{trend_cell(row["trend"])}</td>'
                f'<td style="padding:7px 10px;text-align:center">{earn_str}</td>'
                f'<td style="padding:7px 10px;color:#888;font-size:0.8em">{sent_html} {noticia}</td>'
            )
            rows_html += f'<tr style="{bg}border-bottom:1px solid #1a1a1a">{cells}</tr>'

        st.markdown(f"""
        <div style="overflow-x:auto">
        <table style="border-collapse:collapse;width:100%;font-size:0.82em">
            <thead><tr style="background:#0e1117">{header_html}</tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
        </div>
        <div style="margin-top:8px;color:#666;font-size:0.75em">
        Score: suma de señales ponderadas (max 100) ·
        Entry = precio actual/premarket · Stop = Entry − 1.5×ATR14 ·
        T1 = Entry + 2×ATR14 · T2 = Entry + 3.5×ATR14 ·
        Vol σ = desviación estándar del volumen vs 20d
        </div>
        """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — RADAR PREMARKET
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### 🌅 Radar Premarket — Movimientos antes de apertura USA")

    pm_df = df_all[df_all["pre_pct"].notna()].copy()
    pm_df = pm_df.sort_values("pre_pct", ascending=False).reset_index(drop=True)

    if pm_df.empty:
        st.info("Datos premarket no disponibles fuera de horario premarket (09:00–15:30 CH).")
    else:
        # Cards top movers premarket
        top_pm = pm_df[pm_df["pre_pct"].abs() >= 1].head(4)
        if not top_pm.empty:
            cols = st.columns(min(len(top_pm), 4))
            for idx, (_, row) in enumerate(top_pm.iterrows()):
                pct   = row["pre_pct"]
                color = "#0a3a0a" if pct >= 0 else "#3a0a0a"
                with cols[idx]:
                    st.markdown(f"""
                        <div style="background:{color};padding:14px;border-radius:10px;
                                    text-align:center;border:1px solid #2a2a2a">
                            <div style="color:white;font-size:1.2em;font-weight:bold">{row['nombre']}</div>
                            <div style="color:#7dff7d;font-size:1.6em;font-weight:bold">{pct:+.2f}%</div>
                            <div style="color:#ffd700;font-size:0.8em">Score: {row['score']}</div>
                            <div style="color:#aaa;font-size:0.75em">{row['trend']} · RSI {row['rsi_val'] or '-'}</div>
                        </div>
                    """, unsafe_allow_html=True)
            st.divider()

        # Tabla completa premarket
        PM_COLS = ["Ticker", "Pre %", "Precio", "Día %", "Score", "Señales",
                   "Entry", "Stop", "T1", "R:R", "RSI", "Vol σ", "Trend"]
        hdr = "".join(
            f'<th style="padding:6px 10px;text-align:left;border-bottom:2px solid #333;'
            f'color:#aaa;white-space:nowrap;font-size:0.8em">{c}</th>'
            for c in PM_COLS
        )
        rows = ""
        for _, row in pm_df.iterrows():
            pct = row["pre_pct"]
            if pct >= 3:    pre_bg = "background:#0a5a0a;color:white;"
            elif pct >= 1:  pre_bg = "background:#1a7a1a;color:white;"
            elif pct > 0:   pre_bg = "background:#2a5a2a;color:white;"
            elif pct <= -3: pre_bg = "background:#5a0a0a;color:white;"
            elif pct < 0:   pre_bg = "background:#3a0a0a;color:white;"
            else:            pre_bg = ""

            sigs = " ".join([
                f'<span style="background:#1a3a1a;color:#7dff7d;padding:1px 4px;'
                f'border-radius:3px;font-size:0.68em">{SIGNAL_LABELS.get(s,s)}</span>'
                for s in row["signals"]
            ]) or "-"

            cells = (
                f'<td style="padding:6px 10px;font-weight:bold;color:white">{row["nombre"]}</td>'
                f'<td style="padding:6px 10px;{pre_bg}border-radius:4px;font-weight:bold">{pct:+.2f}%</td>'
                f'<td style="padding:6px 10px;color:#ccc">${row["price"]:.2f}</td>'
                f'<td style="padding:6px 10px">{pct_cell(row["dia_pct"])}</td>'
                f'<td style="padding:6px 10px">{score_bar(row["score"])}</td>'
                f'<td style="padding:6px 10px;min-width:160px">{sigs}</td>'
                f'<td style="padding:6px 10px;color:#7dff7d">${round(row["entry"],2) if row["entry"] else "-"}</td>'
                f'<td style="padding:6px 10px;color:#ff9999">${round(row["stop"],2) if row["stop"] else "-"}</td>'
                f'<td style="padding:6px 10px;color:#90ee90">${round(row["target1"],2) if row["target1"] else "-"}</td>'
                f'<td style="padding:6px 10px;color:#ffd700">{str(round(row["rr"],1))+":1" if row["rr"] else "-"}</td>'
                f'<td style="padding:6px 10px">{rsi_cell(row["rsi_val"])}</td>'
                f'<td style="padding:6px 10px">{volz_cell(row["vol_z"])}</td>'
                f'<td style="padding:6px 10px">{trend_cell(row["trend"])}</td>'
            )
            rows += f'<tr style="border-bottom:1px solid #1a1a1a">{cells}</tr>'

        st.markdown(f"""
        <div style="overflow-x:auto">
        <table style="border-collapse:collapse;width:100%;font-size:0.82em">
            <thead><tr style="background:#0e1117">{hdr}</tr></thead>
            <tbody>{rows}</tbody>
        </table>
        </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — TODAS LAS POSICIONES
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### 📋 Todas las posiciones")

    ALL_COLS = ["Ticker", "Precio", "Día %", "Pre %", "Score",
                "RSI", "Vol σ", "Trend", "vs 52w", "Noticia"]
    hdr = "".join(
        f'<th style="padding:6px 10px;text-align:left;border-bottom:2px solid #333;'
        f'color:#aaa;white-space:nowrap">{c}</th>'
        for c in ALL_COLS
    )

    all_sorted = df_all.sort_values("dia_pct", ascending=False).reset_index(drop=True)
    rows = ""
    for _, row in all_sorted.iterrows():
        dist52_str = f"{row['dist52']:.1f}%" if row["dist52"] is not None else "-"
        if row["dist52"] is not None:
            if row["dist52"] >= -3:    d52_style = "background:#0a5a0a;color:white;font-weight:bold;border-radius:4px;"
            elif row["dist52"] >= -10: d52_style = "color:#90ee90;"
            elif row["dist52"] <= -30: d52_style = "color:#ff6666;"
            else:                      d52_style = "color:#ff9999;"
        else:
            d52_style = "color:#555;"

        cells = (
            f'<td style="padding:6px 10px;font-weight:bold;color:white">{row["nombre"]}</td>'
            f'<td style="padding:6px 10px;color:#ccc">${round(row["price"],2) if row["price"] else "-"}</td>'
            f'<td style="padding:6px 10px">{pct_cell(row["dia_pct"], bold=True)}</td>'
            f'<td style="padding:6px 10px">{pct_cell(row["pre_pct"])}</td>'
            f'<td style="padding:6px 10px">{score_bar(row["score"])}</td>'
            f'<td style="padding:6px 10px">{rsi_cell(row["rsi_val"])}</td>'
            f'<td style="padding:6px 10px">{volz_cell(row["vol_z"])}</td>'
            f'<td style="padding:6px 10px">{trend_cell(row["trend"])}</td>'
            f'<td style="padding:6px 10px;{d52_style}">{dist52_str}</td>'
            f'<td style="padding:6px 10px;color:#777;font-size:0.8em">{(row["noticia"] or "-")[:55]}</td>'
        )
        rows += f'<tr style="border-bottom:1px solid #1a1a1a">{cells}</tr>'

    st.markdown(f"""
    <div style="overflow-x:auto;max-height:750px;overflow-y:auto">
    <table style="border-collapse:collapse;width:100%;font-size:0.82em">
        <thead><tr style="position:sticky;top:0;background:#0e1117">{hdr}</tr></thead>
        <tbody>{rows}</tbody>
    </table>
    </div>
    <div style="margin-top:8px;color:#666;font-size:0.75em">
    Vol σ = desviación estándar del volumen vs 20 días ·
    vs 52w = distancia al máximo anual ·
    Trend = alineación EMA 9/21/50
    </div>
    """, unsafe_allow_html=True)
