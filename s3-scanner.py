import requests
import pandas as pd
from ta.momentum import RSIIndicator
import time
import argparse
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument("--rsi", type=float, default=70)
args = parser.parse_args()
RSI_THRESHOLD = args.rsi

BASE_URL = "https://api.hyperliquid.xyz/info"
RSI_PERIOD = 14
VOL_WINDOW = 20
REQUEST_DELAY = 0.25
HISTORY_RETENTION_SECONDS = 604800 # 7 days
#HISTORY_RETENTION_SECONDS = 2592000 # 30 days


DB_NAME = "scanner.db"
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS oi_history (
    symbol TEXT,
    timestamp INTEGER,
    oi REAL
)
""")
conn.commit()

# =========================
# Guardar snapshot OI
# =========================
def save_oi_snapshot(symbol, oi):

    cursor.execute(
        """
        INSERT INTO oi_history (symbol, timestamp, oi)
        VALUES (?, strftime('%s','now'), ?)
        """,
        (symbol, oi),
    )

    conn.commit()

# =========================
# Calcular OI Delta
# =========================
def get_oi_delta(symbol, current_oi):

    # cursor.execute(
    #     """
    #     SELECT oi
    #     FROM oi_history
    #     WHERE symbol = ?
    #     AND timestamp <= strftime('%s','now') - 3600
    #     ORDER BY timestamp DESC
    #     LIMIT 1
    #     """,
    #     (symbol,),
    # )

    cursor.execute(
        """
        SELECT oi
        FROM oi_history
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT 1 OFFSET 1
        """,
        (symbol,),
    )

    row = cursor.fetchone()

    if not row:
        return 0

    previous_oi = row[0]

    if previous_oi == 0:
        return 0

    return ((current_oi - previous_oi) / previous_oi) * 100


# =========================
# Limpiar histórico viejo
# =========================
def cleanup_old_data():

    # cursor.execute(
    #     """
    #     DELETE FROM oi_history
    #     WHERE timestamp < strftime('%s','now') - {HISTORY_RETENTION_SECONDS}
    #     """
    # )

    cursor.execute(
        """
        DELETE FROM oi_history
        WHERE timestamp < strftime('%s','now') - ?
        """,
        (HISTORY_RETENTION_SECONDS,)
    )

    conn.commit()


# =========================
# Obtener mercados
# =========================
def get_markets():
    payload = {"type": "meta"}

    r = requests.post(BASE_URL, json=payload)
    data = r.json()

    if "universe" not in data:
        print("Error: respuesta inválida del API, falta 'universe'")
        return []

    return [asset["name"] for asset in data["universe"]]


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
def calculate_rsi(df, period=RSI_PERIOD):
    rsi = RSIIndicator(close=df["close"], window=period)
    return rsi.rsi().iloc[-1]


# =========================
# Obtener funding y Open Interest
# =========================
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
        volume_24h = float(ctx.get("dayNtlVlm", 0))
        open_interest = float(ctx.get("openInterest", 0))
        price = float(ctx.get("markPx", 0))
        oi_usd = open_interest * price

        market_data[symbol] = {
            "funding": funding,
            "open_interest": oi_usd,
            "volume_24h": volume_24h,
        }

    return market_data


# =========================
# Calcular volumen relativo
# =========================
def calculate_relative_volume(df):
    volume = df["v"].astype(float)
    current_volume = volume.iloc[-1]
    average_volume = volume.rolling(VOL_WINDOW).mean().iloc[-1]

    if average_volume == 0:
        return 0

    return current_volume / average_volume


# =========================
# Obtiene K/M/B automático
# =========================
def format_number(num):
    if num >= 1_000_000_000:
        return f"{num/1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num/1_000:.1f}K"
    #return str(num)
    return f"{num:,.0f}"

# =========================
# un “classifier” 
# =========================
def classify_trade(rsi, funding, rv, oi_delta):
    # 🔥 SHORT fuerte
    if rsi >= 70 and funding > 0 and rv < 0.8:
        return "🔥 SHORT"

    # 🔥 SHORT por sobreextensión + debilidad
    if rsi >= 72 and rv < 1:
        return "🔥 SHORT"

    # ⚠️ zona peligrosa (no confirmación)
    if 65 <= rsi < 70:
        return "⚠️ WATCH"

    # ⚠️ funding muy negativo (posible squeeze primero)
    if funding < -0.02:
        return "⚠️ WATCH (p.squeeze)"

    # ❌ sin setup
    return "❌ NO TRADE"


# =========================
# un “classifier” 2
# =========================
def classify_trade2(rsi, funding, rv, oi_delta):
    
    # 🔴 SHORT SQUEEZE RISK (peligro para shorts)
    # hay demasiados shorts → el precio puede subir violentamente primero
    if rsi < 65 and funding < -0.02 and oi_delta > 1:
        return "⚠️ SHORT SQUEEZE RISK"

    # 🟢 LONG SQUEEZE RISK (peligro para longs)
    # mercado sobrecargado de longs → posible dump violento
    if rsi > 75 and funding > 0.01 and oi_delta > 1:
        return "⚠️ LONG SQUEEZE RISK"

    # 🔥 SHORT fuerte
    if rsi >= 70 and funding > 0 and rv < 0.8:
        return "🔥 SHORT"

    # 🔥 SHORT por sobreextensión
    if rsi >= 72 and rv < 1:
        return "🔥 SHORT (weak vol)"

    # ⚠️ zona neutral / transición
    if 65 <= rsi < 70:
        return "⚠️ WATCH"

    # ❌ sin edge
    return "❌ NO TRADE"


# =========================
# Scanner principal
# =========================
def run_scanner():

    cleanup_old_data() #---------------------

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

            oi_delta = get_oi_delta(symbol, oi) #------
            save_oi_snapshot(symbol, oi) #------


            volume_24h = market_data.get(symbol, {}).get("volume_24h", 0)

            signal = classify_trade(rsi, funding, rv, oi_delta) #--------------------

            #oi > 10_000_000 and volume_24h > 1_000_000 and rv > 0.8
            if rsi > RSI_THRESHOLD and oi > 0:
                results.append(
                    {
                        "symbol": symbol,
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "oi": oi,
                        "volume_24h": volume_24h,
                        "rv": round(rv, 2),
                        "oi_delta": round(oi_delta, 2), #------------
                        "signal": signal,   #------------
                    }
                )

        except KeyError as e:
            print(f"Datos faltantes para {symbol}: {e}")
        except Exception as e:
            print(f"Error en {symbol}: {e}")

        time.sleep(REQUEST_DELAY)

    results = sorted(results, key=lambda x: x["rsi"], reverse=True)

    print("=" * 115)

    if not results:
        print(f"\nNo hay activos con RSI > {RSI_THRESHOLD}")
    else:
        for item in results:
            print(
                f"{item['symbol']:<7} "
                f"RSI: {item['rsi']:>6.2f}  "
                f"Funding: {item['funding']:>8.4f}  "
                f"OI: ${format_number(item['oi']):>8}  "
                f"OIΔ: {item['oi_delta']:>6.2f}%  "
                f"RVOL: {item['rv']:>5.2f}x  "
                f"Vol24h: ${format_number(item['volume_24h']):>8}  "
                f"{item['signal']}"
            )

    print("=" * 115)


if __name__ == "__main__":
    run_scanner()

    # while True:
    #     run_scanner()
    #     time.sleep(3600)
