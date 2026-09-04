import os
import time
from datetime import datetime, timezone

import requests

# ============================================================
# Gold Signal Bot - BUY / SELL / HOLD
# Market: XAU/USD
# Timeframe: 5 minutes
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"
CHECK_INTERVAL = 300          # 5 minutes
PRICE_CHANGE_THRESHOLD = 0.50

# Signal settings
RSI_BUY_LEVEL = 55
RSI_SELL_LEVEL = 45

# Do not send the same signal repeatedly.
last_signal = None


def log(message):
    """Print a timestamped message for Railway Logs."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


def get_candles():
    """Get recent 5-minute XAU/USD candles from Twelve Data."""
    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": 50,
        "apikey": API_KEY,
        "format": "JSON",
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        if "status" in data and data["status"] == "error":
            log(f"Twelve Data API Error: {data}")
            return None

        values = data.get("values")
        if not values or len(values) < 22:
            log("Not enough candle data received.")
            return None

        # Twelve Data normally returns newest candle first.
        values = list(reversed(values))

        candles = []
        for item in values:
            candles.append({
                "datetime": item["datetime"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
            })

        return candles

    except Exception as e:
        log(f"Error getting market data: {e}")
        return None


def ema(values, period):
    """Calculate Exponential Moving Average."""
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (price - result) * multiplier + result

    return result


def rsi(values, period=14):
    """Calculate RSI using Wilder-style smoothing."""
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    """Calculate Average True Range."""
    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    # Simple average of the latest period for a stable first version.
    return sum(true_ranges[-period:]) / period


def calculate_signal(candles):
    """Calculate BUY / SELL / HOLD from EMA, RSI and ATR."""
    closes = [c["close"] for c in candles]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    current_price = closes[-1]
    current_rsi = rsi(closes, 14)
    current_atr = atr(candles, 14)

    if None in (ema9, ema21, current_rsi, current_atr):
        return None

    # Conservative confirmation:
    # BUY = EMA9 above EMA21 + price above EMA9 + RSI >= 55
    # SELL = EMA9 below EMA21 + price below EMA9 + RSI <= 45
    # Otherwise HOLD.
    if (
        ema9 > ema21
        and current_price > ema9
        and current_rsi >= RSI_BUY_LEVEL
    ):
        signal = "BUY"

    elif (
        ema9 < ema21
        and current_price < ema9
        and current_rsi <= RSI_SELL_LEVEL
    ):
        signal = "SELL"

    else:
        signal = "HOLD"

    return {
        "signal": signal,
        "price": current_price,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": current_rsi,
        "atr": current_atr,
        "candle_time": candles[-1]["datetime"],
    }


def send_message(text):
    """Send a message to Telegram."""
    if not BOT_TOKEN or not CHAT_ID:
        log("ERROR: BOT_TOKEN or CHAT_ID is missing.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
    }

    try:
        response = requests.post(url, data=payload, timeout=20)
        data = response.json()

        if response.ok and data.get("ok"):
            log("Telegram message sent.")
            return True

        log(f"Telegram API Error: {data}")
        return False

    except Exception as e:
        log(f"Telegram send error: {e}")
        return False


def format_signal(result):
    """Create a readable Telegram signal message."""
    signal = result["signal"]

    if signal == "BUY":
        title = "🟢 GOLD SIGNAL — BUY"
    elif signal == "SELL":
        title = "🔴 GOLD SIGNAL — SELL"
    else:
        title = "⚪ GOLD SIGNAL — HOLD"

    return (
        f"{title}\n\n"
        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n"
        f"Price: {result['price']:.2f}\n"
        f"EMA 9: {result['ema9']:.2f}\n"
        f"EMA 21: {result['ema21']:.2f}\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"ATR: {result['atr']:.2f}\n"
        f"Candle: {result['candle_time']}\n\n"
        f"Signal: {signal}\n"
        f"⚠️ This is a market analysis signal, not an automatic trade."
    )


def main():
    global last_signal

    log("========================================")
    log("Gold Signal Bot Started...")
    log(f"Symbol: {SYMBOL}")
    log(f"Timeframe: {INTERVAL}")
    log("Checking market every 5 minutes.")
    log(f"BUY RSI level: {RSI_BUY_LEVEL}")
    log(f"SELL RSI level: {RSI_SELL_LEVEL}")
    log("========================================")

    # Basic configuration check
    if not BOT_TOKEN:
        log("ERROR: BOT_TOKEN is not configured.")
    if not CHAT_ID:
        log("ERROR: CHAT_ID is not configured.")
    if not API_KEY:
        log("ERROR: API_KEY is not configured.")

    while True:
        try:
            candles = get_candles()

            if candles:
                result = calculate_signal(candles)

                if result:
                    signal = result["signal"]

                    log(f"Current gold price: {result['price']:.2f}")
                    log(f"EMA 9: {result['ema9']:.2f}")
                    log(f"EMA 21: {result['ema21']:.2f}")
                    log(f"RSI: {result['rsi']:.2f}")
                    log(f"ATR: {result['atr']:.2f}")
                    log(f"Signal: {signal}")

                    # Send only when the signal changes.
                    if signal != last_signal:
                        message = format_signal(result)

                        if send_message(message):
                            last_signal = signal
                            log(f"New signal sent: {signal}")
                    else:
                        log(f"Signal unchanged ({signal}). No Telegram message sent.")

            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            log(f"Main loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
