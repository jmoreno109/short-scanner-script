import requests
import pandas as pd
from ta.momentum import RSIIndicator
import time

BASE_URL = "https://api.hyperliquid.xyz/info"


# =========================
# Obtener mercados
# =========================
def get_markets():
    payload = {"type": "meta"}

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
        "req": {"coin": symbol, "interval": interval, "startTime": 0},
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
# Obtener funding y Open Interest
# =========================
def get_market_data():

    payload = {"type": "metaAndAssetCtxs"}

    r = requests.post(BASE_URL, json=payload)
    data = r.json()

    universe = data[0]["universe"]
    contexts = data[1]

    market_data = {}

    for asset, ctx in zip(universe, contexts):

        symbol = asset["name"]

        funding = float(ctx.get("funding", 0))

        open_interest = float(ctx.get("openInterest", 0))

        volume_24h = float(ctx.get("dayNtlVlm", 0))

        market_data[symbol] = {
            "funding": funding,
            "open_interest": open_interest,
            "volume_24h": volume_24h,
        }

    return market_data


# =========================
# Calcular volumen relativo
# =========================
def calculate_relative_volume(df):

    df["volume"] = df["v"].astype(float)

    current_volume = df["volume"].iloc[-1]

    average_volume = df["volume"].rolling(20).mean().iloc[-1]

    if average_volume == 0:
        return 0

    return current_volume / average_volume


# =========================
# Scanner principal
# =========================
def run_scanner():

    markets = get_markets()

    market_data = get_market_data()

    results = []

    print("\nBuscando activos con RSI(14) > 65 en 3D...\n") #------------

    for symbol in markets:

        try:
            df = get_candles(symbol)

            if df is None:
                continue

            if len(df) < 20:
                continue

            rsi = calculate_rsi(df)

            rv = calculate_relative_volume(df)

            #funding = market_data[symbol]["funding"]
            funding = market_data[symbol]["funding"] * 100

            oi = market_data[symbol]["open_interest"]

            volume_24h = market_data[symbol]["volume_24h"]

            if rsi > 65 and oi > 0: #--------------------------------------------
                results.append(
                    {
                        "symbol": symbol,
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "oi": oi,
                        "volume_24h": volume_24h,
                        "rv": round(rv, 2),
                    }
                )

        except Exception as e:
            print(f"Error en {symbol}: {e}")

        results = sorted(results, key=lambda x: x["rsi"], reverse=True)

    print("=" * 100)

    if not results:
        print("No hay activos con RSI > 65") #----------------------
    else:
        for item in results:
            print(
                f"{item['symbol']:<10} "
                f"RSI: {item['rsi']:<6}   "
                #f"Funding: {item['funding']:<10.6f} "
                f"Funding: {item['funding']:<8.4f}   "
                f"OI: {item['oi']:<15,.0f} "
                f"RVOL: {item['rv']}x     "
                f"Vol24h: ${item['volume_24h']:,.0f}"
            )

    print("=" * 100)


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
