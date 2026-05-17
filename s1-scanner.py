
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

