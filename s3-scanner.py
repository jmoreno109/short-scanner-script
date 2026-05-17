import requests
import pandas as pd
from ta.momentum import RSIIndicator
import time

BASE_URL = "https://api.hyperliquid.xyz/info"

RSI_PERIOD = 14
VOL_WINDOW = 20
RSI_THRESHOLD = 65
REQUEST_DELAY = 0.25


def get_markets():
    payload = {"type": "meta"}

    r = requests.post(BASE_URL, json=payload)
    data = r.json()

    if "universe" not in data:
        print("Error: respuesta inválida del API, falta 'universe'")
        return []

    return [asset["name"] for asset in data["universe"]]


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


def calculate_rsi(df, period=RSI_PERIOD):
    rsi = RSIIndicator(close=df["close"], window=period)
    return rsi.rsi().iloc[-1]


def get_market_data():
    payload = {"type": "metaAndAssetCtxs"}

    r = requests.post(BASE_URL, json=payload)
    data = r.json()

    if len(data) < 2 or "universe" not in data[0]:
        print("Error: respuesta inválida del API para datos de mercado")
        return {}

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


def calculate_relative_volume(df):
    volume = df["v"].astype(float)
    current_volume = volume.iloc[-1]
    average_volume = volume.rolling(VOL_WINDOW).mean().iloc[-1]

    if average_volume == 0:
        return 0

    return current_volume / average_volume


def run_scanner():
    markets = get_markets()
    market_data = get_market_data()
    results = []

    print(f"\nBuscando activos con RSI({RSI_PERIOD}) > {RSI_THRESHOLD} en 3D...\n")

    for symbol in markets:
        try:
            df = get_candles(symbol)

            if df is None:
                continue

            if len(df) < VOL_WINDOW:
                continue

            rsi = calculate_rsi(df)
            rv = calculate_relative_volume(df)
            funding = market_data.get(symbol, {}).get("funding", 0) * 100
            oi = market_data.get(symbol, {}).get("open_interest", 0)
            volume_24h = market_data.get(symbol, {}).get("volume_24h", 0)

            if rsi > RSI_THRESHOLD and oi > 0:
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

        except KeyError as e:
            print(f"Datos faltantes para {symbol}: {e}")
        except Exception as e:
            print(f"Error en {symbol}: {e}")

        time.sleep(REQUEST_DELAY)

    results = sorted(results, key=lambda x: x["rsi"], reverse=True)

    print("=" * 100)

    if not results:
        print(f"\nNo hay activos con RSI > {RSI_THRESHOLD}")
    else:
        for item in results:
            print(
                f"{item['symbol']:<10} "
                f"RSI: {item['rsi']:<6}   "
                f"Funding: {item['funding']:<8.4f}   "
                f"OI: {item['oi']:<15,.0f} "
                f"RVOL: {item['rv']}x     "
                f"Vol24h: ${item['volume_24h']:,.0f}"
            )

    print("=" * 100)


if __name__ == "__main__":
    run_scanner()

    # while True:
    #     run_scanner()
    #     time.sleep(3600)
