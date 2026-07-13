import requests
import pandas as pd
from ta.momentum import RSIIndicator
import time
import argparse
import sqlite3
from fnmatch import fnmatch
from datetime import datetime
import numpy as np
import json

LOG_FILE = "zshort_scanner.log"

parser = argparse.ArgumentParser()
parser.add_argument("--rsi", type=float, default=40)

# parser.add_argument("--symbol", type=str, default=None)
parser.add_argument("--symbols", nargs="+", type=str, default=None)

args = parser.parse_args()
RSI_THRESHOLD = args.rsi

# SYMBOL = args.symbol.upper() if args.symbol else None
SYMBOLS = [s.upper() for s in args.symbols] if args.symbols else None

BASE_URL = "https://api.hyperliquid.xyz/info"
RSI_PERIOD = 14
VOL_WINDOW = 20
REQUEST_DELAY = 0.25
# HISTORY_RETENTION_SECONDS = 604800  # 7 days
HISTORY_RETENTION_SECONDS = 2592000  # 30 days

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

cursor.execute("""
CREATE TABLE IF NOT EXISTS price_history (
    symbol TEXT,
    timestamp INTEGER,
    price REAL
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS scanner_history (
    symbol TEXT,
    timestamp INTEGER,
    price REAL,
    rsi REAL,
    rvol REAL,
    funding REAL,
    oi REAL,
    oi_delta REAL,
    efficiency REAL,
    volume_24h REAL,
    score REAL,
    price_change_24h REAL,
    bearish_cvd_div INTEGER
)
""")
conn.commit()


cursor.execute("""
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,    
    price REAL,
    rsi REAL,
    rvol REAL,
    funding REAL,
    oi REAL,
    oi_delta REAL,
    oi_acceleration REAL,
    oi_threshold REAL,
    oi_strength  REAL,
    efficiency REAL,
    volume_24h REAL,    
    price_change_24h REAL,
    cvd_div INTEGER,
    score REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()


cursor.execute("""
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    interval TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    candle_time DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, interval, candle_time)
)
""")
conn.commit()


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
# Define el log scanner
# =========================
def log_message(message):
    # timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timestamp = datetime.now().strftime("%m-%d %H:%M")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")
        # f.write(f"{message}\n")


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
# Guardar snapshot market
# =========================
def save_scanner_snapshot(
    symbol,
    price,
    rsi,
    rvol,
    funding,
    oi,
    oi_delta,
    efficiency,
    volume_24h,
    score,
    price_change_24h,
    bearish_cvd_div,
):

    cursor.execute(
        """
        INSERT INTO scanner_history (
            symbol,
            timestamp,
            price,
            rsi,
            rvol,
            funding,
            oi,
            oi_delta,
            efficiency,
            volume_24h,
            score,
            price_change_24h,
            bearish_cvd_div
        )
        VALUES (
            ?,
            strftime('%s','now'),
            ?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            symbol,
            price,
            rsi,
            rvol,
            funding,
            oi,
            oi_delta,
            efficiency,
            volume_24h,
            score,
            price_change_24h,
            int(bearish_cvd_div),
        ),
    )

    conn.commit()


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
# def get_markets():
#     payload = {"type": "meta"}

#     r = requests.post(BASE_URL, json=payload)
#     data = r.json()

#     if "universe" not in data:
#         print("Error: respuesta inválida del API, falta 'universe'")
#         return []

#     return [asset["name"] for asset in data["universe"]]


# =========================
# Obtener mercados
# =========================
def get_markets(symbols=None):
    if symbols:
        return symbols

    payload = {"type": "meta"}

    r = requests.post(BASE_URL, json=payload)
    data = r.json()

    if "universe" not in data:
        print("Error: respuesta inválida del API, falta 'universe'")
        return []

    return [asset["name"] for asset in data["universe"]]


# =========================
# Obtener mercados desde archivo local JSON
# =========================
def get_markets_from_json():

    with open("markets.json", "r") as f:
        data = json.load(f)

    if "universe" not in data:
        print("Error: falta 'universe'")
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
# salvar candles
# =========================
def save_candles(symbol, interval, df):

    for _, row in df.iterrows():

        cursor.execute(
            """
            INSERT OR IGNORE INTO candles (
                symbol,
                interval,
                open,
                high,
                low,
                close,
                volume,
                candle_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                symbol,
                interval,
                float(row["o"]),
                float(row["h"]),
                float(row["l"]),
                float(row["c"]),
                float(row["v"]),
                row["t"],
            ),
        )

    conn.commit()
    # conn.close()


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
# guarda market data
# =========================
def save_market_data(market_data):

    for symbol, data in market_data.items():

        cursor.execute(
            """
            INSERT INTO market_snapshots (
                symbol,
                funding,
                oi,
                volume_24h,
                price,
                price_change_24h
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                symbol,
                data["funding"],
                data["open_interest"],
                data["volume_24h"],
                data["price"],
                data["change_24h"],
            ),
        )

    conn.commit()


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


def calculate_rvol_3d(df, period=20):

    volume = df["v"].astype(float)

    sma = volume.rolling(period).mean().iloc[-2]

    current = volume.iloc[-2]

    return current / sma if sma > 0 else 0


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
    rsi,
    funding,
    oi,
    oi_delta,
    rvol,
    volume_24h,
    bearish_cvd_div,
    price_change_pct_24h,
    symbol,
    oi_acceleration,
):

    oi_threshold = get_dynamic_oi_threshold(symbol)
    oi_strength = abs(oi_delta) / max(oi_threshold, 1)
    price_response = price_change_pct_24h / max(abs(oi_delta), 1)

    log_message(
        f"{symbol} "
        f"oi_delta={oi_delta:.2f} "
        f"threshold={oi_threshold:.2f} "
        f"strength={oi_strength:.2f} "
        f"price_response={price_response:.3f} "
        f"price_change_pct={price_change_pct_24h:.3f} "
        f"oi_acceleration={oi_acceleration:.3f} "
    )

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
        score -= 2  # riesgo / low liquidity / Baja rotación y mercado muy quieto

    elif oi_vol_ratio > 2:
        score -= 1  # speculative / Rotación baja, señales menos fiables

    elif 1 <= oi_vol_ratio <= 2:
        score += 1  # neutral / balanced market / Buena actividad, contexto saludable

    else:
        score += 0  # mercado muy activo / flujo agresivo / Mucha rotación, necesita confirmación con OIΔ

    # =====================
    # 3. BIAS (lo más importante)
    # =====================

    # RSI EXTREMO
    if rsi >= 80:
        score += 5
    elif rsi >= 75:
        score += 4
    elif rsi >= 70:
        score += 3
    elif rsi >= 65:
        score += 1
    else:
        score -= 1  # no hay sobrecompra real

    # FUNDING
    if funding >= 0.03 and rsi >= 70:
        score += 3  # euforia extrema
    elif funding >= 0.01:
        score += 2
    elif funding >= 0.002:
        score += 1
    elif funding < -0.02:
        score -= 2  # riesgo de short squeeze

    # =====================
    # 4. CONFIRMATION
    # =====================

    # OI DELTA / POSITIONING STRENGTH
    if oi_strength > 1.0 and rsi >= 75:
        score += 3

    elif oi_strength > 0.5 and rsi >= 70:
        score += 2

    elif oi_strength > 0.5 and rsi < 60:
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
    elif rvol < 1.2 and rsi >= 70:
        score += 1

    # mercado caliente
    elif rvol < 2.0 and rsi >= 70:
        score += 0

    # continuation fuerte
    elif rvol < 3.0 and rsi >= 70:
        score -= 1

    # expansión explosiva
    else:
        score -= 2

    # =====================
    # 5. MOMENTUM EXPANSION
    # =====================

    # expansión saludable / crowding
    if oi_strength > 1.0 and 0.5 <= rvol <= 1.2 and rsi >= 65:
        score += 1

    # momentum peligroso contra short
    if oi_strength > 1.0 and rvol > 2.0:
        score -= 2

    # =====================
    # 6. PRICE EFFICIENCY
    # =====================
    # Eficiencia de cuánto se mueve el precio, relativo al nuevo positioning (OI)

    # Mucha exposición nueva
    # Solo analizas eficiencia cuando el positioning realmente importa, filtras ruido
    if oi_strength > 1.0:

        # El deterioro es muy fuerte
        if price_response <= -0.5:
            score += 5

        # longs atrapados nuevo OI perdiendo dinero
        elif -0.5 < price_response < 0:
            score += 4

        # Longs entrando pero el precio ya ni responde Absorción fuerte / agotamiento
        # Empieza a entrar mucho OI, pero el precio ya no acelera igual
        elif 0 <= price_response < 0.25:
            score += 3

        # Agotamiento moderado
        elif 0.25 <= price_response < 0.50:
            score += 2

        # continuation moderada
        elif 0.50 <= price_response < 1.0:
            score -= 1

        # El precio todavía responde bien al nuevo OI
        # Trend saludable / continuation saludable
        elif 1.0 <= price_response < 2.0:
            score -= 3

        # squeeze / expansion agresiva
        elif price_response >= 2.0:
            score -= 5

    # =====================
    # 7. OI ACELERACION
    # =====================
    if oi_delta > 0 and oi_acceleration > 5 and rsi >= 70 and price_response < 0.25:
        score += 2

    # =====================
    # 8. CONTEXT
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
        score += 1

    # =====================
    # 9. CVD DIVERGENCE
    # =====================
    if bearish_cvd_div < 0 and rsi >= 70:
        score += 2

    return round(score, 1)


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


# =========================
# oi_threshold
# =========================
def get_dynamic_oi_threshold(symbol, window=50):

    cursor.execute(
        """
        SELECT oi_delta
        FROM scanner_history
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (symbol, window),
    )

    rows = cursor.fetchall()

    if len(rows) < 10:
        return 5

    deltas = [abs(r[0]) for r in rows if r[0] is not None]

    if not deltas:
        return 5

    # return min(50, max(5, np.percentile(deltas, 95)))
    return min(50, max(5, np.percentile(deltas, 85)))


# =========================
# classify_from_score
# =========================
def classify_from_score(score, rsi, funding, rvol, oi_delta):

    # 🔥 STRONG SHORT
    if score >= 12 and funding > 0:
        return "🚀"

    # 🔥 STRONG SHORT
    if score >= 9 and rsi >= 70 and funding > 0:
        return "🟢"

    # # 🔥 STRONG SHORT (confluencia real)
    # # if score >= 7 and rsi >= 72 and funding > 0 and rvol < 0.8 and oi_delta > 0 :
    # if score >= 7 and rsi >= 72 and funding > 0 and rvol < 0.6:
    #     return "🟢"

    # ⚠️ SHORT SETUP
    if score >= 9:
        return "🟡"

    # ⚠️ SHORT SETUP
    if 5 <= score < 9:
        return "🟡"

    # ⚠️ WEAK EDGE
    if 2 <= score < 5:
        return "🟠"

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
# Guardar snapshot Price
# =========================
def save_price_snapshot(symbol, price):
    cursor.execute(
        """
        INSERT INTO price_history (symbol, timestamp, price)
        VALUES (?, strftime('%s','now'), ?)
        """,
        (symbol, price),
    )
    conn.commit()


# =========================
# Obtener precio previo
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


# =========================
# Obtener dirección precio
# =========================
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
    if funding >= 0.02:
        return "🚀"
    elif funding >= 0.01:
        return "🟢"
    elif funding >= 0:
        return "🟡"
    else:
        return "🔴"


def get_cvd_label(cvd_div):
    if cvd_div:
        return "🟢"
    return "🔴"


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
    elif rvol > 1.6:
        return "🟠"

    # expansión saludable / actividad elevada
    elif rvol > 1.1:
        return "🟡"

    # normal o débil / agotamiento
    return "🟢"


def update_market_data(
    symbol,
    rsi,
    rvol,
    oi_delta,
    oi_acceleration,
    oi_threshold,
    oi_strength,
    price_response,
    cvd_signal,
    score,
):

    cursor.execute(
        """
        UPDATE market_snapshots
        SET 
            rsi = ?,
            rvol = ?,
            oi_delta = ?,
            oi_acceleration = ?,
            oi_threshold = ?,
            oi_strength = ?,
            efficiency = ?,
            cvd_div = ?,
            score = ?
        WHERE id = (
            SELECT id
            FROM market_snapshots
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
        )
    """,
        (
            rsi,
            rvol,
            oi_delta,
            oi_acceleration,
            oi_threshold,
            oi_strength,
            price_response,
            cvd_signal,
            score,
            symbol,
        ),
    )

    conn.commit()


# =========================
# Obtener el precio by hours
# =========================
def get_price_change_hours(symbol, current_price, hours=4):

    cursor.execute(
        """
        SELECT price
        FROM scanner_history
        WHERE symbol = ?
        AND timestamp <= CAST(strftime('%s', 'now', ?) AS INTEGER)
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (symbol, f"-{hours} hours"),
    )

    row = cursor.fetchone()

    if not row:
        return None

    old_price = row[0]

    if old_price <= 0:
        return None

    return ((current_price - old_price) / old_price) * 100


# =========================
# obtener el oi_delta previo
# =========================
def get_previous_oi_delta(symbol):

    cursor.execute(
        """
        SELECT oi_delta
        FROM scanner_history
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (symbol,),
    )

    row = cursor.fetchone()

    if not row:
        return None

    return row[0]


def get_oi_acceleration(symbol, current_oi_delta):

    cursor.execute(
        """
        SELECT oi_delta
        FROM scanner_history
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT 5
        """,
        (symbol,),
    )

    rows = cursor.fetchall()

    if len(rows) < 3:
        return 0

    avg_previous = np.mean([r[0] for r in rows])

    return current_oi_delta - avg_previous


def get_oi_delta_label(oi, oi_delta):
    # oi en USD
    if oi >= 1_000_000_000:
        if oi_delta >= 5:
            return "🟢"
        elif oi_delta >= 2:
            return "🟡"
        else:
            return "🔴"

    elif oi >= 500_000_000:
        if oi_delta >= 8:
            return "🟢"
        elif oi_delta >= 3:
            return "🟡"
        else:
            return "🔴"

    elif oi >= 100_000_000:
        if oi_delta >= 10:
            return "🟢"
        elif oi_delta >= 5:
            return "🟡"
        else:
            return "🔴"

    elif oi >= 20_000_000:
        if oi_delta >= 15:
            return "🟢"
        elif oi_delta >= 8:
            return "🟡"
        else:
            return "🔴"

    else:
        if oi_delta >= 20:
            return "🟢"
        elif oi_delta >= 12:
            return "🟡"
        else:
            return "🔴"


def get_efficiency_label(price_response):
    if price_response <= -1:  # -0.5 Colapso de eficiencia (setup excelente para short)
        return "🔻"  # "💥"

    elif price_response < 0:  # Muy bajista
        return "🔻"

    elif price_response < 0.25:  # Agotamiento fuerte
        return "➖"

    elif price_response < 1.0:  # Neutral / transición
        return "➖"

    elif price_response < 2.0:  # Continuación saludable
        return "🟢"

    else:
        return "🟢"  # "🚀" "🟢" Expansión explosiva (evitar shorts)


# =========================
# Calcular aceleración oi
# =========================
def calculate_oi_acceleration(symbol, current_oi_delta):

    previous_oi_delta = get_previous_oi_delta(symbol)

    if previous_oi_delta is None:
        return 0

    return current_oi_delta - previous_oi_delta
    

# =========================
# Scanner principal
# =========================
def run_scanner():

    cleanup_old_data()

    # markets = get_markets()
    # markets = get_markets_from_json()
    # markets = get_markets(SYMBOL)
    markets = get_markets(SYMBOLS)

    market_data = get_market_data()
    save_market_data(market_data)

    results = []

    print(f"\nBuscando activos con RSI({RSI_PERIOD}) > {RSI_THRESHOLD} en 3D...\n")

    for symbol in markets:

        # skip blacklist
        if is_blacklisted(symbol):
            continue

        try:

            volume_24h = market_data.get(symbol, {}).get("volume_24h", 0)

            # 1. Liquidez
            if volume_24h < 1_000_000:
                continue

            df_rsi = get_candles(symbol, interval="3d")
            save_candles(symbol, "3d", df_rsi)

            df_rvol = get_candles(symbol, interval="4h")
            save_candles(symbol, "4h", df_rvol)

            if df_rsi is None:
                continue

            if len(df_rsi) < VOL_WINDOW:
                continue

            rsi = calculate_rsi(df_rsi)

            rv = calculate_relative_volume(df_rvol)

            df_cvd = calculate_pseudo_cvd(df_rvol)

            cvd_signal = detect_cvd_signal(df_cvd)

            funding = market_data.get(symbol, {}).get("funding", 0) * 100

            oi = market_data.get(symbol, {}).get("open_interest", 0)

            oi_delta = get_oi_delta(symbol, oi)

            oi_acceleration = get_oi_acceleration(symbol, oi_delta)

            save_oi_snapshot(symbol, oi)

            oi_threshold = get_dynamic_oi_threshold(symbol)

            oi_strength = abs(oi_delta) / max(oi_threshold, 1)

            price = market_data.get(symbol, {}).get("price", 0)

            previous_price = get_previous_price(symbol)

            price_direction = get_price_direction(price, previous_price)

            save_price_snapshot(symbol, price)

            price_change_pct = get_price_change_hours(symbol, price, hours=8)
            if price_change_pct is None:
                price_change_pct = market_data.get(symbol, {}).get("change_24h", 0)

            score = compute_short_score(
                rsi,
                funding,
                oi,
                oi_delta,
                rv,
                volume_24h,
                cvd_signal,
                price_change_pct,
                symbol,
                oi_acceleration,
            )

            price_response = price_change_pct / max(abs(oi_delta), 1)

            update_market_data(
                symbol,
                rsi,
                rv,
                oi_delta,
                oi_acceleration,
                oi_threshold,
                oi_strength,
                price_response,
                cvd_signal,
                score,
            )

            save_scanner_snapshot(
                symbol=symbol,
                price=price,
                rsi=rsi,
                rvol=rv,
                funding=funding,
                oi=oi,
                oi_delta=oi_delta,
                efficiency=price_response,
                volume_24h=volume_24h,
                score=score,
                price_change_24h=price_change_pct,
                bearish_cvd_div=cvd_signal,
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
                        "cvd_div": cvd_signal,
                        "price_direction": price_direction,
                        "price": price,
                        "price_response": price_response,
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
                f"{item['price_direction']} {item['symbol'][:4]:<4}  "
                f"RSI: {item['rsi']:>5.2f}  "
                f"RVOL({get_rvol_label(item['rv'])}): {item['rv']:>4.2f}x  "
                f"FUN({get_funding_label(item['funding'])}):{item['funding']:>7.4f}  "
                f"OI({get_oi_label(item['oi'])}):${format_number(item['oi']):>7}  "
                # f"OIΔ: {item['oi_delta']:>6.2f}%  "
                f"OIΔ({get_oi_delta_label(item['oi'],item['oi_delta'])}):{item['oi_delta']:>6.2f}%  "
                f"V24h({item['risk_label']}):${format_number(item['volume_24h']):>7}  "
                # f"E({get_efficiency_label(item['price_response'])}) "
                # f"S({item['signal']}):{item['score']:>2}"
                f"E/S({get_efficiency_label(item['price_response'])}|{item['signal']}):{item['score']:>2}"
            )

            print(line1)

    print("=" * 122)


if __name__ == "__main__":
    run_scanner()

    # while True:
    #     try:
    #         run_scanner()
    #     except Exception as e:
    #         print(e)
    #     time.sleep(1200)


# | OI actual | OIΔ que empieza a ser interesante |
# | --------: | --------------------------------: |
# |      > 1B |                               +2% |
# |   500M–1B |                               +3% |
# | 100M–500M |                               +5% |
# |  20M–100M |                               +8% |
# |     < 20M |                              +12% |
