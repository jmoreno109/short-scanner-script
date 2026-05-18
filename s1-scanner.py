
#Setup Short
#RSI_3D > 70
#AND funding > 0.05
#AND OI_change_24h > 10%
#AND volume > average

# Caso más favorable Setup Short 2
# RSI alto
# RVOL debajo de 1
# funding positivo
# precio extendido
# OI alto

# < 5M USD | Muy bajo | Generalmente basura.
# 5M - 20M  | Bajo | Altcoins pequeñas.
# 20M - 100M | Decente | Ya operable.
# 100M - 500M | Bueno | Empieza a ser interesante.
# 500M - 1B+ | Muy fuerte | Mercado muy activo.

# CASO 1
# Precio ↑ + OI ↑
# Muy bullish.
# Significa:
# entran nuevos longs,
# expansión de posiciones,
# tendencia fuerte.

# CASO 2
# Precio ↑ + OI ↓
# Short covering.
# Subida menos sostenible.

# CASO 3
# Precio ↓ + OI ↑
# Nuevos shorts entrando.
# Bearish/agresivo.

# CASO 4
# Precio ↓ + OI ↓
# Capitulación/flush.


# 🔴 Posible SHORT SQUEEZE (para ti importante)
# Esto es lo que te interesa si haces shorts:
# Funding MUY negativo ❄️
# 👉 demasiados shorts abiertos
# OI subiendo fuerte 📈
# 👉 mucha gente entrando en posiciones
# Precio no cae (o sube lento)
# 👉 los shorts están “equivocados”
# RVOL empieza a subir
# 👉 Esto significa:
# “Hay muchos shorts atrapados → riesgo de subida violenta”


# ✔ funciona bien para, estilos de trading
# scalping reversal
# fade trades
# overextended moves



# | Señal                             | Interpretación                       | Score |
# | --------------------------------- | ------------------------------------ | ----- |
# | RSI ≥ 75                          | sobrecompra extrema                  | +4    |
# | RSI ≥ 70                          | sobrecompra fuerte                   | +3    |
# | RSI ≥ 65                          | sobreextensión inicial               | +1    |
# | Funding > 0                       | crowded longs                        | +1    |
# | Funding > 0.01                    | euforia long                         | +2    |
# | Funding > 0.03 + RSI ≥ 70         | euforia extrema                      | +3    |
# | OIΔ > 1 + RSI ≥ 70                | entrada de posiciones en sobrecompra | +2    |
# | OIΔ > 5 + RSI ≥ 70                | crowding agresivo                    | +3    |
# | RVOL < 0.5 + RSI ≥ 70             | agotamiento / volumen secándose      | +2    |
# | RVOL < 0.5                        | debilidad leve                       | +0.5  |
# | OIΔ > 5 + RVOL 0.6–1.2 + RSI ≥ 60 | expansión moderada (crowding)        | +1.5  |
# | OI > 10M + RSI ≥ 70               | mercado pesado / muy posicionado     | +1    |
# | Vol24h > 1M                       | liquidez aceptable                   | +0.5  |

# | Señal                | Interpretación                          | Score     |
# | -------------------- | --------------------------------------- | --------- |
# | Vol24h < 500K        | basura / ilíquido                       | return -5 |
# | OI/Vol > 5           | apalancamiento excesivo / poca liquidez | -2        |
# | OI/Vol > 2           | mercado especulativo                    | -1        |
# | RSI < 65             | no hay sobrecompra clara                | -1        |
# | Funding < -0.02      | riesgo de short squeeze                 | -2        |
# | RVOL > 1.5           | momentum fuerte                         | -2        |
# | OIΔ > 5 + RVOL > 1.5 | expansión agresiva / continuation       | -2        |


import requests
import pandas as pd
from ta.momentum import RSIIndicator
import time

BASE_URL = "https://api.hyperliquid.xyz/info"


# =========================
# Obtener mercados
# =========================
def get_markets():
    payload = {
        "type": "meta"
    }

    r = requests.post(BASE_URL, json=payload)
    data = r.json()

    markets = []

    for asset in data["universe"]:
        markets.append(asset["name"])

    return markets


# =========================
# Obtener candles
# =========================
def get_candles(symbol, interval="3d", limit=200):

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol,
            "interval": interval,
            "startTime": 0
        }
    }

    r = requests.post(BASE_URL, json=payload)

    if r.status_code != 200:
        return None

    data = r.json()

    if not data:
        return None

    df = pd.DataFrame(data)

    df["close"] = df["c"].astype(float)

    return df


# =========================
# Calcular RSI
# =========================
def calculate_rsi(df, period=14):

    rsi = RSIIndicator(close=df["close"], window=period)

    return rsi.rsi().iloc[-1]


# =========================
# Scanner principal
# =========================
def run_scanner():

    markets = get_markets()

    results = []

    print("\nBuscando activos con RSI(14) > 70 en 3D...\n")

    for symbol in markets:

        try:
            df = get_candles(symbol)

            if df is None:
                continue

            if len(df) < 20:
                continue

            rsi = calculate_rsi(df)

            if rsi < 40: # ---------------------------------------------------
                results.append((symbol, round(rsi, 2)))

        except Exception as e:
            print(f"Error en {symbol}: {e}")

    results = sorted(results, key=lambda x: x[1], reverse=True)

    print("=" * 50)

    if not results:
        print("No hay activos con RSI > 70")
    else:
        for symbol, rsi in results:
            print(f"{symbol:<10} RSI: {rsi}")

    print("=" * 50)


# =========================
# Ejecutar una vez
# =========================
run_scanner()


# =========================
# Ejecutar cada 1 hora
# =========================
# while True:
#     run_scanner()
#     time.sleep(3600)



def compute_short_score(rsi, funding, oi, oi_delta, rvol, volume_24h):

    # =====================
    # 0. LIQUIDITY GATE (FILTRO DURO)
    # =====================

    if volume_24h < 500_000:
        return -5  # basura / no tradear

    score = 0

    # =====================
    # 0. RISK (estructura de liquidez) (OI / VOL)
    # =====================

    oi_vol_ratio = oi / volume_24h

    if oi_vol_ratio > 5:
        score -= 2   # HIGH LEVERAGE / LOW LIQUIDITY
    elif oi_vol_ratio > 2:
        score -= 1   # SPECULATIVE

    # =====================
    # 1. BIAS (lo más importante)
    # =====================

    # RSI EXTREMO
    if rsi >= 75:
        score += 4
    elif rsi >= 70:
        score += 3
    elif rsi >= 65:
        score += 1
    else:
        score -= 1   # no hay sobrecompra real

    # FUNDING
    if funding > 0.03 and rsi >= 70:
        score += 3   # euforia extrema
    elif funding > 0.01:
        score += 2
    elif funding > 0:
        score += 1
    elif funding < -0.02:
        score -= 2   # riesgo de short squeeze

    # =====================
    # 2. CONFIRMACIÓN
    # =====================

    # OI DELTA (crowding)
    if oi_delta > 5 and rsi >= 70:
        score += 3
    elif oi_delta > 1 and rsi >= 70:
        score += 2

    # RVOL BAJO = agotamiento
    if rvol < 0.5 and rsi >= 70:
        score += 2
    elif rvol < 0.5:
        score += 0.5

    # RVOL MUY ALTO = momentum continuation
    elif rvol > 1.5:
        score -= 2

    # =====================
    # 2.5 MOMENTUM EXPANSION
    # =====================

    # expansión "sana" -> crowding
    if oi_delta > 5 and 0.6 <= rvol <= 1.2 and rsi >= 60:
        score += 1.5

    # expansión agresiva -> peligro short
    if oi_delta > 5 and rvol > 1.5:
        score -= 2

    # =====================
    # 3. CONTEXTO
    # =====================

    # mucho OI en sobrecompra
    if oi > 10_000_000 and rsi >= 70:
        score += 1

    # liquidez suficiente
    if volume_24h > 1_000_000:
        score += 0.5

    # =====================
    # 4. CLAMP FINAL
    # =====================

    score = round(score, 1)

    return score