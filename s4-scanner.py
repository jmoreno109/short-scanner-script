import requests
import pandas as pd
from ta.momentum import RSIIndicator
import time
import argparse
import sqlite3
from fnmatch import fnmatch
from datetime import datetime
import numpy as np

LOG_FILE = "zscore_scanner.log"

parser = argparse.ArgumentParser()
parser.add_argument("--rsi", type=float, default=70)
args = parser.parse_args()
RSI_THRESHOLD = args.rsi

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

cursor.execute("""
CREATE TABLE IF NOT EXISTS analysis_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    price REAL,
    rsi REAL,
    score REAL,
    bullish_exhaustion_pct REAL,
    short_opportunity_pct REAL,
    distribution_pct REAL,
    long_trap_pct REAL,
    smart_money_exit_pct REAL,
    reversal_pct REAL,
    cvd_signal INTEGER,
    timestamp INTEGER DEFAULT (strftime('%s','now')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(
        symbol,
        price,
        score,
        bullish_exhaustion_pct,
        short_opportunity_pct,
        distribution_pct,
        long_trap_pct,
        smart_money_exit_pct,
        reversal_pct,
        cvd_signal
    )
)
""")
conn.commit()


def save_analysis_snapshot(
    symbol,
    price,
    rsi,
    score,
    bullish_exhaustion_pct,
    short_opportunity_pct,
    distribution_pct,
    long_trap_pct,
    smart_money_exit_pct,
    reversal_pct,
    cvd_signal,
):

    cursor.execute(
        """
        INSERT OR IGNORE INTO analysis_snapshots (
            symbol,
            price,
            rsi,
            score,
            bullish_exhaustion_pct,
            short_opportunity_pct,
            distribution_pct,
            long_trap_pct,
            smart_money_exit_pct,
            reversal_pct,
            cvd_signal 
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            price,
            rsi,
            score,
            bullish_exhaustion_pct,
            short_opportunity_pct,
            distribution_pct,
            long_trap_pct,
            smart_money_exit_pct,
            reversal_pct,
            cvd_signal,
        ),
    )

    conn.commit()


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
    timestamp = datetime.now().strftime("%m-%d %H:%M")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


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

    cursor.execute("""
        SELECT DISTINCT symbol
        FROM scanner_history
        ORDER BY symbol
    """)

    rows = cursor.fetchall()

    return [row[0] for row in rows]


# =========================
# Obtener candles
# =========================
def get_candles(symbol, interval, limit=200):

    cursor.execute(
        """
        SELECT
            candle_time,
            open,
            high,
            low,
            close,
            volume
        FROM candles
        WHERE symbol = ?
        AND interval = ?
        ORDER BY candle_time DESC
        LIMIT ?
        """,
        (symbol, interval, limit),
    )

    rows = cursor.fetchall()

    if not rows:
        return None

    # invertir para dejar vieja -> nueva
    rows.reverse()

    df = pd.DataFrame(
        rows,
        columns=[
            "candle_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

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

    cursor.execute("""
        SELECT
            sh.symbol,
            sh.timestamp,
            sh.price,
            sh.rsi,
            sh.rvol,
            sh.funding,
            sh.oi,
            sh.oi_delta,
            sh.efficiency,
            sh.volume_24h,
            sh.score,
            sh.price_change_24h,
            sh.bearish_cvd_div
        FROM scanner_history AS sh
        INNER JOIN (
            SELECT
                symbol,
                MAX(timestamp) AS max_timestamp
            FROM scanner_history
            GROUP BY symbol
        ) latest
        ON sh.symbol = latest.symbol
        AND sh.timestamp = latest.max_timestamp
    """)

    rows = cursor.fetchall()

    market_data = {}

    for row in rows:

        symbol = row[0]

        market_data[symbol] = {
            "timestamp": row[1],
            "price": row[2],
            "rsi": row[3],
            "rvol": row[4],
            "funding": row[5],
            "open_interest": row[6],
            "oi_delta": row[7],
            "efficiency": row[8],
            "volume_24h": row[9],
            "score": row[10],
            "change_24h": row[11],
            "bearish_cvd_div": row[12],
        }

    return market_data


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
# Calcular volumen relativo
# =========================
def calculate_relative_volume(df):

    volume = df["volume"].astype(float)

    # excluir vela actual incompleta
    current_volume = volume.iloc[-2]

    average_volume = volume.iloc[-VOL_WINDOW - 2 : -2].mean()

    if average_volume <= 0:
        return 0

    rvol = current_volume / average_volume

    return round(rvol, 2)


# =========================
# score multi-factor + quant system básico
# =========================
def compute_short_score_new(
    rsi,
    funding,
    oi,
    oi_delta,
    rvol,
    volume_24h,
    bearish_cvd_div,
    price_change_pct_24h,
    symbol,
):

    score = 0

    # RSI
    if rsi >= 80:
        score += 5
    elif rsi >= 75:
        score += 4
    elif rsi >= 70:
        score += 3
    elif rsi >= 65:
        score += 1

    # RSI Sobrecomprado
    if rsi > 65:

        # Funding (crowded longs = bearish)
        if funding > 0.01:
            score += 2
        elif funding > 0:
            score += 1
        elif funding < -0.02:
            score -= 2  # squeeze risk (peligro)

        # OI Delta (flujo)
        if oi_delta > 1:
            score += 2
        elif oi_delta < -1:
            score += 1

        # RVOL (debilidad o exceso)
        if rvol < 0.5:
            score += 2  # debilidad → bueno para short
        elif rvol > 1.5:
            score -= 1  # momentum fuerte contra short

    # Open Interest (liquidez)
    if oi > 10_000_000:
        score += 1

    # volumen (confirmación de interés)
    if volume_24h > 1_000_000:
        score += 1

    return score


# =========================
# score multi-factor + quant system básico
# =========================
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
):

    oi_threshold = get_dynamic_oi_threshold(symbol)
    oi_strength = abs(oi_delta) / max(oi_threshold, 1)
    price_response = price_change_pct_24h / max(abs(oi_delta), 1)

    log_message(
        f"{symbol} "
        f"oi_delta={oi_delta:.2f} "
        f"threshold={oi_threshold:.2f} "
        f"ratio={oi_delta / oi_threshold:.2f} "
        f"strength={oi_strength:.2f} "
        f"price_response={price_response:.3f} "
        f"price_change_pct={price_change_pct_24h:.3f} "
    )

    risk_symbol = "NEAR"
    if symbol == risk_symbol:
        log_message(f"2.RISK {symbol} score += 1")

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
        score += 1  # neutral / balanced market

    else:
        score += 0  # mercado muy activo / alta rotación / flujo agresivo

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
    elif rvol < 2.0 and rsi >= 70:  # --------------check
        score += 0

    # continuation fuerte
    elif rvol < 3.0 and rsi >= 70:
        score -= 1

    # expansión explosiva # --------------check
    # else:
    #     score -= 5

    # =====================
    # 5. MOMENTUM EXPANSION
    # =====================

    # expansión saludable / crowding
    if oi_strength > 1.0 and 0.5 <= rvol <= 1.2 and rsi >= 65:
        score += 0.5

    # momentum peligroso contra short
    if oi_strength > 1.0 and rvol > 2.0:
        score -= 2

    # =====================
    # 5.5 PRICE EFFICIENCY
    # =====================
    # Eficiencia cuánto se mueve el precio
    # relativo al nuevo positioning (OI)

    # Mucha exposición nueva, filtras ruido
    # Solo analizas eficiencia cuando el positioning realmente importa.
    # if oi_strength > 1.0:
    if oi_delta > oi_threshold:

        # El deterioro es muy fuerte
        if price_response <= -0.5:
            score += 7

        # longs atrapados nuevo OI perdiendo dinero
        elif -0.5 < price_response < 0:
            score += 6

        # Longs entrando pero el precio ya ni responde Absorción fuerte / agotamiento
        # Empieza a entrar mucho OI, pero el precio ya no acelera igual
        elif 0 <= price_response < 0.25:
            score += 4

        # Agotamiento moderado
        elif 0.25 <= price_response < 0.50:
            score += 2

        # continuation moderada
        elif 0.50 <= price_response < 1.0:  # -------------------------------check
            score -= 1

        # El precio todavía responde bien al nuevo OI
        # Trend saludable / continuation saludable
        elif 1.0 <= price_response < 2.0:
            score -= 3

        # squeeze / expansion agresiva
        elif price_response >= 2.0:
            score -= 5

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
# Obtiene el oi_threshold de forma dinamica
# ========================================
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

    return min(50, max(5, np.percentile(deltas, 95)))


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


# ========================================
# Classify
# ========================================
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
# Pseudo CVD (weighted)
# =========================
def calculate_pseudo_cvd(df):

    # volume = df["v"].astype(float)
    volume = df["volume"].astype(float)

    delta = []

    for i in range(len(df)):

        # open_price = float(df.iloc[i]["o"])
        # close_price = float(df.iloc[i]["c"])

        open_price = float(df.iloc[i]["open"])
        close_price = float(df.iloc[i]["close"])

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


# =========================
# detect_cvd_signal
# =========================
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
# get_cvd_label
# =========================
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
# Compara el oi_delta actual contra los últimos N períodos.
# qué tan anómalo es respecto a su propio historial.
# =========================
def get_oi_zscore(symbol, current_oi_delta, window=100):

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

    if len(rows) < 20:
        return 0

    deltas = [abs(r[0]) for r in rows if r[0] is not None]

    mean = np.mean(deltas)
    std = np.std(deltas)

    if std == 0:
        return 0

    return (abs(current_oi_delta) - mean) / std


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


def clamp(value, min_value=0, max_value=100):
    return max(min_value, min(value, max_value))


def normalize(value, min_val, max_val):
    if max_val == min_val:
        return 0
    return clamp(((value - min_val) / (max_val - min_val)) * 100)


# =========================
# 1 Exceso Alcista Score
# Busca activos demasiado sobreextendidos.
# =========================
# Valores altos sugieren:
# Los compradores ya han empujado mucho el precio.
# La fuerza alcista podría estar perdiendo impulso.
# Hay mayor riesgo de consolidación o corrección.
def bullish_exhaustion_score(rsi, rvol, price_change_24h, efficiency):

    # | Score | Estado |
    # | ----- | ------ |
    # | <40   | 🟢     |
    # | 40-70 | 🟡     |
    # | >70   | 🔴     |
    # Interpretación: "Subió demasiado rápido. Posible agotamiento."

    score = 0

    if rsi > 80:
        score += 5

    if rsi > 70:  # 75
        score += 30

    if rvol > 2:
        score += 20  # 25

    if price_change_24h > 8:
        score += 25

    if efficiency > 0.8:
        score += 20

    return clamp(score)


def get_be_label(score):

    if score >= 70:
        return "🟢"

    elif score >= 40:
        return "🟡"

    return "🔴"


# =========================
# 2 Short Opportunity Score
# Combina momentum agotado + derivados débiles.
# =========================
def short_opportunity_score(rsi, funding, oi_delta, bearish_cvd_div):

    # | Score  | Lectura   |
    # | ------ | --------- |
    # | 0-25   | No        |
    # | 25-50  | Débil     |
    # | 50-75  | Buena     |
    # | 75-100 | Excelente |

    score = 0

    if rsi > 70:
        score += 25

    if funding > 0.03:
        score += 25

    if oi_delta < 0:
        score += 25

    if bearish_cvd_div < 0:
        score += 25

    return clamp(score)


def get_so_label(score):

    # 75-100 Excelente
    if score >= 75:
        return "🟢"

    # 50-75 Buena
    elif score >= 50:
        return "🟡"

    # 25-50 Débil
    elif score >= 25:
        return "🟠"

    # 0-25 No
    return "🔴"


# =========================
# 3. Distribution Score
# Semáforo de Distribución Institucional
# Precio subiendo mientras el interés abierto cae.
# =========================
def distribution_score(price_change_24h, oi_delta, bearish_cvd_div):

    # Esto suele significar: El precio sigue subiendo pero los compradores agresivos desaparecen.

    score = 0

    if price_change_24h > 5:
        score += 40

    if oi_delta < 0:
        score += 30

    if bearish_cvd_div < 0:
        score += 30

    return clamp(score)


# =========================
# 4. Long Trap Score
# Semáforo de Riesgo de Long Trap
# Normalizado de 0-100.
# Un score alto significa que el mercado presenta características típicas de un posible Long Trap.
# ¿Qué tan probable es que los longs que están entrando ahora terminen atrapados por una caída?
# =========================
# Valores altos sugieren:
# Mucha gente está comprando tarde.
# El mercado puede provocar una subida final para atraer compradores.
# Luego podría venir una caída brusca.
def long_trap_score(rsi, funding, rvol, oi_delta):

    # | Trap Score  |
    # | ----------- |
    # | <40 Bajo    |
    # | 40-70 Medio |
    # | >70 Alto    |

    rsi_score = normalize(rsi, 50, 90)

    funding_score = normalize(funding, 0.0, 0.05)

    rvol_score = normalize(rvol, 1, 4)

    oi_score = normalize(abs(min(oi_delta, 0)), 0, 10)

    score = (
        rsi_score * 0.30 + funding_score * 0.30 + rvol_score * 0.20 + oi_score * 0.20
    )

    return round(clamp(score), 2)


def get_lt_label(score):

    if score >= 70:
        return "🟢"

    elif score >= 40:
        return "🟡"

    return "🔴"


# =========================
# 5. Smart Money Exit Score
# Detecta cuando el dinero inteligente sale.
# =========================
def smart_money_exit_score(oi_delta, efficiency, bearish_cvd_div, funding):

    # Si sale >70:
    # 🔴 Posible techo local.

    score = 0

    if oi_delta < -2:
        score += 30

    if efficiency > 0.8:
        score += 20

    if bearish_cvd_div < 0:
        score += 30

    if funding > 0:
        score += 20

    return clamp(score)


# =========================
# 6. Reversal Probability Score
# Versión simple sin percentiles.
# Probabilidad de reversión.
# =========================
# Valores altos sugieren:
# El movimiento actual tiene más posibilidades de darse vuelta.
# No indica necesariamente la magnitud del giro.
# Puede ser reversión alcista o bajista según el contexto.
def reversal_probability_score(rsi, rvol, funding, price_change_24h):

    # | Score                  |
    # | ---------------------- |
    # | <40 Continuación       |
    # | 40-60 Neutral          |
    # | >60 Reversión probable |
    # | >80 Muy probable       |

    rsi_score = normalize(rsi, 50, 90)

    rvol_score = normalize(rvol, 1, 4)

    funding_score = normalize(funding, 0, 0.05)

    price_score = normalize(price_change_24h, 0, 20)

    score = (
        rsi_score * 0.30 + rvol_score * 0.25 + funding_score * 0.20 + price_score * 0.25
    )

    return round(clamp(score), 2)


def get_rp_label(score):

    # >70 Muy probable
    if score >= 70:  # 80
        return "🟢"

    # >50 Reversión probable
    elif score >= 50:  # 60
        return "🟡"

    # 30-50 Neutral
    elif score >= 30:  # 40
        return "🟠"

    # <40 Continuación
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

            # 1. Liquidez
            if volume_24h < 1_000_000:
                continue

            df_rsi = get_candles(symbol, interval="3d")

            df_rvol = get_candles(symbol, interval="4h")

            if df_rsi is None:
                continue

            if len(df_rsi) < VOL_WINDOW:
                continue

            rsi = market_data.get(symbol, {}).get("rsi", 0)

            rv = market_data.get(symbol, {}).get("rvol", 0)

            # df_cvd = calculate_pseudo_cvd(df_rvol)
            # cvd_signal = detect_cvd_signal(df_cvd)
            cvd_signal = market_data.get(symbol, {}).get("bearish_cvd_div", 0)

            funding = market_data.get(symbol, {}).get("funding", 0)

            oi = market_data.get(symbol, {}).get("open_interest", 0)

            oi_delta = market_data.get(symbol, {}).get("oi_delta", 0)

            price = market_data.get(symbol, {}).get("price", 0)

            previous_price = get_previous_price(symbol)

            price_direction = get_price_direction(price, previous_price)

            price_change_pct = market_data.get(symbol, {}).get("change_24h", 0)

            efficiency = market_data.get(symbol, {}).get("efficiency", 0)

            bearish_cvd_div = market_data.get(symbol, {}).get("bearish_cvd_div", 0)

            score = market_data.get(symbol, {}).get("score", 0)

            score2 = compute_short_score_new(
                rsi,
                funding,
                oi,
                oi_delta,
                rv,
                volume_24h,
                cvd_signal,
                price_change_pct,
                symbol,
            )

            be_score = bullish_exhaustion_score(rsi, rv, price_change_pct, efficiency)

            so_score = short_opportunity_score(rsi, funding, oi_delta, bearish_cvd_div)

            di_score = distribution_score(price_change_pct, oi_delta, bearish_cvd_div)

            lt_score = long_trap_score(rsi, funding, rv, oi_delta)

            sme_score = smart_money_exit_score(
                oi_delta, efficiency, bearish_cvd_div, funding
            )

            rp_score = reversal_probability_score(rsi, rv, funding, price_change_pct)

            signal = classify_from_score(score, rsi, funding, rv, oi_delta)

            risk_label = get_risk_label(oi, volume_24h)

            trend = trend_score(rsi, rv)

            positioning = positioning_score(oi, funding, oi_delta)

            absorption = absorption_score(price_change_pct, oi_delta, cvd_signal)

            fscore = final_score(trend, positioning, absorption)

            signal_prom = classify_signal(trend, positioning, absorption)

            signal2 = classify_from_score(score2, rsi, funding, rv, oi_delta)

            save_analysis_snapshot(
                symbol=symbol,
                price=price,
                rsi=rsi,
                score=score,
                bullish_exhaustion_pct=be_score,
                short_opportunity_pct=so_score,
                distribution_pct=di_score,
                long_trap_pct=lt_score,
                smart_money_exit_pct=sme_score,
                reversal_pct=rp_score,
                cvd_signal=cvd_signal,
            )

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
                        "price_direction": price_direction,
                        "price": price,
                        "fscore": fscore,
                        "trend_score": trend,
                        "positioning_score": positioning,
                        "absorption_score": absorption,
                        "signal_prom": signal_prom,
                        "cvd_signal": cvd_signal,
                        "score2": score2,
                        "signal2": signal2,
                        "be_score": be_score,
                        "so_score": so_score,
                        "di_score": di_score,
                        "lt_score": lt_score,
                        "sme_score": sme_score,
                        "rp_score": rp_score,
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
                f"PX:{item['price']:>7.2f}  "
                # f"RSI: {item['rsi']:>5.2f}  "
                f"SCO({item['signal']}):{item['score']:>5.1f}  "
                # f"Scol({item['signal2']}):{item['score2']:>5.1f}  "
                f"BE({get_be_label(item['be_score'])}): {item['be_score']:>5.1f}%  "
                # f"SO({get_so_label(item['so_score'])}): {item['so_score']:>5.1f}%  "
                # f"DI:{item['di_score']:>5.1f}  "
                f"LT({get_lt_label(item['lt_score'])}): {item['lt_score']:>5.1f}%  "
                # f"SMEx:{item['sme_score']:>5.1f}  "
                f"RP({get_rp_label(item['rp_score'])}): {item['rp_score']:>5.1f}%  "
                f"CVD({get_cvd_label(item['cvd_signal'])})  "
                # ------------------------------------------------------------
                # f"Trend: {item['trend_score']:>4.1f}  "
                # f"Positioning: {item['positioning_score']:>4.1f}  "
                # f"Absorption: {item['absorption_score']:>4.1f}  "
                # f"SubScore({item['signal_prom']}): {item['fscore']:>5.1f}  "
                # f"OI({get_oi_label(item['oi'])}): ${format_number(item['oi']):>7}  "
                # f"OIΔ: {item['oi_delta']:>6.2f}%  "
                # f"{item['price_direction']} {item['symbol'][:6]:<6} "
                # f"RSI: {item['rsi']:>5.2f}  "
                # f"RVOL({get_rvol_label(item['rv'])}): {item['rv']:>4.2f}x  "
                # f"FUN({get_funding_label(item['funding'])}): {item['funding']:>7.4f}  "
                # f"OI({get_oi_label(item['oi'])}): ${format_number(item['oi']):>7} "
                # f"OIΔ: {item['oi_delta']:>6.2f}%  "
                # f"V24h({item['risk_label']}): ${format_number(item['volume_24h']):>7}  "
                # f"SCO({item['signal']}): {item['score']:>4.1f}"
            )

            print(line1)

    print("=" * 122)


if __name__ == "__main__":
    run_scanner()

    # 1. Escenario muy bajista después de una subida
    # | Métrica              | Valor |
    # | -------------------- | ----- |
    # | Bullish Exhaustion   | Alto  |
    # | Long Trap            | Alto  |
    # | Reversal Probability | Alto  |
    # Interpretación:
    # La subida está agotada.
    # Los últimos compradores podrían quedar atrapados.
    # Existe alta probabilidad de giro bajista.
    # Este es el caso donde los tres pueden subir simultáneamente.

    # 2. Escenario de agotamiento sin trampa
    # | Métrica              | Valor |
    # | -------------------- | ----- |
    # | Bullish Exhaustion   | Alto  |
    # | Long Trap            | Bajo  |
    # | Reversal Probability | Medio |
    # Interpretación:
    # La tendencia alcista pierde fuerza.
    # No hay evidencia clara de compradores atrapados.
    # Puede venir una lateralización en lugar de una caída fuerte.

    # 3. Escenario de trampa temprana
    # | Métrica              | Valor |
    # | -------------------- | ----- |
    # | Bullish Exhaustion   | Medio |
    # | Long Trap            | Alto  |
    # | Reversal Probability | Alto  |
    # Interpretación:
    # Aún queda energía alcista.
    # Pero el mercado muestra señales de falsa ruptura.
    # Hay riesgo de caída aunque el agotamiento no sea extremo.

    # Lectura conjunta práctica
    # Una forma útil de verlo es:

    # Bullish Exhaustion → "¿La subida se está quedando sin combustible?"
    # Long Trap → "¿Los compradores recientes están siendo engañados?"
    # Reversal Probability → "¿Qué tan probable es que cambie la dirección?"
    # Combinación más potente para detectar techo

    # Bullish Exhaustion > 80
    # Long Trap > 70
    # Reversal Probability > 70
