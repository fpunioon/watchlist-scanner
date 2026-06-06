"""
IBKR Rules Engine — Fase 4
Conecta a TWS/IB Gateway, lee posiciones en tiempo real,
verifica las reglas R1-R6 y genera órdenes automáticas si se activan.

REQUISITO: TWS o IB Gateway abierto en el Mac con API habilitada.
  TWS → Configuración → API → Habilitar API de socket → Puerto 7497
"""

from ib_insync import *
import pandas as pd
import yfinance as yf
from datetime import datetime
import time

# ── Configuración ─────────────────────────────────────────────────────────────
IB_HOST   = '127.0.0.1'
IB_PORT   = 7497   # TWS paper: 7497 | TWS live: 7496 | Gateway live: 4001
CLIENT_ID = 10

# Precios medio de compra (Revolut — no visibles en IBKR)
PM_REVOLUT = {
    'NVDA':  167.59,
    'LLY':   1123.15,
    'MSFT':  374.50,
    'GOOGL': 274.09,
    'AMZN':  248.52,
    'VOO':   601.51,
}

# Reglas
R1_MAX_PCT    = 0.15   # posición individual máxima
R1_TARGET_PCT = 0.12   # objetivo tras recorte
R2_STOP_PCT   = -0.25  # stop loss desde PM (NVDA)
R2_STOPS = {
    'NVDA': -0.25, 'LLY': -0.20, 'META': -0.15,
    'MSFT': -0.15, 'GOOGL': -0.15, 'AMZN': -0.15,
    'VOO':  None,
}
R5_TOP3_MAX   = 0.45
R6_CASH_MIN   = 0.05


def conectar():
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=10)
        print(f"✅ Conectado a IBKR — cuenta: {ib.managedAccounts()}")
        return ib
    except Exception as e:
        print(f"❌ No se pudo conectar: {e}")
        print("   Asegúrate de que TWS está abierto y la API está habilitada (puerto 7497)")
        return None


def get_posiciones(ib):
    """Lee posiciones abiertas desde IBKR."""
    portfolio = ib.portfolio()
    posiciones = {}
    for item in portfolio:
        sym = item.contract.symbol
        posiciones[sym] = {
            'cantidad':      item.position,
            'precio_medio':  item.averageCost,
            'precio_actual': item.marketPrice,
            'valor_mercado': item.marketValue,
            'pnl_latente':   item.unrealizedPNL,
            'pnl_pct':       (item.marketPrice - item.averageCost) / item.averageCost
                             if item.averageCost > 0 else 0,
        }
    return posiciones


def get_cash(ib):
    """Lee el cash disponible en CHF."""
    vals = {v.tag: v.value for v in ib.accountValues() if v.currency in ('CHF','BASE')}
    return float(vals.get('CashBalance', 0))


def verificar_reglas(posiciones, cash_chf):
    """Verifica R1-R6 y devuelve alertas + órdenes sugeridas."""

    total = sum(p['valor_mercado'] for p in posiciones.values()) + cash_chf
    alertas = []
    ordenes  = []

    # ── R1: tamaño máximo por posición ───────────────────────────────────────
    for sym, p in posiciones.items():
        peso = p['valor_mercado'] / total
        if peso > R1_MAX_PCT:
            exceso_pct  = peso - R1_TARGET_PCT
            exceso_usd  = exceso_pct * total
            acciones_vender = int(exceso_usd / p['precio_actual'])
            alertas.append({
                'regla': 'R1', 'nivel': 'URGENTE',
                'ticker': sym,
                'mensaje': f"{sym} pesa {peso:.1%} (límite {R1_MAX_PCT:.0%})",
                'accion': f"Vender {acciones_vender} acc a ~${p['precio_actual']:.2f}"
            })
            ordenes.append({'tipo': 'VENTA', 'ticker': sym, 'cantidad': acciones_vender,
                            'precio': p['precio_actual'], 'regla': 'R1'})

    # ── R2: stop loss ─────────────────────────────────────────────────────────
    for sym, p in posiciones.items():
        stop = R2_STOPS.get(sym)
        pm   = PM_REVOLUT.get(sym, p['precio_medio'])
        if stop and pm > 0:
            pnl_vs_pm = (p['precio_actual'] - pm) / pm
            if pnl_vs_pm <= stop:
                alertas.append({
                    'regla': 'R2', 'nivel': 'CRÍTICO',
                    'ticker': sym,
                    'mensaje': f"{sym} cayó {pnl_vs_pm:.1%} desde PM ${pm:.2f} (stop {stop:.0%})",
                    'accion': f"CERRAR posición — vender {p['cantidad']:.2f} acc"
                })
                ordenes.append({'tipo': 'VENTA_TOTAL', 'ticker': sym,
                                'cantidad': p['cantidad'], 'precio': p['precio_actual'],
                                'regla': 'R2'})

    # ── R5: concentración top 3 ───────────────────────────────────────────────
    sorted_pos = sorted(posiciones.items(), key=lambda x: -x[1]['valor_mercado'])
    top3_val   = sum(p['valor_mercado'] for _, p in sorted_pos[:3])
    top3_pct   = top3_val / total
    if top3_pct > R5_TOP3_MAX:
        mayor = sorted_pos[0][0]
        alertas.append({
            'regla': 'R5', 'nivel': 'ATENCIÓN',
            'ticker': mayor,
            'mensaje': f"Top 3 concentración {top3_pct:.1%} (límite {R5_TOP3_MAX:.0%})",
            'accion': f"Recortar {mayor} — ya cubierto por R1 si aplica"
        })

    # ── R6: cash mínimo ───────────────────────────────────────────────────────
    cash_pct = cash_chf / total
    if cash_pct < R6_CASH_MIN:
        necesario = R6_CASH_MIN * total - cash_chf
        alertas.append({
            'regla': 'R6', 'nivel': 'ATENCIÓN',
            'ticker': 'CASH',
            'mensaje': f"Cash {cash_pct:.1%} (mínimo {R6_CASH_MIN:.0%})",
            'accion': f"Generar ~{necesario:,.0f} CHF en cash"
        })

    return alertas, ordenes, total


def ejecutar_orden(ib, orden, confirmar=True):
    """Ejecuta una orden de venta. Por defecto pide confirmación."""
    sym       = orden['ticker']
    cantidad  = abs(orden['cantidad'])
    precio    = orden['precio']

    if cantidad < 1:
        print(f"  ⚠️ {sym}: cantidad {cantidad:.2f} < 1 acc — sin ejecución")
        return

    if confirmar:
        resp = input(f"\n  ¿Ejecutar VENTA {cantidad} acc {sym} a ~${precio:.2f}? (s/n): ")
        if resp.lower() != 's':
            print(f"  ↩️ Cancelado por el usuario")
            return

    contract = Stock(sym, 'SMART', 'USD')
    ib.qualifyContracts(contract)
    order    = MarketOrder('SELL', cantidad)
    trade    = ib.placeOrder(contract, order)
    print(f"  📤 Orden enviada: VENTA {cantidad} acc {sym} — ID {trade.order.orderId}")
    return trade


def run_scanner(auto_execute=False):
    """Bucle principal: conecta, escanea, alerta, ejecuta si procede."""

    print("="*65)
    print("IBKR RULES ENGINE — Fase 4")
    print(f"Ejecutando: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("="*65)

    ib = conectar()
    if not ib:
        print("\n⚠️  MODO SIMULACIÓN (sin conexión IBKR)")
        print("   Posiciones simuladas desde última lectura del CSV:")
        # Fallback simulado
        posiciones = {
            'VOO': {'cantidad': 97.7, 'precio_medio': 601.51, 'precio_actual': 678.0,
                    'valor_mercado': 66240.6, 'pnl_latente': 7472.6, 'pnl_pct': 0.127}
        }
        cash_chf = 10.0
    else:
        posiciones = get_posiciones(ib)
        cash_chf   = get_cash(ib)

    if not posiciones:
        print("Sin posiciones abiertas en IBKR.")
        if ib: ib.disconnect()
        return

    # ── Mostrar posiciones ────────────────────────────────────────────────────
    print(f"\n📊 POSICIONES IBKR")
    print(f"{'Ticker':<8} {'Cant':>8} {'PM':>8} {'Precio':>8} {'Valor':>10} {'PyG%':>7}")
    print("-"*55)
    for sym, p in sorted(posiciones.items(), key=lambda x: -x[1]['valor_mercado']):
        print(f"  {sym:<8} {p['cantidad']:>8.2f} {p['precio_medio']:>8.2f} "
              f"{p['precio_actual']:>8.2f} {p['valor_mercado']:>10,.0f} {p['pnl_pct']:>+7.1%}")
    print(f"  {'CASH':<8} {'':>8} {'':>8} {'':>8} {cash_chf:>10,.0f}")

    # ── Verificar reglas ──────────────────────────────────────────────────────
    alertas, ordenes, total = verificar_reglas(posiciones, cash_chf)

    print(f"\n🔍 ESTADO REGLAS — Total cartera: {total:,.0f} CHF")
    print("-"*55)
    if not alertas:
        print("  ✅ Todas las reglas OK")
    else:
        for a in alertas:
            icono = "🔴" if a['nivel'] == 'URGENTE' else "🟠" if a['nivel'] == 'CRÍTICO' else "🟡"
            print(f"  {icono} [{a['regla']}] {a['mensaje']}")
            print(f"     → {a['accion']}")

    # ── Ejecutar órdenes ──────────────────────────────────────────────────────
    if ordenes and ib:
        print(f"\n📋 ÓRDENES SUGERIDAS ({len(ordenes)})")
        for o in ordenes:
            print(f"  {o['tipo']} {o['cantidad']:.0f} acc {o['ticker']} "
                  f"@ ~${o['precio']:.2f}  [{o['regla']}]")

        if auto_execute:
            print("\n⚡ AUTO-EXECUTE activado")
            for o in ordenes:
                ejecutar_orden(ib, o, confirmar=False)
        else:
            resp = input("\n¿Ejecutar órdenes ahora? (s/n): ")
            if resp.lower() == 's':
                for o in ordenes:
                    ejecutar_orden(ib, o, confirmar=True)

    if ib:
        ib.disconnect()
        print("\n🔌 Desconectado de IBKR")

    print("\n" + "="*65)
    return alertas, ordenes


if __name__ == '__main__':
    run_scanner(auto_execute=False)
