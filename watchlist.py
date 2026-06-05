import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

st.set_page_config(page_title="Watchlist Scanner", layout="wide")
st.title("Watchlist Scanner")

WATCHLIST = [
    "V", "LHX", "TSM", "NRG", "ARM", "RTX", "LLY", "SNOW", "XOM", "RHM.DE",
    "NVDA", "GOOG", "MSFT", "QQQ", "ETH-USD", "CCJ", "AMD", "SLV", "UEC",
    "UBER", "ACX.MC", "MU", "LITE", "CLS", "BOTZ",
    "GC=F", "LMT", "GD", "TSLA", "AMZN", "GS", "PANW", "JBL", "VOO", "SMCI", "ASML",
    "JPM", "BAC", "BRK-B",
    "ABBV", "JNJ", "MRNA",
    "AMAT", "MRVL", "AVGO",
    "SAP", "NVO",
    "MSTR", "COIN", "PLTR",
]

TICKER_NAMES = {
    "RHM.DE": "Rheinmetall", "ETH-USD": "Ethereum",
    "ACX.MC": "Acerinox", "GC=F": "Gold Futures", "MU": "Micron",
}

# ── Hora suiza ────────────────────────────────────────────────────────────────
ch_tz = pytz.timezone("Europe/Zurich")
now_ch = datetime.now(ch_tz)
t_min = now_ch.hour * 60 + now_ch.minute

def is_premarket(): return 9*60 <= t_min < 15*60+30
def is_open():      return 15*60+30 <= t_min <= 22*60

premarket   = is_premarket()
market_open = is_open()

# ── Estado mercado ────────────────────────────────────────────────────────────
if premarket:
    mins_left = (15*60+30) - t_min
    estado = f"⏰ PREMARKET — Apertura en {mins_left} min"
    estado_color = "#a85a00"
elif market_open:
    estado = "🟢 MERCADO ABIERTO"
    estado_color = "#1a7a1a"
else:
    estado = "🔴 MERCADO CERRADO"
    estado_color = "#5c1a1a"

st.markdown(f"""
    <div style="background:{estado_color};padding:8px 16px;border-radius:8px;display:inline-block;margin-bottom:8px">
        <span style="color:white;font-weight:bold">{estado} · {now_ch.strftime('%H:%M')} CH</span>
    </div>
""", unsafe_allow_html=True)

# ── Controles ────────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    min_change = st.slider("Alerta a partir de %", 0.0, 10.0, 2.0, 0.5)
with c2:
    auto = st.checkbox("Auto-refresh 5min", value=market_open or premarket)
with c3:
    st.button("🔄 Actualizar", use_container_width=True)

if auto and HAS_AUTOREFRESH:
    st_autorefresh(interval=5 * 60 * 1000, key="auto")

st.caption(f"Actualizado: {now_ch.strftime('%H:%M:%S')}")


# ── Datos ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=290)
def fetch_data():
    raw5d = yf.download(WATCHLIST, period="5d", interval="1d",
                        progress=False, auto_adjust=True, group_by="ticker")
    raw1y = yf.download(WATCHLIST, period="1y", interval="1d",
                        progress=False, auto_adjust=True, group_by="ticker")
    return raw5d, raw1y


@st.cache_data(ttl=120)
def fetch_premarket_data():
    """Fetch premarket info para todos los tickers."""
    out = {}
    for t in WATCHLIST:
        try:
            info = yf.Ticker(t).info
            out[t] = info
        except Exception:
            out[t] = {}
    return out


def get_info_batch(tickers):
    out = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            out[t] = info
        except Exception:
            out[t] = {}
    return out


def get_news(ticker):
    try:
        news = yf.Ticker(ticker).news
        if news:
            return news[0].get("content", {}).get("title", "") or news[0].get("title", "")
    except Exception:
        pass
    return "-"


def scan():
    raw5d, raw1y = fetch_data()
    results = []

    for ticker in WATCHLIST:
        try:
            if ticker not in raw5d.columns.get_level_values(0):
                continue
            df5 = raw5d[ticker].dropna()
            if df5.empty or len(df5) < 2:
                continue

            prev_close = float(df5["Close"].iloc[-2])
            curr_close = float(df5["Close"].iloc[-1])
            open_today = float(df5["Open"].iloc[-1])

            dia_pct  = round((curr_close - prev_close) / prev_close * 100, 2)
            open_pct = round((curr_close - open_today) / open_today * 100, 2) if open_today else None

            vol_hoy  = float(df5["Volume"].iloc[-1])
            vol_avg  = float(df5["Volume"].rolling(5).mean().iloc[-1])
            vol_ratio = round(vol_hoy / vol_avg, 2) if vol_avg > 0 else 0

            max52 = None
            dist52 = None
            if ticker in raw1y.columns.get_level_values(0):
                df1y = raw1y[ticker].dropna()
                if not df1y.empty and "High" in df1y.columns:
                    max52 = float(df1y["High"].max())
                    dist52 = round((curr_close - max52) / max52 * 100, 1)

            nombre = TICKER_NAMES.get(ticker, ticker)
            results.append({
                "Ticker":       nombre,
                "_ticker":      ticker,
                "Precio":       round(curr_close, 2),
                "Día %":        dia_pct,
                "Desde open %": open_pct if open_pct is not None else "-",
                "Premarket %":  "-",
                "_pre_pct":     None,
                "vs 52w max":   f"{dist52:.1f}%" if dist52 is not None else "-",
                "_dist52":      dist52,
                "Vol ratio":    vol_ratio,
                "Noticia":      "-",
            })
        except Exception:
            continue

    df_r = pd.DataFrame(results)
    if df_r.empty:
        return df_r
    return df_r.sort_values("Día %", ascending=False).reset_index(drop=True)


# ── Helpers HTML ──────────────────────────────────────────────────────────────

def fmt_pct(v, decimals=2):
    if isinstance(v, (int, float)) and not pd.isna(v):
        return f"{v:+.{decimals}f}%"
    return str(v)


def cell_color(v, thresholds=((5,"#0a7a0a"),(2,"#1a9a1a"),(0,"#2db82d"),(-2,"#8a1a1a"))):
    try:
        n = float(v)
        for thr, bg in thresholds:
            if n >= thr:
                return bg, "white"
        return "#6b0a0a", "white"
    except:
        return None, None


# ── TABLA PREMARKET ───────────────────────────────────────────────────────────

def render_premarket_table():
    """Tabla con todos los valores ordenados por movimiento premarket."""

    st.subheader("🌅 Radar Premarket — Oportunidades antes de apertura USA")

    with st.spinner("Cargando datos premarket..."):
        infos = fetch_premarket_data()
        raw5d, raw1y = fetch_data()

    rows = []
    for ticker in WATCHLIST:
        try:
            info = infos.get(ticker, {})
            nombre = TICKER_NAMES.get(ticker, ticker)

            # Precio premarket y cierre anterior
            pre_price  = info.get("preMarketPrice")
            prev_close = info.get("regularMarketPreviousClose")
            curr_price = info.get("regularMarketPrice") or info.get("currentPrice")
            mkt_cap    = info.get("marketCap")

            pre_pct = None
            pre_vol = info.get("preMarketVolume")

            if pre_price and prev_close and prev_close > 0:
                pre_pct = round((pre_price - prev_close) / prev_close * 100, 2)

            # Día anterior %
            day_pct = None
            if curr_price and prev_close and prev_close > 0:
                day_pct = round((curr_price - prev_close) / prev_close * 100, 2)

            # Vol ratio (premarket vol vs avg diario)
            avg_vol = info.get("averageVolume")
            pre_vol_ratio = None
            if pre_vol and avg_vol and avg_vol > 0:
                pre_vol_ratio = round(pre_vol / avg_vol, 2)

            # 52w high
            high52 = info.get("fiftyTwoWeekHigh")
            dist52 = None
            if high52 and curr_price and high52 > 0:
                dist52 = round((curr_price - high52) / high52 * 100, 1)

            # Tendencia (últimos 5 días)
            trend = "-"
            if ticker in raw5d.columns.get_level_values(0):
                df5 = raw5d[ticker].dropna()
                if len(df5) >= 3:
                    closes = df5["Close"].iloc[-3:].values
                    if closes[-1] > closes[-2] > closes[-3]:
                        trend = "↑↑↑"
                    elif closes[-1] > closes[-2]:
                        trend = "↑↑"
                    elif closes[-1] < closes[-2] < closes[-3]:
                        trend = "↓↓↓"
                    elif closes[-1] < closes[-2]:
                        trend = "↓↓"
                    else:
                        trend = "→"

            # Score rally (0-100)
            score = 0
            score_detail = []
            if pre_pct is not None:
                if pre_pct >= 3:   score += 35; score_detail.append(f"Pre+{pre_pct:.1f}%")
                elif pre_pct >= 1: score += 20; score_detail.append(f"Pre+{pre_pct:.1f}%")
                elif pre_pct > 0:  score += 10; score_detail.append(f"Pre+{pre_pct:.1f}%")
            if pre_vol_ratio and pre_vol_ratio >= 0.3:
                score += 20; score_detail.append(f"Vol{pre_vol_ratio:.1f}x")
            if dist52 is not None and dist52 >= -5:
                score += 20; score_detail.append("Near52w")
            if trend in ("↑↑↑", "↑↑"):
                score += 15; score_detail.append("Trend↑")
            if day_pct and day_pct >= 1:
                score += 10; score_detail.append(f"Día+{day_pct:.1f}%")

            rows.append({
                "Ticker":       nombre,
                "_ticker":      ticker,
                "Pre $":        f"{pre_price:.2f}" if pre_price else "-",
                "Pre %":        pre_pct,
                "Cierre ant.":  f"{prev_close:.2f}" if prev_close else "-",
                "Día ant. %":   day_pct,
                "Pre Vol ratio":pre_vol_ratio,
                "vs 52w max":   dist52,
                "Tendencia":    trend,
                "Score Rally":  score,
                "_score_detail":"; ".join(score_detail),
            })

        except Exception:
            continue

    if not rows:
        st.warning("Sin datos premarket disponibles.")
        return

    df_pm = pd.DataFrame(rows)
    # Ordenar por Score Rally desc, luego Pre % desc
    df_pm = df_pm.sort_values(["Score Rally", "Pre %"], ascending=[False, False]).reset_index(drop=True)

    # ── Cards top 4 por score ─────────────────────────────────────────────────
    top4 = df_pm[df_pm["Score Rally"] > 0].head(4)
    if not top4.empty:
        st.markdown("**🎯 Top candidatos al rally hoy:**")
        cols = st.columns(min(len(top4), 4))
        for idx, (_, row) in enumerate(top4.iterrows()):
            pre_pct = row["Pre %"]
            score   = row["Score Rally"]
            color   = "#0a4a0a" if (pre_pct or 0) >= 0 else "#4a0a0a"
            pre_str = f"{pre_pct:+.2f}%" if pre_pct is not None else "n/d"
            score_color = "#7dff7d" if score >= 50 else "#ffd700" if score >= 30 else "#ff9999"
            with cols[idx]:
                st.markdown(f"""
                    <div style="background:{color};padding:14px;border-radius:10px;text-align:center;border:1px solid #2a2a2a">
                        <div style="color:white;font-size:1.2em;font-weight:bold">{row['Ticker']}</div>
                        <div style="color:#7dff7d;font-size:1.6em;font-weight:bold">{pre_str}</div>
                        <div style="color:{score_color};font-size:0.9em;font-weight:bold">Score: {score}/100</div>
                        <div style="color:#aaa;font-size:0.7em;margin-top:4px">{row['_score_detail']}</div>
                        <div style="color:#888;font-size:0.8em">{row['Tendencia']}</div>
                    </div>
                """, unsafe_allow_html=True)
        st.divider()

    # ── Tabla completa premarket ──────────────────────────────────────────────
    cols_pm = ["Ticker", "Pre $", "Pre %", "Cierre ant.", "Día ant. %",
               "Pre Vol ratio", "vs 52w max", "Tendencia", "Score Rally"]

    header = "".join(
        f'<th style="padding:6px 10px;text-align:left;border-bottom:1px solid #333;color:#aaa;white-space:nowrap">{c}</th>'
        for c in cols_pm
    )

    rows_html = ""
    for _, row in df_pm.iterrows():
        cells = ""
        for col in cols_pm:
            val = row[col]
            style = "padding:6px 10px;white-space:nowrap;"

            if col == "Pre %":
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    if val >= 3:    style += "background:#0a7a0a;color:white;font-weight:bold;border-radius:4px;"
                    elif val >= 1:  style += "background:#1a9a1a;color:white;border-radius:4px;"
                    elif val > 0:   style += "background:#2db82d;color:white;border-radius:4px;"
                    elif val <= -3: style += "background:#6b0a0a;color:white;font-weight:bold;border-radius:4px;"
                    elif val < 0:   style += "background:#8a1a1a;color:white;border-radius:4px;"
                    val = f"{val:+.2f}%"
                else:
                    val = "-"

            elif col == "Día ant. %":
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    if val >= 2:    style += "color:#7dff7d;font-weight:bold;"
                    elif val > 0:   style += "color:#2db82d;"
                    elif val <= -2: style += "color:#ff6666;font-weight:bold;"
                    elif val < 0:   style += "color:#ff9999;"
                    val = f"{val:+.2f}%"
                else:
                    val = "-"

            elif col == "Pre Vol ratio":
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    if val >= 0.5:  style += "color:#ffd700;font-weight:bold;"
                    elif val >= 0.2:style += "color:#90ee90;"
                    val = f"{val:.2f}x"
                else:
                    val = "-"

            elif col == "vs 52w max":
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    if val >= -3:   style += "background:#0a7a0a;color:white;font-weight:bold;border-radius:4px;"
                    elif val >= -10:style += "color:#90ee90;"
                    elif val <= -30:style += "color:#ff6666;"
                    else:           style += "color:#ff9999;"
                    val = f"{val:.1f}%"
                else:
                    val = "-"

            elif col == "Score Rally":
                if isinstance(val, (int, float)) and val > 0:
                    if val >= 60:   style += "background:#0a5a0a;color:#7dff7d;font-weight:bold;border-radius:4px;"
                    elif val >= 40: style += "background:#3a3a0a;color:#ffd700;font-weight:bold;border-radius:4px;"
                    elif val >= 20: style += "color:#ffd700;"
                    val = f"{int(val)}"
                else:
                    val = "-"

            elif col == "Tendencia":
                if val == "↑↑↑":   style += "color:#7dff7d;font-weight:bold;"
                elif val == "↑↑":  style += "color:#2db82d;"
                elif val == "↓↓↓": style += "color:#ff6666;font-weight:bold;"
                elif val == "↓↓":  style += "color:#ff9999;"
                else:               style += "color:#888;"

            cells += f'<td style="{style}">{val}</td>'
        rows_html += f"<tr style='border-bottom:1px solid #1a1a1a'>{cells}</tr>"

    table_html = f"""
    <div style="overflow-x:auto;overflow-y:auto;max-height:600px;margin-top:8px">
    <table style="border-collapse:collapse;width:100%;font-size:0.85em;">
        <thead><tr style="position:sticky;top:0;background:#0e1117">{header}</tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption("Score Rally: premarket% + volumen premarket + cercanía 52w max + tendencia reciente · Actualiza cada 5min")


# ── Render ───────────────────────────────────────────────────────────────────

with st.spinner("Escaneando..."):
    df = scan()

if df.empty:
    st.error("Sin datos.")
else:
    # ── Alerta sonora ─────────────────────────────────────────────────────────
    alertas = df[df["Día %"] >= min_change]
    if not alertas.empty and (market_open or premarket):
        st.markdown("""
            <script>
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.3, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
            osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.5);
            </script>
        """, unsafe_allow_html=True)

    # ── Cards ganadoras del día ───────────────────────────────────────────────
    if not alertas.empty:
        st.subheader(f"🚀 Subiendo ≥ {min_change}% hoy")
        for i, row in alertas.head(4).iterrows():
            noticia = get_news(row["_ticker"])
            df.loc[i, "Noticia"] = noticia[:60] + "..." if len(noticia) > 60 else noticia
        alertas = df[df["Día %"] >= min_change]
        cols = st.columns(min(len(alertas), 4))
        for idx, (_, row) in enumerate(alertas.head(4).iterrows()):
            with cols[idx]:
                color = "#1a5c1a" if row["Día %"] >= 0 else "#5c1a1a"
                st.markdown(f"""
                    <div style="background:{color};padding:14px;border-radius:10px;text-align:center;margin-bottom:8px">
                        <div style="color:white;font-size:1.2em;font-weight:bold">{row['Ticker']}</div>
                        <div style="color:#7dff7d;font-size:1.5em;font-weight:bold">+{row['Día %']:.2f}%</div>
                        <div style="color:#ccc;font-size:0.75em">${row['Precio']} · Vol {row['Vol ratio']}x</div>
                        <div style="color:#aaa;font-size:0.7em;margin-top:4px">{row['Noticia']}</div>
                    </div>
                """, unsafe_allow_html=True)
        st.divider()

    # ── Cards premarket (horario premarket) ───────────────────────────────────
    df["_pre_pct"] = pd.to_numeric(df.get("_pre_pct"), errors="coerce")
    if premarket:
        infos = get_info_batch(WATCHLIST[:10])
        for i, row in df.iterrows():
            t = row["_ticker"]
            info = infos.get(t, {})
            pre = info.get("preMarketPrice")
            close = info.get("regularMarketPreviousClose")
            if pre and close and pre > 0:
                pct = round((pre - close) / close * 100, 2)
                df.loc[i, "_pre_pct"] = pct
                df.loc[i, "Premarket %"] = f"{pct:+.2f}%"

        pre_movers = df[df["_pre_pct"].notna() & (pd.to_numeric(df["_pre_pct"], errors="coerce").abs() >= 1)]
        if not pre_movers.empty:
            st.subheader("⏰ Movimiento premarket")
            cols2 = st.columns(min(len(pre_movers), 4))
            for idx, (_, row) in enumerate(pre_movers.head(4).iterrows()):
                pct = row["_pre_pct"]
                color = "#1a5c1a" if pct >= 0 else "#5c1a1a"
                sign = "+" if pct >= 0 else ""
                with cols2[idx]:
                    st.markdown(f"""
                        <div style="background:{color};padding:14px;border-radius:10px;text-align:center">
                            <div style="color:white;font-size:1.2em;font-weight:bold">{row['Ticker']}</div>
                            <div style="color:#7dff7d;font-size:1.5em;font-weight:bold">{sign}{pct:.2f}%</div>
                            <div style="color:#ccc;font-size:0.8em">Premarket</div>
                        </div>
                    """, unsafe_allow_html=True)
            st.divider()

    # ── TABLA PREMARKET ───────────────────────────────────────────────────────
    render_premarket_table()

    st.divider()

    # ── Tabla completa posiciones ─────────────────────────────────────────────
    st.subheader("Todas las posiciones")

    display = df.drop(columns=["_ticker", "_pre_pct", "_dist52"], errors="ignore").copy()
    cols_order = ["Ticker", "Precio", "Día %", "Desde open %", "Premarket %", "vs 52w max", "Vol ratio", "Noticia"]
    cols_order = [c for c in cols_order if c in display.columns]

    header = "".join(
        f'<th style="padding:6px 10px;text-align:left;border-bottom:1px solid #333;color:#aaa">{c}</th>'
        for c in cols_order
    )

    rows_html = ""
    for _, row in display.iterrows():
        cells = ""
        for col in cols_order:
            val = row[col]
            style = "padding:6px 10px;white-space:nowrap;"
            display_val = val

            if col == "Día %":
                bg, fg = cell_color(val)
                display_val = fmt_pct(val)
                if bg:
                    style += f"background:{bg};color:{fg};font-weight:bold;border-radius:4px;"
            elif col == "Desde open %":
                bg, fg = cell_color(val)
                display_val = fmt_pct(val)
                if bg:
                    style += f"background:{bg};color:{fg};border-radius:4px;"
            elif col == "Premarket %":
                if val != "-" and val:
                    try:
                        n = float(str(val).replace("%","").replace("+",""))
                        bg = "#0a7a0a" if n >= 2 else "#1a9a1a" if n > 0 else "#6b0a0a" if n <= -2 else "#8a1a1a"
                        style += f"background:{bg};color:white;border-radius:4px;"
                    except: pass
                display_val = val
            elif col == "vs 52w max":
                if val != "-" and val:
                    try:
                        n = float(str(val).replace("%",""))
                        if n >= -3:   style += "background:#0a7a0a;color:white;font-weight:bold;border-radius:4px;"
                        elif n >= -10:style += "color:#2db82d;"
                        elif n <= -30:style += "color:#ff6666;"
                        else:         style += "color:#ff9999;"
                    except: pass
                display_val = val
            elif col == "Precio":
                try: display_val = f"{float(val):.2f}"
                except: pass
            elif col == "Vol ratio":
                try: display_val = f"{float(val):.2f}x"
                except: pass

            cells += f'<td style="{style}">{display_val}</td>'
        rows_html += f"<tr>{cells}</tr>"

    table_html = f"""
    <div style="overflow-x:auto;overflow-y:auto;max-height:700px;">
    <table style="border-collapse:collapse;width:100%;font-size:0.85em;">
        <thead><tr style="position:sticky;top:0;background:#0e1117">{header}</tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption("Verde oscuro en '52w max' = cerca de máximo anual · 'Desde open' = movimiento desde apertura de hoy · Auto-refresh cada 5min si activado")
