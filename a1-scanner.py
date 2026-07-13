import requests
import pandas as pd
import numpy as np
from textwrap import dedent
import argparse


class HyperliquidAnalyzer:

    def __init__(self):
        self.url = "https://api.hyperliquid.xyz/info"

    def get_candles(self, coin, interval="4h", limit=200):

        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": 0},
        }

        try:
            response = requests.post(self.url, json=payload, timeout=10)
        except requests.exceptions.RequestException as e:
            print(f"Error de conexión: {e}")
            return None

        if response.status_code != 200:
            raise Exception(response.text)

        data = response.json()

        candles = data[-limit:]

        df = pd.DataFrame(candles)

        df["open"] = df["o"].astype(float)
        df["high"] = df["h"].astype(float)
        df["low"] = df["l"].astype(float)
        df["close"] = df["c"].astype(float)
        df["volume"] = df["v"].astype(float)

        return df

    @staticmethod
    def ema(series, period):
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def adx(df, period=14):

        high = df["high"]
        low = df["low"]
        close = df["close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()

        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0)

        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0)

        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.rolling(period).mean()

        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr

        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr

        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100

        return dx.rolling(period).mean()

    def analyze(self, coin):

        df = self.get_candles(coin)

        if df is None:
            return

        df["ema7"] = self.ema(df["close"], 7)
        df["ema25"] = self.ema(df["close"], 25)
        df["ema99"] = self.ema(df["close"], 99)

        df["velocity"] = df["close"].diff()
        df["acceleration"] = df["velocity"].diff()

        df["adx"] = self.adx(df)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        trend_bearish = last["ema7"] < last["ema25"] and last["ema25"] < last["ema99"]

        accelerating_drop = last["velocity"] < 0 and last["acceleration"] < 0

        decelerating_drop = last["velocity"] < 0 and last["acceleration"] > 0

        adx_strength = last["adx"] > 25
        adx_falling = last["adx"] < prev["adx"]

        print(f"\n==== {coin} =====================================\n")
        print(f"Precio actual: {last['close']:.6f}")

        if trend_bearish:
            print("Tendencia: BAJISTA")
        else:
            print("Tendencia: NO BAJISTA")

        if last["velocity"] > 0:
            print("Caída: DETENIDA / REBOTE")
        elif accelerating_drop:
            print("Caída: ACELERANDO")
        elif decelerating_drop:
            print("Caída: DESACELERANDO")
        else:
            print("Caída: CONSOLIDANDO")

        #print(f"\n")
        print(f"ADX: {last['adx']:.2f}")

        print(dedent("""
        ADX < 20   → mercado lateral
        ADX 20-25  → tendencia empezando
        ADX > 25   → tendencia fuerte
        ADX > 40   → tendencia muy fuerte
        ADX > 60   → tendencia extremadamente fuerte
        """))

        if adx_strength:
            print("Fuerza tendencia: FUERTE")
        else:
            print("Fuerza tendencia: DÉBIL")

        # tabla = [
        #     [last['velocity'], last['acceleration'], interpretacion]
        # ]
        # print(tabulate(
        #     tabla,
        #     headers=["Velocidad", "Aceleración", "Interpretación"],
        #     floatfmt=".7f",
        #     tablefmt="grid"
        # ))

        print(dedent(f"""
        --------------------------------------------------------------------
           Velocidad   |   Aceleración  |        Interpretación
        --------------------------------------------------------------------
        {last['velocity']:14.7f} | {last['acceleration']:14.7f} |
        --------------------------------------------------------------------
        +              | +              | Rebote fuerte
        +              | -              | Sigue subiendo pero pierde fuerza
        -              | +              | Sigue cayendo pero desacelera
        -              | -              | Caída acelerándose
        --------------------------------------------------------------------
        """))

        print("\n======== Diagnóstico =========================\n")

        if trend_bearish and accelerating_drop and adx_strength:
            print("SHORT: Mantener")
            print("La presión vendedora sigue aumentando.")

        elif trend_bearish and decelerating_drop:
            print("SHORT: Precaución")
            print("La caída pierde fuerza.")

        elif trend_bearish and adx_falling:
            print("SHORT: Considerar salida parcial")
            print("La tendencia sigue bajista pero se debilita.")

        else:
            print("Sin ventaja clara para mantener short.")

        print("\n")


if __name__ == "__main__":

    # symbol = input("Coin (ADA, BTC, ETH, SOL...): ").upper()

    # analyzer = HyperliquidAnalyzer()
    # analyzer.analyze(symbol)

    parser = argparse.ArgumentParser(
        description="Analizador de tendencia Hyperliquid"
    )

    parser.add_argument(
        "--symbol",
        "-s",
        required=True,
        help="Símbolo a analizar (BTC, ETH, ADA, SOL...)"
    )

    args = parser.parse_args()

    analyzer = HyperliquidAnalyzer()
    analyzer.analyze(args.symbol.upper())
