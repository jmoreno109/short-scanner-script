import requests
import pandas as pd
from ta.momentum import RSIIndicator
import time
import argparse
import sqlite3
from fnmatch import fnmatch
from datetime import datetime
import numpy as np

LOG_FILE = "short_scanner.log"

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

with open("blacklist.txt", "r", encoding="utf-8") as f:
    BLACKLIST = [
        line.strip() for line in f if line.strip() and not line.startswith("#")
    ]


# =========================
# Define los activos a ignorar
# =========================
def is_blacklisted(symbol):
    for rule in BLACKLIST:
        if fnmatch(symbol.upper(), rule.upper()):
            return True
    return False


# =========================
# Calcular OI Delta
# =========================
def get_oi_delta(symbol, current_oi):

    # 4h
    cursor.execute(
        """
        SELECT oi
        FROM oi_history
        WHERE symbol = ?
        AND timestamp <= strftime('%s','now') - 14400 
        ORDER BY timestamp DESC
        LIMIT 1
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

        prev_day_price = float(ctx.get("prevDayPx", 0))
        if prev_day_price > 0:
            change_24h = ((price - prev_day_price) / prev_day_price) * 100
        else:
            change_24h = 0

        market_data[symbol] = {
            "funding": funding,
            "open_interest": oi_usd,
            "volume_24h": volume_24h,
            "price": price,
            "change_24h": change_24h,
        }

    return market_data


# =========================
# Calcular volumen relativo
# =========================
def calculate_relative_volume(df):

    volume = df["v"].astype(float)

    # excluir vela actual incompleta
    current_volume = volume.iloc[-2]

    average_volume = volume.iloc[-VOL_WINDOW - 2 : -2].mean()

    if average_volume <= 0:
        return 0

    rvol = current_volume / average_volume

    return round(rvol, 2)


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


# ========================================
# Score multi-factor + quant system básico
# ========================================
def compute_short_score(
    rsi, funding, oi, oi_delta, rvol, volume_24h, bearish_cvd_div, price_change_pct_24h
):

    # =====================
    # 1. LIQUIDITY GATE (FILTRO DURO)
    # =====================

    if volume_24h < 500_000:
        return -5  # basura / no tradear

    score = 0

    # =====================
    # 2. RISK (estructura de liquidez) (OI / VOL)
    # =====================
    # > 5 → Mercado muy especulativo / scalping / posible manipulación
    # 2 – 5 → Mercado pesado / apalancado / alta rotación / ruido
    # 1 – 2 → Normal / sano / Zona saludable
    # < 1 → Mercado muy activo vs tamaño del OI

    oi_vol_ratio = oi / volume_24h

    if oi_vol_ratio > 5:
        score -= 2  # leverage alto / riesgo / low liquidity
    elif oi_vol_ratio > 2:
        score -= 1  # speculative
    elif 1 <= oi_vol_ratio <= 2:
        score += 0.5  # neutral / balanced market
    else:
        score += 0  # mercado muy activo / alta rotación / flujo agresivo

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
    elif funding > 0.002:
        score += 1
    elif funding < -0.02:
        score -= 2  # riesgo de short squeeze

    # =====================
    # 4. CONFIRMATION
    # =====================

    # OI DELTA
    if oi_delta > 1 and rsi >= 70:
        score += 2

    elif oi_delta > 1 and rsi < 60:
        score += 0

    # RVOL (CALIBRADO PARA 4h)
    # Agotamiento  0.05 - 0.40
    # Normal       0.5  - 1.0
    # Expansion    1.2  - 2.0
    # Squeeze momentum 2.5+

    # agotamiento extremo
    if rvol < 0.5 and rsi >= 70:
        score += 3

    # agotamiento moderado
    elif rvol < 0.8 and rsi >= 70:
        score += 2

    # volumen normal
    elif rvol < 1.2:
        score -= 0.5

    # mercado caliente
    elif rvol < 2.0:
        score -= 1.5

    # continuation fuerte
    elif rvol < 3.0:
        score -= 3

    # expansión explosiva
    else:
        score -= 5

    # =====================
    # 5. MOMENTUM EXPANSION
    # =====================

    # expansión saludable / crowding
    if oi_delta > 3 and 0.5 <= rvol <= 1.2 and rsi >= 65:
        score += 0.5

    # momentum peligroso contra short
    if oi_delta > 3 and rvol > 2.0:
        score -= 2

    # =====================
    # 5.5 PRICE EFFICIENCY
    # =====================
    # eficiencia:
    # cuánto se mueve el precio
    # relativo al nuevo positioning (OI)

    efficiency = abs(price_change_pct_24h) / max(abs(oi_delta), 1)

    # mucha exposición nueva
    if oi_delta > 6:  # 7-8

        # Muchos longs entrando pero el precio ya ni responde
        # Absorción fuerte / agotamiento
        # Empieza a entrar mucho OI, pero el precio ya no acelera igual.
        if efficiency < 0.25:
            score += 4

        # Agotamiento moderado
        elif efficiency < 0.50:
            score += 2

        # El precio todavía responde bien al nuevo OI
        # Trend saludable / continuation saludable
        elif efficiency > 1:
            score -= 3

    # longs entrando
    # pero el precio ya negativo
    # MUY bearish
    if oi_delta > 5 and price_change_pct_24h < 0 and rsi >= 70:
        score += 5

    # =====================
    # 6. CONTEXT
    # =====================

    # OI alto SOLO ayuda si hay debilidad
    if oi > 10_000_000 and rsi >= 70 and rvol < 1.2:
        score += 1

    # OI bajo = manipulable
    if oi < 5_000_000:
        score -= 4

    elif oi < 10_000_000:
        score -= 3

    elif oi < 20_000_000:
        score -= 2

    # Liquidez suficiente
    if volume_24h > 1_000_000:
        score += 0.5

    # =====================
    # 7. CVD DIVERGENCE
    # =====================

    if bearish_cvd_div < 0 and rsi >= 70:
        score += 2

    return round(score, 1)


# ========================================
# TREND SCORE (RSI + RVOL)
# ¿Está sobreextendido o en continuación fuerte?
# | Score   | Semáforo | Significado                               |
# | ------- | -------- | ----------------------------------------- |
# | `>= 4`  | 🔴       | Trend muy extendido / posible agotamiento |
# | `2 a 4` | 🟡       | Trend fuerte pero vigilable               |
# | `0 a 2` | 🟢       | Trend saludable                           |
# | `< 0`   | ⚪       | Sin momentum                              |
# ========================================
def trend_score(rsi, rvol):

    score = 0

    # RSI (exceso direccional)
    if rsi >= 75:
        score += 4
    elif rsi >= 70:
        score += 3
    elif rsi >= 65:
        score += 1
    else:
        score -= 1

    # RVOL (fase del movimiento)
    if rvol < 0.5:
        score += 2  # agotamiento
    elif rvol < 0.8:
        score += 1
    elif rvol < 1.2:
        score -= 0.5
    elif rvol < 2:
        score -= 1.5
    else:
        score -= 3

    return round(score, 2)


# ========================================
# POSITIONING SCORE (OI + FUNDING + OIΔ)
# ¿Está el mercado cargado en longs o desarmándose?
# ========================================
# | Score   | Semáforo | Significado                        |
# | ------- | -------- | ---------------------------------- |
# | `>= 5`  | 🔴       | Mercado demasiado cargado en longs |
# | `2 a 5` | 🟡       | Positioning agresivo               |
# | `0 a 2` | 🟢       | Balanceado                         |
# | `< 0`   | 🟢🟦     | Desapalancamiento / reset          |
def positioning_score(oi, funding, oi_delta):

    score = 0

    # FUNDING (crowding de longs)
    if funding > 0.03:
        score += 3
    elif funding > 0.01:
        score += 2
    elif funding > 0.002:
        score += 1
    elif funding < -0.02:
        score -= 2

    # OI DELTA (flow)
    if oi_delta > 5:
        score += 3
    elif oi_delta > 2:
        score += 2
    elif oi_delta < -2:
        score -= 2

    # OI absoluto (riesgo estructural)
    if oi < 5_000_000:
        score -= 3
    elif oi < 10_000_000:
        score -= 2
    elif oi > 50_000_000:
        score += 1

    return round(score, 2)


# ========================================
# ABSORPTION SCORE (EFFICIENCY + CVD)
# ¿El mercado está absorbiendo o hay continuación real?
# ========================================
# | Score   | Semáforo  | Significado                     |
# | ------- | --------- | ------------------------------- |
# | `>= 5`  | 🔴        | Fuerte absorción / distribución |
# | `2 a 5` | 🟡        | Posible absorción               |
# | `0 a 2` | 🟢        | Movimiento limpio               |
# | `< 0`   | 🟢 fuerte | Continuación real               |
def absorption_score(price_change_pct_24h, oi_delta, bearish_cvd_div):

    score = 0

    efficiency = abs(price_change_pct_24h) / max(abs(oi_delta), 1)

    # ABSORCIÓN (mucho OI, poco movimiento)
    if efficiency < 0.25:
        score += 4
    elif efficiency < 0.5:
        score += 2
    elif efficiency > 1:
        score -= 3

    # CVD divergence (smart money vs price)
    if bearish_cvd_div < 0:
        score += 2

    # exceso de positioning sin reacción
    if oi_delta > 5 and price_change_pct_24h < 0:
        score += 3

    return round(score, 2)


# ========================================
# SCORE FINAL (combinación limpia)
# ========================================
def final_score(trend, positioning, absorption):

    score = trend + positioning + absorption
    return round(score, 2)


def classify_from_score(score, rsi, funding, rvol, oi_delta):

    # 🔥 STRONG SHORT
    if score >= 12 and funding > 0:
        return "🚀"

    # 🔥 STRONG SHORT
    if score >= 8 and rsi >= 70 and funding > 0 and rvol < 1:
        return "🟢"

    # 🔥 STRONG SHORT (confluencia real)
    # if score >= 7 and rsi >= 72 and funding > 0 and rvol < 0.8 and oi_delta > 0 :
    if score >= 7 and rsi >= 72 and funding > 0 and rvol < 0.6:
        return "🟢"

    # ⚠️ SHORT SETUP
    if score >= 7:
        return "🟡"

    # ⚠️ SHORT SETUP
    if 4 <= score < 7:  # 🟠
        return "🟡"

    # ⚠️ WEAK EDGE
    if 2 <= score < 4:
        return "🟡"

    return "🔴"


# =========================
# Pseudo CVD (weighted)
# =========================
def calculate_pseudo_cvd(df):

    volume = df["v"].astype(float)

    delta = []

    for i in range(len(df)):

        open_price = float(df.iloc[i]["o"])
        close_price = float(df.iloc[i]["c"])

        # retorno de la vela
        move_pct = (close_price - open_price) / open_price

        # delta ponderado
        weighted_delta = volume.iloc[i] * move_pct

        delta.append(weighted_delta)

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


# def detect_cvd_signal0(df, lookback=10):

#     recent_price_high = df["close"].iloc[-lookback:].max()
#     previous_price_high = df["close"].iloc[-lookback * 2 : -lookback].max()

#     recent_cvd_high = df["cvd"].iloc[-lookback:].max()
#     previous_cvd_high = df["cvd"].iloc[-lookback * 2 : -lookback].max()

#     # bearish divergence
#     if recent_price_high > previous_price_high and recent_cvd_high < previous_cvd_high:
#         return "🔻"

#     # bullish confirmation
#     if recent_price_high > previous_price_high and recent_cvd_high > previous_cvd_high:
#         return "🟢"

#     return "➖"


def detect_cvd_signal(df, lookback=10):

    recent_price_high = df["close"].iloc[-lookback:].max()
    previous_price_high = df["close"].iloc[-lookback * 2 : -lookback].max()

    recent_cvd_high = df["cvd"].iloc[-lookback:].max()
    previous_cvd_high = df["cvd"].iloc[-lookback * 2 : -lookback].max()

    # bearish divergence
    if recent_price_high > previous_price_high and recent_cvd_high < previous_cvd_high:
        return -1

    # bullish confirmation
    if recent_price_high > previous_price_high and recent_cvd_high > previous_cvd_high:
        return 1

    return 0

def get_cvd_label(cvd_div):

    # bearish divergence
    if cvd_div < 0:
        return "🔻"

    # bullish confirmation
    if cvd_div > 0:
        return "🟢"

    return "➖"



# =========================
# Obtener dirección precio
# =========================
def get_previous_price(symbol):

    cursor.execute(
        """
        SELECT price
        FROM price_history
        WHERE symbol = ?
        AND price > 0
        ORDER BY timestamp DESC
        LIMIT 2
        """,
        (symbol,),
    )

    rows = cursor.fetchall()

    if len(rows) < 2:
        return None

    return rows[1][0]


def get_price_direction(current_price, previous_price):

    if previous_price is None or previous_price == 0:
        return "⚪"

    change_pct = ((current_price - previous_price) / previous_price) * 100

    if change_pct >= 3:
        return "🚀"

    elif change_pct > 0:
        return "🟢"

    elif change_pct <= -3:
        return "💥"  # "💥"

    elif change_pct < 0:
        return "🔴"

    return "⚪"


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


# def get_cvd_label(cvd_div):

#     if cvd_div:
#         return "🟢"
#     return "🔴"


def get_oi_label(oi):

    if oi < 5_000_000:  # muy manipulable
        return "🔴"

    elif oi < 20_000_000:  # speculative
        return "🟡"

    return "🟢"  # más estable


def get_rvol_label(rvol):

    # squeeze / continuation violenta fuerte / explosión / casino total
    if rvol > 2.5:
        return "🔴"

    # momentum fuerte / mercado caliente / extremadamente especulativo
    elif rvol > 1.5:
        return "🟡"  # "🟠"

    # expansión saludable / actividad elevada
    elif rvol > 1:  # 0.8
        return "🟡"

    # normal o débil / agotamiento
    return "🟢"


def classify_signal(trend, positioning, absorption):

    # ==================================
    # 🟢 HIGH QUALITY SHORT
    # crowding + agotamiento + absorción
    # ==================================
    if trend > 3 and positioning > 2 and absorption > 3:
        return "🟢"

    # ==================================
    # 🟡 GOOD SETUP
    # setup interesante
    # ==================================
    if trend > 2 and positioning > 1:
        return "🟡"

    # ==================================
    # 🟠 EARLY WARNING
    # algo empieza
    # ==================================
    if trend > 1 or positioning > 1:
        return "🟠"

    # ==================================
    # 🔴 NO EDGE
    # nada especial
    # ==================================
    return "🔴"


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

        # skip blacklist
        if is_blacklisted(symbol):
            continue

        try:

            volume_24h = market_data.get(symbol, {}).get("volume_24h", 0)

            change_24h = market_data.get(symbol, {}).get("change_24h", 0)

            # 1. Liquidez
            if volume_24h < 1_000_000:
                continue

            df_rsi = get_candles(symbol, interval="3d")

            df_rvol = get_candles(symbol, interval="4h")

            if df_rsi is None:
                continue

            if len(df_rsi) < VOL_WINDOW:
                continue

            rsi = calculate_rsi(df_rsi)

            rv = calculate_relative_volume(df_rvol)

            df_cvd = calculate_pseudo_cvd(df_rvol)

            # ---
            #bearish_cvd_div = detect_bearish_cvd_divergence(df_cvd)
            cvd_signal = detect_cvd_signal(df_cvd)
            # ---

            funding = market_data.get(symbol, {}).get("funding", 0) * 100

            oi = market_data.get(symbol, {}).get("open_interest", 0)

            oi_delta = get_oi_delta(symbol, oi)

            price = market_data.get(symbol, {}).get("price", 0)

            previous_price = get_previous_price(symbol)

            price_direction = get_price_direction(price, previous_price)

            score = compute_short_score(
                #rsi, funding, oi, oi_delta, rv, volume_24h, bearish_cvd_div, change_24h
                rsi, funding, oi, oi_delta, rv, volume_24h, cvd_signal, change_24h
            )

            efficiency = abs(change_24h) / max(abs(oi_delta), 1)

            signal = classify_from_score(score, rsi, funding, rv, oi_delta)

            risk_label = get_risk_label(oi, volume_24h)

            # -----------------------------------------------
            trend = trend_score(rsi, rv)
            positioning = positioning_score(oi, funding, oi_delta)

            #absorption = absorption_score(change_24h, oi_delta, bearish_cvd_div)
            absorption = absorption_score(change_24h, oi_delta, cvd_signal)

            fscore = final_score(trend, positioning, absorption)

            signal_prom = classify_signal(trend, positioning, absorption)
            # -----------------------------------------------

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
                        #"cvd_div": bearish_cvd_div,
                        "price_direction": price_direction,
                        "price": price,
                        "fscore": fscore,
                        "trend_score": trend,
                        "positioning_score": positioning,
                        "absorption_score": absorption,
                        "signal_prom": signal_prom,
                        "cvd_signal": cvd_signal,
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

            line1 = (
                f"{item['symbol'][:6]:<6}  "
                f"Score({item['signal']}): {item['score']:>5.1f}  "
                f"Trend: {item['trend_score']:>4.1f}  "
                f"Positioning: {item['positioning_score']:>4.1f}  "
                f"Absorption: {item['absorption_score']:>4.1f}  "
                f"SubScore({item['signal_prom']}): {item['fscore']:>5.1f}  "
                f"CVD({get_cvd_label(item['cvd_signal'])})"
            )
            
            print(line1)

    print("=" * 122)

if __name__ == "__main__":
    run_scanner()

    # while True:
    #     run_scanner()
    #     time.sleep(3600)
