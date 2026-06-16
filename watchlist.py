"""
Watchlist Scanner — Quant Engine v2
Señales institucionales: Breakout, Momentum, Gap Rally, Pullback, Oversold Bounce
Calcula automáticamente Entry / Stop / Target / R:R por cada ticker
"""

import os
import streamlit as st
import requests
import yfinance as yf   # solo para el calendario de earnings (Massive no lo da en el plan de acciones)
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
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
    "NVDA": {"acc": 197.13, "pm": 167.59},  # post-venta 35 acc lunes
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

# Conversión de símbolos al formato de Massive (clases con punto, cripto con X:).
# None = mercado no cubierto por el plan de acciones (se omite del scan).
POLY_SYMBOL = {
    "BRK-B":   "BRK.B",
    "ETH-USD": "X:ETHUSD",
    "GC=F":    None,        # oro (futuro): otro mercado, sin cobertura en plan acciones
}

SIGNAL_LABELS = {
    "TREND":      "📈 Tendencia validada",
    "WATCH":      "👀 En tendencia (vigilar)",
    "PULLBACK":   "🔄 Pullback en tendencia",
    "BREAKOUT":   "🚀 Breakout c/ volumen",
    "OVERSOLD":   "🏹 Rebote sobre soporte",
    "EXTENDED":   "⚠️ Extendido (no entrar)",
    "VOL_SPIKE":  "🔥 Vol anómalo",
    "EARNINGS":   "📅 Earnings próximo",
}

# ── Hora CH ───────────────────────────────────────────────────────────────────
ch_tz       = pytz.timezone("Europe/Zurich")
now_ch      = datetime.now(ch_tz)
# Hora redondeada al minuto más cercano para el badge (evita el retraso por truncar segundos)
now_ch_disp = now_ch + timedelta(seconds=30)
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
            f'<span style="color:white;font-weight:bold">{badge} · {now_ch_disp.strftime("%H:%M")} CH</span>'
            f'</div>', unsafe_allow_html=True)

c1, c2, c3 = st.columns([2, 1, 1])
with c1: min_score = st.slider("Score mínimo para alertas", 10, 90, 40, 5)
with c2: auto      = st.checkbox("Auto-refresh 5min", value=market_open or premarket)
with c3: st.button("🔄 Actualizar", use_container_width=True)

if auto and HAS_AUTOREFRESH:
    st_autorefresh(interval=5*60*1000, key="auto")
st.caption(f"Actualizado: {now_ch.strftime('%H:%M:%S')}")


# ── API Massive (ex Polygon.io) ────────────────────────────────────────────────
API_BASE = "https://api.polygon.io"   # api.massive.com también vale; este sigue activo

def _api_key():
    try:
        return st.secrets["MASSIVE_API_KEY"]
    except Exception:
        return os.environ.get("MASSIVE_API_KEY", "")

API_KEY = _api_key()

def _poly_symbol(t):
    """Convierte el ticker de la watchlist al símbolo de Massive (None = se omite)."""
    return POLY_SYMBOL.get(t, t)


# ── Fetch ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=290)
def fetch_ohlcv():
    """Histórico diario de ~1 año por ticker (para EMA200, ROC60, swing y resistencias)."""
    to_d   = datetime.now().strftime("%Y-%m-%d")
    from_d = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    out = {}
    for t in WATCHLIST:
        sym = _poly_symbol(t)
        if not sym:
            continue
        try:
            url = (f"{API_BASE}/v2/aggs/ticker/{sym}/range/1/day/{from_d}/{to_d}"
                   f"?adjusted=true&sort=asc&limit=400&apiKey={API_KEY}")
            res = requests.get(url, timeout=20).json().get("results") or []
            if len(res) < 20:
                continue
            df = pd.DataFrame(res).rename(
                columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
            df.index = pd.to_datetime(df["t"], unit="ms")
            out[t] = df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            continue
    return out

@st.cache_data(ttl=120)
def fetch_info_all():
    """Snapshot en vivo de TODOS los tickers en una sola llamada (precio y premarket reales)."""
    out  = {t: {} for t in WATCHLIST}
    rev  = {(_poly_symbol(t) or t): t for t in WATCHLIST}
    syms = ",".join(s for s in (_poly_symbol(t) for t in WATCHLIST) if s)
    try:
        url = (f"{API_BASE}/v2/snapshot/locale/us/markets/stocks/tickers"
               f"?tickers={syms}&apiKey={API_KEY}")
        data = requests.get(url, timeout=20).json().get("tickers", []) or []
        for item in data:
            t = rev.get(item.get("ticker"))
            if not t:
                continue
            minbar = item.get("min") or {}
            last   = minbar.get("c") or (item.get("lastTrade") or {}).get("p")
            info = {}
            if premarket and last:          # solo dentro de la ventana premarket
                info["preMarketPrice"] = last
            out[t] = info
    except Exception:
        pass
    return out

@st.cache_data(ttl=300)
def fetch_news_bulk():
    """Último titular por ticker desde el endpoint de noticias de Massive."""
    out = {t: "-" for t in WATCHLIST}
    for t in WATCHLIST:
        sym = _poly_symbol(t)
        if not sym or sym.startswith("X:"):
            continue
        try:
            url = f"{API_BASE}/v2/reference/news?ticker={sym}&limit=1&apiKey={API_KEY}"
            res = requests.get(url, timeout=15).json().get("results") or []
            if res:
                title = res[0].get("title", "") or ""
                out[t] = title[:70] + ("..." if len(title) > 70 else "")
        except Exception:
            continue
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
        if ticker not in raw60:
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
        ema20_s  = ema(close, 20)
        ema50_s  = ema(close, 50)
        ema200_s = ema(close, 200) if len(close) >= 200 else ema(close, max(50, len(close) // 2))
        atr14    = atr(high, low, close, 14)
        vol_z    = vol_zscore(volume)

        rsi_val  = float(rsi14.iloc[-1])
        prev_rsi = float(rsi14.iloc[-2]) if len(rsi14) >= 2 else rsi_val
        ema20    = float(ema20_s.iloc[-1])
        ema50    = float(ema50_s.iloc[-1])
        ema200   = float(ema200_s.iloc[-1])
        atr_val  = float(atr14.iloc[-1]) if not pd.isna(atr14.iloc[-1]) else price * 0.02

        result["rsi_val"] = round(rsi_val, 1)
        result["vol_z"]   = round(vol_z, 2)
        result["atr_val"] = round(atr_val, 2)

        # ── ROC (rate of change) y pendiente de la media ──────────────────────
        roc20 = float((price / float(close.iloc[-21]) - 1) * 100) if len(close) >= 21 else 0.0
        roc60 = float((price / float(close.iloc[-61]) - 1) * 100) if len(close) >= 61 else 0.0
        ema50_slope = (float((ema50 - float(ema50_s.iloc[-6])) / float(ema50_s.iloc[-6]) * 100)
                       if len(ema50_s) >= 6 else 0.0)

        # ── Fuerza relativa vs QQQ ────────────────────────────────────────────
        qqq_pct = 0.0
        try:
            if "QQQ" in raw60:
                dq = raw60["QQQ"].dropna()
                if len(dq) >= 2:
                    qqq_pct = float((dq["Close"].iloc[-1] - dq["Close"].iloc[-2]) / dq["Close"].iloc[-2] * 100)
        except Exception:
            pass
        rel_strength = round((result["dia_pct"] or 0) - qqq_pct, 2)
        result["rel_str"] = rel_strength

        # ── Estructura: swing low y resistencia previa ────────────────────────
        swing_low = float(low.tail(20).min())
        resist_60 = float(high.tail(60).max())
        high20    = float(high.tail(20).max())

        # ── 52w distancia (sobre el propio histórico de 1 año) ────────────────
        high52 = float(high.max())
        if high52 and high52 > 0:
            result["dist52"] = round((price - high52) / high52 * 100, 1)

        # ── Tendencia (alineación EMA 20/50/200) ──────────────────────────────
        if price > ema20 > ema50 > ema200:
            result["trend"] = "↑↑↑"
        elif price > ema50 > ema200:
            result["trend"] = "↑↑"
        elif price > ema50:
            result["trend"] = "↑"
        elif price < ema20 < ema50 < ema200:
            result["trend"] = "↓↓↓"
        elif price < ema50 < ema200:
            result["trend"] = "↓↓"
        else:
            result["trend"] = "→"

        # ── Premarket (best-effort vía .info) ─────────────────────────────────
        pre_price = info.get("preMarketPrice")
        if pre_price and prev_close and prev_close > 0:
            result["pre_pct"] = round((pre_price - prev_close) / prev_close * 100, 2)

        # ── Distancia a las medias (pullback vs extensión) ────────────────────
        dist_ema20 = (price - ema20) / ema20 * 100
        dist_ema50 = (price - ema50) / ema50 * 100
        near_ema20 = -1.5 <= dist_ema20 <= 4.0
        near_ema50 = -1.5 <= dist_ema50 <= 5.0

        # ═════════════════════════════════════════════════════════════════════
        # MOTOR DE SEÑALES — rankea TODA la tendencia válida (no solo el disparo)
        # ═════════════════════════════════════════════════════════════════════

        # Gate de tendencia REAL: precio > EMA50 > EMA200, ROC positivo a 20 y 60
        # sesiones y media de 50 al alza. Descarta JNJ (ROC60 negativo) y BRK plano.
        trend_validated = (price > ema50 > ema200
                           and ema50_slope > 0
                           and roc20 > 0 and roc60 > 0)

        signals = []
        setup   = None     # PULLBACK | BREAKOUT | OVERSOLD | WATCH | EXTENDED
        entry   = None

        if trend_validated:
            signals.append("TREND")
            entry = price

            # Clasificación del momento dentro de la tendencia:
            if (near_ema20 or near_ema50) and 38 <= rsi_val <= 62 and rsi_val >= prev_rsi - 2:
                setup = "PULLBACK"      # retroceso a la media → mejor entrada
                signals.append("PULLBACK")
            elif price >= high20 * 0.995 and vol_z >= 1.0 and 50 <= rsi_val <= 72:
                setup = "BREAKOUT"      # rompiendo máximo de 20 sesiones con volumen
                signals.append("BREAKOUT")
            elif rsi_val > 70 or dist_ema20 > 9:
                setup = "EXTENDED"      # vertical: se vigila, NO se entra (caso ASML)
                signals.append("EXTENDED")
            else:
                setup = "WATCH"         # tendencia sana esperando entrada
                signals.append("WATCH")

        # OVERSOLD — rebote sobre soporte dentro de tendencia de fondo (sin gate alcista).
        elif (price > ema200 and ema50_slope > -0.5
                and rsi_val < 36 and rsi_val > prev_rsi and vol_z >= 0.5):
            setup = "OVERSOLD"
            signals.append("OVERSOLD")
            entry = price

        # VOL SPIKE — volumen anómalo (posible catalizador sin confirmar).
        if vol_z >= 2.0 and result["dia_pct"] and abs(result["dia_pct"]) >= 2:
            signals.append("VOL_SPIKE")

        # ═════════════════════════════════════════════════════════════════════
        # NIVELES + SCORE GRADUADO (puntúa todo nombre con tendencia / oversold)
        # ═════════════════════════════════════════════════════════════════════
        score = 0
        if setup and entry:
            # Stop bajo el swing low; si queda demasiado ancho, 1.5×ATR.
            struct_stop = swing_low - 0.15 * atr_val
            atr_stop    = entry - 1.5 * atr_val
            stop = (struct_stop if struct_stop < entry and (entry - struct_stop) <= 2.5 * atr_val
                    else atr_stop)
            risk = max(entry - stop, atr_val * 0.5)

            # Target = extensión medible por ATR o resistencia previa, la MAYOR.
            # (Antes usaba solo la resistencia, que en un pullback alcista queda
            #  pegada al precio y mataba el R:R. Este es el fallo que vaciaba la lista.)
            target1 = max(resist_60, entry + 3.0 * atr_val)
            reward  = target1 - entry
            rr      = round(reward / risk, 2) if risk > 0 else 0
            target2 = round(entry + reward * 1.6, 2)

            # ── Score ponderado (rankea de verdad) ───────────────────────────
            trend_q  = 14 if result["trend"] == "↑↑↑" else 9 if result["trend"] == "↑↑" else 5
            trend_q += min(10, max(0, roc60 / 2.5))        # impulso a 3 meses
            trend_q += min(6,  max(0, ema50_slope * 3))    # media subiendo
            prox     = min(abs(dist_ema20), abs(dist_ema50))
            entry_q  = max(0, 25 - prox * 2.5)             # cerca de la media = mejor
            rr_q     = min(18, max(0, (rr - 1.0) * 9))
            vol_q    = min(12, max(0, vol_z * 5))
            center   = 48 if setup in ("PULLBACK", "WATCH") else 28 if setup == "OVERSOLD" else 58
            rsi_q    = 10 - min(10, abs(rsi_val - center) / 2)
            score    = trend_q + entry_q + rr_q + vol_q + rsi_q
            if setup == "EXTENDED":
                score *= 0.55                              # penaliza perseguir extensión
            score = int(round(score))

            # Niveles operativos solo si es accionable: setup real + R:R suficiente.
            if rr >= 1.9 and setup in ("PULLBACK", "BREAKOUT", "OVERSOLD"):
                result["entry"]   = round(entry, 2)
                result["stop"]    = round(stop, 2)
                result["target1"] = round(target1, 2)
                result["target2"] = target2
                result["rr"]      = rr

        result["score"]   = min(max(score, 0), 100)
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
    raw1y    = raw60  # mismo histórico de 1 año; se mantiene el nombre por compatibilidad
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
    # Aviso de catalizador: earnings dentro de 7 días → no entrar a ciegas
    if r["earnings_dias"] is not None and r["earnings_dias"] <= 7 and r["signals"]:
        r["signals"] = r["signals"] + ["EARNINGS"]
    results.append(r)

df_all = pd.DataFrame(results)
df_all = df_all.sort_values("score", ascending=False).reset_index(drop=True)

# pandas convierte los None en NaN en columnas numéricas, y NaN es "truthy"
# (rompe los `if row[...]` del render → muestra "$nan"). Forzamos a None real
# en los campos opcionales (los que faltan cuando no hay setup accionable).
for _c in ["entry", "stop", "target1", "target2", "rr", "pre_pct", "vs_pm_propio"]:
    if _c in df_all.columns:
        _mask = df_all[_c].notna()
        df_all[_c] = df_all[_c].astype(object).where(_mask, None)

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
        Score ponderado (max 100): tendencia 30 + entrada 25 + R:R 20 + volumen 15 + RSI 10 ·
        Solo setups en tendencia validada (precio &gt; EMA50 &gt; EMA200, ROC 20/60 &gt; 0) ·
        Stop bajo el swing low (o 2×ATR) · Target a resistencia previa ·
        Solo se muestran setups con R:R ≥ 2:1
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
