# ==========================================
# LONG SCANNER VERSION
# Inversión del scanner de SHORTS
# ==========================================
# Swing Exhaustion Scanner

import requests
import pandas as pd
from ta.momentum import RSIIndicator
import time
import argparse
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument("--rsi", type=float, default=30)
args = parser.parse_args()

RSI_THRESHOLD = args.rsi

BASE_URL = "https://api.hyperliquid.xyz/info"

RSI_PERIOD = 14
VOL_WINDOW = 20
REQUEST_DELAY = 0.25

HISTORY_RETENTION_SECONDS = 604800

DB_NAME = "scanner_long.db"

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
# SAVE OI SNAPSHOT
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
# OI DELTA
# =========================
def get_oi_delta(symbol, current_oi):

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
# CLEANUP
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
# MARKETS
# =========================
def get_markets():

    payload = {"type": "meta"}

    r = requests.post(BASE_URL, json=payload)
    data = r.json()

    return [asset["name"] for asset in data["universe"]]


# =========================
# CANDLES
# =========================
def get_candles(symbol, interval="3d"):

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol,
            "interval": interval,
            "startTime": 0,
        },
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
# RSI
# =========================
def calculate_rsi(df, period=RSI_PERIOD):

    rsi = RSIIndicator(close=df["close"], window=period)

    return rsi.rsi().iloc[-1]


# =========================
# MARKET DATA
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
# RVOL
# =========================
def calculate_relative_volume(df):

    volume = df["v"].astype(float)

    current_volume = volume.iloc[-1]

    average_volume = volume.rolling(VOL_WINDOW).mean().iloc[-1]

    if average_volume == 0:
        return 0

    return current_volume / average_volume


# =========================
# FORMAT NUMBERS
# =========================
def format_number(num):

    if num >= 1_000_000_000:
        return f"{num/1_000_000_000:.1f}B"

    elif num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"

    elif num >= 1_000:
        return f"{num/1_000:.1f}K"

    return f"{num:,.0f}"


# =========================
# PSEUDO CVD
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
# BULLISH CVD DIVERGENCE
# =========================
def detect_bullish_cvd_divergence(df, lookback=10):

    if len(df) < lookback * 2:
        return False

    recent_price_low = df["close"].iloc[-lookback:].min()
    previous_price_low = df["close"].iloc[-lookback * 2 : -lookback].min()

    recent_cvd_low = df["cvd"].iloc[-lookback:].min()
    previous_cvd_low = df["cvd"].iloc[-lookback * 2 : -lookback].min()

    price_lower_low = recent_price_low < previous_price_low
    cvd_higher_low = recent_cvd_low > previous_cvd_low

    return price_lower_low and cvd_higher_low


# =========================
# LONG SCORE
# =========================
def compute_long_score(
    rsi,
    funding,
    oi,
    oi_delta,
    rvol,
    volume_24h,
    bullish_cvd_div,
):

    if volume_24h < 500_000:
        return -5

    score = 0

    # =====================
    # RISK
    # =====================

    oi_vol_ratio = oi / volume_24h

    if oi_vol_ratio > 5:
        score -= 2

    elif oi_vol_ratio > 2:
        score -= 1

    # =====================
    # RSI
    # =====================

    if rsi <= 25:
        score += 4

    elif rsi <= 30:
        score += 3

    elif rsi <= 35:
        score += 1

    else:
        score -= 1

    # =====================
    # FUNDING
    # =====================

    if funding < -0.03 and rsi <= 30:
        score += 3

    elif funding < -0.01:
        score += 2

    elif funding < 0:
        score += 1

    elif funding > 0.02:
        score -= 2

    # =====================
    # OI DELTA
    # =====================

    if oi_delta > 1 and rsi <= 35:
        score += 2

    # =====================
    # RVOL
    # =====================

    if rvol > 1.5:
        score += 2

    elif rvol > 1.2:
        score += 1

    elif rvol < 0.5:
        score -= 1

    # =====================
    # MOMENTUM EXPANSION
    # =====================

    if oi_delta > 5 and rvol > 1.2 and rsi <= 40:
        score += 2

    # =====================
    # CONTEXT
    # =====================

    if oi > 10_000_000 and rsi <= 35:
        score += 1

    if volume_24h > 1_000_000:
        score += 0.5

    # =====================
    # CVD
    # =====================

    if bullish_cvd_div and rsi <= 35:
        score += 2

    return score


# =========================
# SIGNAL
# =========================
def classify_from_score(score, rsi, funding, rvol):

    # STRONG LONG
    if score >= 7 and rsi <= 28 and funding < 0 and rvol > 1:
        return "🟢"

    # GOOD LONG
    if score >= 4:
        return "🟡"

    return "🔴"


# =========================
# LABELS
# =========================
def get_risk_label(oi, volume_24h):

    if volume_24h == 0:
        return "⚫"

    oi_vol_ratio = oi / volume_24h

    if oi_vol_ratio > 5:
        return "🔴"

    elif oi_vol_ratio > 2:
        return "🟡"

    else:
        return "🟢"


def get_funding_label(funding):

    if funding < 0:
        return "🟢"

    return "🔴"


def get_cvd_label(cvd_div):

    if cvd_div:
        return "🟢"

    return "🔴"


# =========================
# MAIN SCANNER
# =========================
def run_scanner():

    cleanup_old_data()

    markets = get_markets()

    market_data = get_market_data()

    results = []

    print(f"\nBuscando LONGS con RSI({RSI_PERIOD}) < {RSI_THRESHOLD}\n")

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

            bullish_cvd_div = detect_bullish_cvd_divergence(df)

            funding = market_data.get(symbol, {}).get("funding", 0) * 100

            oi = market_data.get(symbol, {}).get("open_interest", 0)

            oi_delta = get_oi_delta(symbol, oi)

            save_oi_snapshot(symbol, oi)

            volume_24h = market_data.get(symbol, {}).get("volume_24h", 0)

            score = compute_long_score(
                rsi,
                funding,
                oi,
                oi_delta,
                rv,
                volume_24h,
                bullish_cvd_div,
            )

            signal = classify_from_score(
                score,
                rsi,
                funding,
                rv,
            )

            risk_label = get_risk_label(oi, volume_24h)

            if rsi < RSI_THRESHOLD and oi > 0:

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
                        "cvd_div": bullish_cvd_div,
                    }
                )

        except Exception as e:
            print(f"Error en {symbol}: {e}")

        time.sleep(REQUEST_DELAY)

    results = sorted(results, key=lambda x: x["rsi"])

    print("=" * 122)

    if not results:

        print(f"\nNo hay activos con RSI < {RSI_THRESHOLD}")

    else:

        for item in results:

            print(
                f"{item['symbol']:<6} "
                f"RSI: {item['rsi']:>5.2f}  "
                f"Fund({get_funding_label(item['funding'])}): {item['funding']:>7.4f}  "
                f"CVD({get_cvd_label(item['cvd_div'])}) "
                f"OI: ${format_number(item['oi']):>7}  "
                f"OIΔ: {item['oi_delta']:>5.2f}%  "
                f"RVOL: {item['rv']:>4.2f}x  "
                f"Vol24h({item['risk_label']}): ${format_number(item['volume_24h']):>7}  "
                f"SCO({item['signal']}): {item['score']:>4.1f}"
            )

    print("=" * 122)


if __name__ == "__main__":

    run_scanner()
