import requests
import pandas as pd
import time
from ta.momentum import RSIIndicator

BASE_URL = "https://api.hyperliquid.xyz/info"

RSI_PERIOD = 14
VOL_WINDOW = 20
REQUEST_DELAY = 0.25

# =========================
# DATA FETCH
# =========================


def get_markets():
    payload = {"type": "meta"}
    r = requests.post(BASE_URL, json=payload)
    data = r.json()

    return [asset["name"] for asset in data["universe"]]


def get_candles(symbol, interval="3d"):
    payload = {
        "type": "candleSnapshot",
        "req": {"coin": symbol, "interval": interval, "startTime": 0},
    }

    r = requests.post(BASE_URL, json=payload)
    if r.status_code != 200:
        return None

    df = pd.DataFrame(r.json())
    df["close"] = df["c"].astype(float)
    df["volume"] = df["v"].astype(float)
    return df


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
            "volume_24h": volume_24h,
            "open_interest": oi_usd,
        }

    return market_data


# =========================
# INDICATORS
# =========================


def calculate_rsi(df):
    return RSIIndicator(df["close"], window=RSI_PERIOD).rsi().iloc[-1]


def calculate_rvol(df):
    vol = df["volume"]
    if len(vol) < VOL_WINDOW:
        return 0

    return vol.iloc[-1] / vol.rolling(VOL_WINDOW).mean().iloc[-1]


# =========================
# SHORT ENGINE
# =========================


def compute_short_score(rsi, funding, oi, oi_delta, rvol, volume_24h):

    if volume_24h < 500_000:
        return -5

    score = 0

    if rsi >= 75:
        score += 4
    elif rsi >= 70:
        score += 3
    elif rsi >= 65:
        score += 1
    else:
        score -= 1

    if funding > 0.01:
        score += 2
    elif funding > 0:
        score += 1
    elif funding < -0.02:
        score -= 2

    if oi_delta > 1 and rsi >= 70:
        score += 2

    if rvol < 0.5 and rsi >= 70:
        score += 2
    elif rvol > 1.5:
        score -= 1

    if oi > 10_000_000 and rsi >= 70:
        score += 1

    if volume_24h > 1_000_000:
        score += 0.5

    return score


def short_signal(score, rsi, funding, rvol):
    if score < 2:
        return "❌ NO TRADE", 0
    if score < 4:
        return "⚠️ WEAK SHORT", 40
    if score < 7:
        conf = 60
        if rsi >= 70:
            conf += 10
        if funding > 0:
            conf += 5
        if rvol < 0.7:
            conf += 10
        return "⚠️ SHORT SETUP", min(conf, 85)

    conf = 75
    if rsi >= 72:
        conf += 10
    if funding > 0:
        conf += 5
    if rvol < 0.6:
        conf += 10
    return "🔥 STRONG SHORT", min(conf, 95)


# =========================
# LONG ENGINE
# =========================


def compute_long_score(rsi, funding, oi, oi_delta, rvol, volume_24h):

    if volume_24h < 500_000:
        return -5

    score = 0

    if rsi <= 30:
        score += 4
    elif rsi <= 35:
        score += 3
    elif rsi <= 40:
        score += 1
    else:
        score -= 1

    if funding < 0:
        score += 2
    elif funding > 0.01:
        score -= 2

    if oi_delta > 1 and rsi <= 40:
        score += 2

    if rvol > 1.5:
        score += 2
    elif rvol < 0.5:
        score -= 1

    if oi > 10_000_000 and rsi <= 40:
        score += 1

    if volume_24h > 1_000_000:
        score += 0.5

    return score


def long_signal(score, rsi, funding, rvol):
    if score < 2:
        return "❌ NO TRADE", 0
    if score < 4:
        return "⚠️ WEAK LONG", 40
    if score < 7:
        conf = 60
        if rsi <= 35:
            conf += 10
        if funding < 0:
            conf += 5
        if rvol > 1:
            conf += 10
        return "⚠️ LONG SETUP", min(conf, 85)

    conf = 75
    if rsi <= 30:
        conf += 10
    if funding < 0:
        conf += 5
    if rvol > 1.2:
        conf += 10
    return "🚀 STRONG LONG", min(conf, 95)


# =========================
# MAIN ENGINE
# =========================


def run_engine():

    markets = get_markets()
    market_data = get_market_data()

    results = []

    print("\n🔎 BIDIRECTIONAL MARKET REGIME SCANNER\n")

    for symbol in markets:
        try:
            df = get_candles(symbol)
            if df is None or len(df) < VOL_WINDOW:
                continue

            rsi = calculate_rsi(df)
            rvol = calculate_rvol(df)

            data = market_data.get(symbol, {})
            funding = data.get("funding", 0)
            oi = data.get("open_interest", 0)
            volume_24h = data.get("volume_24h", 0)

            oi_delta = 0  # simplificado (puedes conectar tu DB aquí)

            short_score = compute_short_score(
                rsi, funding, oi, oi_delta, rvol, volume_24h
            )
            long_score = compute_long_score(
                rsi, funding, oi, oi_delta, rvol, volume_24h
            )

            short_sig, short_conf = short_signal(short_score, rsi, funding, rvol)
            long_sig, long_conf = long_signal(long_score, rsi, funding, rvol)

            if long_conf > short_conf:
                final_signal = long_sig
                confidence = long_conf
                direction = "LONG"
            else:
                final_signal = short_sig
                confidence = short_conf
                direction = "SHORT"

            results.append(
                {
                    "symbol": symbol,
                    "rsi": rsi,
                    "direction": direction,
                    "signal": final_signal,
                    "confidence": confidence,
                    "short_score": short_score,
                    "long_score": long_score,
                }
            )

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"Error {symbol}: {e}")

    # =========================
    # SORT + PRINT
    # =========================

    results = sorted(results, key=lambda x: x["confidence"], reverse=True)

    print("=" * 110)

    for r in results[:20]:
        print(
            f"{r['symbol']:<8} "
            f"RSI:{r['rsi']:<6.2f} "
            f"{r['direction']:<5} "
            f"{r['signal']:<18} "
            f"CONF:{r['confidence']:>3}%"
            # print(f"{symbol:<8} RSI:{rsi:6.2f} {direction:<5} {signal:<18} CONF:{confidence:>3}%")
        )

    print("=" * 110)


if __name__ == "__main__":
    run_engine()
