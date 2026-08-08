import os
import time
import requests
from datetime import datetime, timezone

# =========================
# SETTINGS
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

CHECK_EVERY = 300          # 5 minutes
MIN_CANDLES = 60

RSI_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
ATR_PERIOD = 14

ATR_SL_MULTIPLIER = 1.5
ATR_TP_MULTIPLIER = 2.0

# جلوگیری از ارسال سیگنال تکراری
last_signal = None
last_signal_time = None


# =========================
# TELEGRAM
# =========================

def send_message(text):

    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: BOT_TOKEN or CHAT_ID is missing.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:

        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=15
        )

        if response.ok:
            print("Telegram message sent.")
            return True

        print("Telegram error:", response.text)
        return False

    except Exception as e:

        print("Telegram connection error:", e)
        return False


# =========================
# GET MARKET DATA
# =========================

def get_candles():

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": 100,
        "apikey": API_KEY,
        "timezone": "UTC"
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=20
        )

        data = response.json()

        if data.get("status") == "error":
            print("API Error:", data)
            return []

        values = data.get("values", [])

        if not values:
            print("No candle data received.")
            return []

        candles = []

        for item in values:

            candles.append({
                "datetime": item["datetime"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            })

        # قدیمی‌ترین → جدیدترین
        candles.reverse()

        return candles

    except Exception as e:

        print("Market data error:", e)
        return []


# =========================
# EMA
# =========================

def calculate_ema(prices, period):

    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


# =========================
# RSI
# =========================

def calculate_rsi(prices, period=14):

    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):

        change = prices[i] - prices[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)

        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# =========================
# ATR
# =========================

def calculate_atr(candles, period=14):

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
            abs(low - previous_close)
        )

        true_ranges.append(tr)

    atr = sum(true_ranges[:period]) / period

    for tr in true_ranges[period:]:
        atr = ((atr * (period - 1)) + tr) / period

    return atr


# =========================
# MARKET FRESHNESS
# =========================

def is_market_data_fresh(candles):

    try:

        last_datetime = candles[-1]["datetime"]

        candle_time = datetime.strptime(
            last_datetime,
            "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        age_minutes = (now - candle_time).total_seconds() / 60

        print(f"Latest candle age: {age_minutes:.1f} minutes")

        # اگر داده بیش از 30 دقیقه قدیمی باشد
        if age_minutes > 30:

            print("Market data is stale. No signal.")

            return False

        return True

    except Exception as e:

        print("Freshness check error:", e)

        return False


# =========================
# SIGNAL ENGINE
# =========================

def analyze_market(candles):

    if len(candles) < MIN_CANDLES:

        print(
            f"Not enough candles: "
            f"{len(candles)}/{MIN_CANDLES}"
        )

        return None

    if not is_market_data_fresh(candles):

        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    current_price = closes[-1]

    ema_fast = calculate_ema(
        closes,
        EMA_FAST
    )

    ema_slow = calculate_ema(
        closes,
        EMA_SLOW
    )

    rsi = calculate_rsi(
        closes,
        RSI_PERIOD
    )

    atr = calculate_atr(
        candles,
        ATR_PERIOD
    )

    if None in (
        ema_fast,
        ema_slow,
        rsi,
        atr
    ):

        print("Indicator calculation failed.")

        return None

    print(f"Current Price: {current_price:.2f}")
    print(f"EMA {EMA_FAST}: {ema_fast:.2f}")
    print(f"EMA {EMA_SLOW}: {ema_slow:.2f}")
    print(f"RSI: {rsi:.2f}")
    print(f"ATR: {atr:.2f}")

    # =========================
    # BUY CONDITION
    # =========================

    if (
        ema_fast > ema_slow
        and rsi >= 55
        and rsi < 70
        and current_price > ema_fast
    ):

        signal = "BUY"

        entry = current_price

        stop_loss = entry - (
            atr * ATR_SL_MULTIPLIER
        )

        take_profit = entry + (
            atr * ATR_TP_MULTIPLIER
        )

        return {
            "signal": signal,
            "price": current_price,
            "entry": entry,
            "sl": stop_loss,
            "tp": take_profit,
            "rsi": rsi,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "atr": atr
        }

    # =========================
    # SELL CONDITION
    # =========================

    if (
        ema_fast < ema_slow
        and rsi <= 45
        and rsi > 30
        and current_price < ema_fast
    ):

        signal = "SELL"

        entry = current_price

        stop_loss = entry + (
            atr * ATR_SL_MULTIPLIER
        )

        take_profit = entry - (
            atr * ATR_TP_MULTIPLIER
        )

        return {
            "signal": signal,
            "price": current_price,
            "entry": entry,
            "sl": stop_loss,
            "tp": take_profit,
            "rsi": rsi,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "atr": atr
        }

    print("No trading signal.")

    return None


# =========================
# SIGNAL MESSAGE
# =========================

def create_signal_message(result):

    signal = result["signal"]

    if signal == "BUY":

        emoji = "🟢"
        direction = "BUY / خرید"

    else:

        emoji = "🔴"
        direction = "SELL / فروش"

    message = f"""
{emoji} GOLD TRADING SIGNAL

━━━━━━━━━━━━━━

Symbol: XAU/USD
Timeframe: 5 Minutes

Signal:
{direction}

━━━━━━━━━━━━━━

💰 Entry:
{result["entry"]:.2f}

🛑 Stop Loss:
{result["sl"]:.2f}

🎯 Take Profit:
{result["tp"]:.2f}

━━━━━━━━━━━━━━

📊 Indicators

EMA 9:
{result["ema_fast"]:.2f}

EMA 21:
{result["ema_slow"]:.2f}

RSI:
{result["rsi"]:.2f}

ATR:
{result["atr"]:.2f}

━━━━━━━━━━━━━━

⚠️ Automated technical signal
Not financial advice.
"""

    return message


# =========================
# MAIN LOOP
# =========================

print("Gold Signal Bot Started...")
print("Symbol:", SYMBOL)
print("Timeframe:", INTERVAL)
print("Checking market every 5 minutes...")
print("Price change threshold replaced by technical signal engine.")


while True:

    try:

        candles = get_candles()

        if candles:

            result = analyze_market(candles)

            if result:

                current_signal = result["signal"]

                print(
                    f"Detected signal: "
                    f"{current_signal}"
                )

                # فقط در صورت تغییر سیگنال پیام بفرست
                if current_signal != last_signal:

                    message = create_signal_message(
                        result
                    )

                    if send_message(message):

                        last_signal = current_signal

                        last_signal_time = datetime.now(
                            timezone.utc
                        )

                else:

                    print(
                        "Same signal as previous. "
                        "Message not sent."
                    )

        time.sleep(CHECK_EVERY)

    except Exception as e:

        print("Main loop error:", e)

        time.sleep(60)
