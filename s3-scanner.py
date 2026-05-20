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
HISTORY_RETENTION_SECONDS = 604800  # 7 days
# HISTORY_RETENTION_SECONDS = 2592000 # 30 days

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

    cursor.execute(
        """
        DELETE FROM oi_history
        WHERE timestamp < strftime('%s','now') - ?
        """,
        (HISTORY_RETENTION_SECONDS,),
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
    # return str(num)
    return f"{num:,.0f}"


# =========================
# Classifier v1
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
# Classifier v2
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
# score multi-factor + quant system básico
# =========================
# def compute_short_score(rsi, funding, oi, oi_delta, rvol, volume_24h):

#     score = 0

#     # RSI
#     if rsi >= 75:
#         score += 3
#     elif rsi >= 70:
#         score += 2
#     elif rsi >= 65:
#         score += 1

#     # Funding (crowded longs = bearish)
#     if funding > 0.01:
#         score += 2
#     elif funding > 0:
#         score += 1
#     elif funding < -0.02:
#         score -= 2   # squeeze risk (peligro)

#     # Open Interest (liquidez)
#     if oi > 10_000_000:
#         score += 1

#     # OI Delta (flujo)
#     if oi_delta > 1:
#         score += 2
#     elif oi_delta < -1:
#         score += 1

#     # RVOL (debilidad o exceso)
#     if rvol < 0.5:
#         score += 2   # debilidad → bueno para short
#     elif rvol > 1.5:
#         score -= 1   # momentum fuerte contra short

#     # volumen (confirmación de interés)
#     if volume_24h > 1_000_000:
#         score += 1

#     return score


# ========================================
# Score multi-factor + quant system básico
# ========================================
# def compute_short_score(rsi, funding, oi, oi_delta, rvol, volume_24h):
def compute_short_score(rsi, funding, oi, oi_delta, rvol, volume_24h, bearish_cvd_div):

    # =====================
    # 1. LIQUIDITY GATE (FILTRO DURO)
    # =====================

    if volume_24h < 500_000:
        return -5  # basura / no tradear

    score = 0

    # =====================
    # 2. RISK (estructura de liquidez) (OI / VOL)
    # =====================

    oi_vol_ratio = oi / volume_24h

    if oi_vol_ratio > 5:
        score -= 2  # high leverage / low liquidity
    elif oi_vol_ratio > 2:
        score -= 1  # speculative

    # =====================
    # 3. BIAS (lo más importante)
    # =====================

    # RSI EXTREMO
    if rsi >= 75:
        score += 4
    elif rsi >= 70:
        score += 3
    elif rsi >= 65:
        score += 1
    else:
        score -= 1  # no hay sobrecompra real

    # FUNDING
    if funding > 0.03 and rsi >= 70:
        score += 3  # euforia extrema
    elif funding > 0.01:
        score += 2
    elif funding > 0:
        score += 1
    elif funding < -0.02:
        score -= 2  # riesgo de short squeeze

    # =====================
    # 4. CONFIRMACIÓN
    # =====================

    # OI DELTA (crowding) SOLO si hay dirección
    if oi_delta > 1 and rsi >= 70:
        score += 2
    elif oi_delta > 1 and rsi < 60:
        score += 0  # neutro (no señal)

    # RVOL BAJO = agotamiento
    if rvol < 0.5 and rsi >= 70:
        score += 2  # debilidad real en sobrecompra
    elif rvol < 0.5:
        score += 0.5  # leve ajuste, no fuerte

    # RVOL MUY ALTO = momentum continuation
    elif rvol > 1.5:
        score -= 1

    # OI DELTA (crowding)
    # if oi_delta > 5 and rsi >= 70:
    #     score += 3
    # elif oi_delta > 2 and rsi >= 70:
    #     score += 2

    # # RVOL BAJO = agotamiento
    # if rvol < 0.6 and rsi >= 70:
    #     score += 2
    # elif rvol < 0.8:
    #     score += 1

    # # RVOL ALTO = continuación
    # elif rvol > 1.8:
    #     score -= 2
    # elif rvol > 1.5:
    #     score -= 1

    # =====================
    # 5 MOMENTUM EXPANSION
    # =====================

    # Expansión "sana" -> crowding
    if oi_delta > 5 and 0.6 <= rvol <= 1.2 and rsi >= 60:
        score += 1.5

    # Expansión agresiva -> peligro short
    if oi_delta > 5 and rvol > 1.5:
        score -= 2

    # =====================
    # 6. CONTEXTO
    # =====================

    # Mucho OI en sobrecompra
    if oi > 10_000_000 and rsi >= 70:
        score += 1

    # Liquidez suficiente
    if volume_24h > 1_000_000:
        score += 0.5

    # =====================
    # 7. CVD DIVERGENCE
    # =====================

    if bearish_cvd_div and rsi >= 70:
        score += 2

    return score


# def classify_from_score(score):

#     if score >= 7:
#         return "🔥 STRONG SHORT"

#     if 4 <= score < 7:
#         return "🎯 SHORT SETUP" #--🧠

#     if 2 <= score < 4:
#         return "⚠️ WEAK EDGE"

#     return "❌ NO TRADE"


# def classify_from_score(score, rsi, funding):

#     # STRONG SHORT (ahora con confluencia real)
#     if score >= 7 and rsi >= 72 and funding > 0:
#         return "🔥"

#     if 4 <= score < 7:
#         return "🎯"

#     if 2 <= score < 4:
#         return "⚠️"

#     return "❌"


def classify_from_score(score, rsi, funding, rvol, oi_delta):

    # 🔥 STRONG SHORT (confluencia real)
    # if score >= 7 and rsi >= 72 and funding > 0 and rvol < 0.8 and oi_delta > 0 :
    if score >= 7 and rsi >= 72 and funding > 0 and rvol < 0.8:
        return "🟢"

    # ⚠️ SHORT SETUP
    if score >= 7:
        return "🟡"

    # ⚠️ SHORT SETUP
    if 4 <= score < 7:
        return "🟡"

    # ⚠️ WEAK EDGE
    if 2 <= score < 4:
        return "🟡"

    return "🔴"


def get_risk_label(oi, volume_24h):

    if volume_24h == 0:
        return "⚫"  # NO LIQUIDITY

    oi_vol_ratio = oi / volume_24h

    if oi_vol_ratio > 5:
        return "🔴"  # HIGH LEVERAGE
    elif oi_vol_ratio > 2:
        return "🟡"  # SPECULATIVE
    else:
        return "🟢"  # HEALTHY


def get_funding_label(funding):
    if funding > 0:
        return "🟢"
    else:
        return "🔴"


def get_cvd_label(cvd_div):
    if cvd_div:
        return "🟢"
    return "🔴"


# =========================
# Pseudo CVD
# =========================
def calculate_pseudo_cvd(df):
    volume = df["v"].astype(float)

    delta = []

    for i in range(len(df)):

        open_price = float(df.iloc[i]["o"])
        close_price = float(df.iloc[i]["c"])

        if close_price > open_price:
            delta.append(volume.iloc[i])

        elif close_price < open_price:
            delta.append(-volume.iloc[i])

        else:
            delta.append(0)

    df["delta"] = delta
    df["cvd"] = df["delta"].cumsum()

    return df


# =========================
# Bearish CVD Divergence
# =========================
def detect_bearish_cvd_divergence(df, lookback=10):

    if len(df) < lookback * 2:
        return False

    # últimos highs
    recent_price_high = df["close"].iloc[-lookback:].max()
    previous_price_high = df["close"].iloc[-lookback * 2 : -lookback].max()

    # últimos highs CVD
    recent_cvd_high = df["cvd"].iloc[-lookback:].max()
    previous_cvd_high = df["cvd"].iloc[-lookback * 2 : -lookback].max()

    # divergencia
    price_higher_high = recent_price_high > previous_price_high
    cvd_lower_high = recent_cvd_high < previous_cvd_high

    return price_higher_high and cvd_lower_high


# def detect_cvd_signal(df, lookback=10):

#     recent_price_high = df["close"].iloc[-lookback:].max()
#     previous_price_high = df["close"].iloc[-lookback*2:-lookback].max()

#     recent_cvd_high = df["cvd"].iloc[-lookback:].max()
#     previous_cvd_high = df["cvd"].iloc[-lookback*2:-lookback].max()

#     # bearish divergence
#     if (
#         recent_price_high > previous_price_high
#         and recent_cvd_high < previous_cvd_high
#     ):
#         return "🔻"

#     # bullish confirmation
#     if (
#         recent_price_high > previous_price_high
#         and recent_cvd_high > previous_cvd_high
#     ):
#         return "🟢"

#     return "➖"


# =========================
# Scanner principal
# =========================
def run_scanner():

    cleanup_old_data()

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

            df = calculate_pseudo_cvd(df)

            bearish_cvd_div = detect_bearish_cvd_divergence(df)

            funding = market_data.get(symbol, {}).get("funding", 0) * 100

            oi = market_data.get(symbol, {}).get("open_interest", 0)

            oi_delta = get_oi_delta(symbol, oi)

            save_oi_snapshot(symbol, oi)

            volume_24h = market_data.get(symbol, {}).get("volume_24h", 0)

            score = compute_short_score(
                rsi, funding, oi, oi_delta, rv, volume_24h, bearish_cvd_div
            )

            signal = classify_from_score(score, rsi, funding, rv, oi_delta)

            risk_label = get_risk_label(oi, volume_24h)

            # oi > 10_000_000 and volume_24h > 1_000_000 and rv > 0.8
            if rsi > RSI_THRESHOLD and oi > 0:
                results.append(
                    {
                        "symbol": symbol,
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "oi": oi,
                        "volume_24h": volume_24h,
                        "rv": round(rv, 2),
                        "oi_delta": round(oi_delta, 2),
                        "score": score,
                        "signal": signal,
                        "risk_label": risk_label,
                        "cvd_div": bearish_cvd_div,
                    }
                )

        except KeyError as e:
            print(f"Datos faltantes para {symbol}: {e}")
        except Exception as e:
            print(f"Error en {symbol}: {e}")

        time.sleep(REQUEST_DELAY)

    results = sorted(results, key=lambda x: x["rsi"], reverse=True)

    print("=" * 122)

    if not results:
        print(f"\nNo hay activos con RSI > {RSI_THRESHOLD}")
    else:
        for item in results:
            print(
                f"{item['symbol']:<6} "
                f"RSI: {item['rsi']:>5.2f}  "
                f"Fund({get_funding_label(item['funding'])}): {item['funding']:>7.4f}  "
                f"CVD({get_cvd_label(item['cvd_div'])}) OI: ${format_number(item['oi']):>7}  "
                f"OIΔ: {item['oi_delta']:>5.2f}%  "
                f"RVOL: {item['rv']:>4.2f}x  "
                f"Vol24h({item['risk_label']}): ${format_number(item['volume_24h']):>7}  "
                f"SCO({item['signal']}): {item['score']:>4.1f}"
            )

    print("=" * 122)


if __name__ == "__main__":
    run_scanner()

    # while True:
    #     run_scanner()
    #     time.sleep(3600)
