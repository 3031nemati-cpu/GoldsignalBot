import os
import time
import requests
from datetime import datetime, timezone

# =========================================================
# GOLD SIGNAL BOT - PROFESSIONAL ANALYSIS VERSION
# XAU/USD | 5 MINUTES
# =========================================================

# -------------------------
# ENVIRONMENT VARIABLES
# -------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

# -------------------------
# MARKET SETTINGS
# -------------------------

SYMBOL = "XAU/USD"
INTERVAL = "5min"

# بررسی بازار هر 5 دقیقه
CHECK_INTERVAL = 300

# تعداد کندل‌های دریافت‌شده
OUTPUT_SIZE = 100

# حداقل امتیاز برای BUY / SELL
MIN_TREND_SCORE = 60

# ATR multipliers
SL_ATR_MULTIPLIER = 1.5

TP1_ATR_MULTIPLIER = 1.0
TP2_ATR_MULTIPLIER = 2.0
TP3_ATR_MULTIPLIER = 3.0

# -------------------------
# FEATURES
# -------------------------

BREAKOUT_ENABLED = True
FALSE_BREAKOUT_ENABLED = True
TREND_WEAKENING_ENABLED = True

# Paper Trading
# True = فقط شبیه‌سازی
# False = همچنان هیچ معامله واقعی انجام نمی‌شود
PAPER_TRADING = True

# جلوگیری از ارسال پیام تکراری
LAST_SIGNAL = None
LAST_CANDLE = None

# معامله شبیه‌سازی‌شده فعلی
PAPER_TRADE = None


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: BOT_TOKEN or CHAT_ID is missing.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code == 200:
            print("Telegram message sent.")
            return True

        print("Telegram error:", response.text)
        return False

    except Exception as e:
        print("Telegram connection error:", e)
        return False


# =========================================================
# TWELVE DATA
# =========================================================

def get_candles():

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": OUTPUT_SIZE,
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

        if response.status_code != 200:
            print("HTTP ERROR:", response.status_code)
            print(data)
            return None

        if data.get("status") == "error":
            print("API ERROR:", data)
            return None

        values = data.get("values")

        if not values:
            print("No candle data received.")
            return None

        candles = []

        for item in values:

            try:

                candle = {
                    "datetime": item["datetime"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"])
                }

                candles.append(candle)

            except Exception:
                continue

        candles.reverse()

        return candles

    except Exception as e:

        print("Market data error:", e)
        return None


# =========================================================
# EMA
# =========================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for price in values[period:]:

        ema = (
            (price - ema) * multiplier
        ) + ema

    return ema


# =========================================================
# RSI
# =========================================================

def calculate_rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):

        change = values[i] - values[i - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(values)):

        change = values[i] - values[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = (
            (avg_gain * (period - 1)) + gain
        ) / period

        avg_loss = (
            (avg_loss * (period - 1)) + loss
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# =========================================================
# ATR
# =========================================================

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

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period]) / period

    for tr in true_ranges[period:]:

        atr = (
            (atr * (period - 1)) + tr
        ) / period

    return atr


# =========================================================
# SUPPORT / RESISTANCE
# =========================================================

def calculate_support_resistance(candles, lookback=20):

    if len(candles) < lookback:
        return None, None

    recent = candles[-lookback:]

    support = min(
        candle["low"] for candle in recent
    )

    resistance = max(
        candle["high"] for candle in recent
    )

    return support, resistance


# =========================================================
# TREND ANALYSIS
# =========================================================

def analyze_market(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

    current_price = closes[-1]

    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)

    rsi = calculate_rsi(closes, 14)

    atr = calculate_atr(candles, 14)

    support, resistance = calculate_support_resistance(
        candles,
        20
    )

    if None in (
        ema9,
        ema21,
        rsi,
        atr,
        support,
        resistance
    ):
        return None

    # -----------------------------------------------------
    # EMA TREND
    # -----------------------------------------------------

    bullish_points = 0
    bearish_points = 0

    if ema9 > ema21:
        bullish_points += 30

    elif ema9 < ema21:
        bearish_points += 30

    # -----------------------------------------------------
    # PRICE POSITION
    # -----------------------------------------------------

    if current_price > ema9:
        bullish_points += 15

    else:
        bearish_points += 15

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    if 52 <= rsi <= 68:
        bullish_points += 20

    elif 32 <= rsi <= 48:
        bearish_points += 20

    elif rsi > 70:
        bearish_points -= 10

    elif rsi < 30:
        bearish_points -= 10

    # -----------------------------------------------------
    # MOMENTUM
    # -----------------------------------------------------

    if len(closes) >= 6:

        momentum = current_price - closes[-6]

        if momentum > 0:
            bullish_points += 15

        elif momentum < 0:
            bearish_points += 15

    # -----------------------------------------------------
    # BREAKOUT
    # -----------------------------------------------------

    breakout_up = False
    breakout_down = False

    previous_candles = candles[-21:-1]

    previous_resistance = max(
        candle["high"]
        for candle in previous_candles
    )

    previous_support = min(
        candle["low"]
        for candle in previous_candles
    )

    if BREAKOUT_ENABLED:

        if current_price > previous_resistance:
            breakout_up = True
            bullish_points += 20

        elif current_price < previous_support:
            breakout_down = True
            bearish_points += 20

    # -----------------------------------------------------
    # FINAL SCORE
    # -----------------------------------------------------

    bullish_score = max(
        0,
        min(100, bullish_points)
    )

    bearish_score = max(
        0,
        min(100, bearish_points)
    )

    if bullish_score >= MIN_TREND_SCORE:

        signal = "BUY"
        trend = "Strong Bullish"
        score = bullish_score

    elif bearish_score >= MIN_TREND_SCORE:

        signal = "SELL"
        trend = "Strong Bearish"
        score = bearish_score

    elif bullish_score > bearish_score:

        signal = "HOLD"
        trend = "Bullish"
        score = bullish_score

    elif bearish_score > bullish_score:

        signal = "HOLD"
        trend = "Bearish"
        score = bearish_score

    else:

        signal = "HOLD"
        trend = "Neutral"
        score = 50

    # -----------------------------------------------------
    # REVERSAL / CAUTION ZONE
    # -----------------------------------------------------

    caution_low = min(
        ema9,
        ema21
    )

    caution_high = max(
        ema9,
        ema21
    )

    # -----------------------------------------------------
    # ENTRY / SL / TP
    # -----------------------------------------------------

    entry = current_price

    if signal == "BUY":

        stop_loss = entry - (
            atr * SL_ATR_MULTIPLIER
        )

        tp1 = entry + (
            atr * TP1_ATR_MULTIPLIER
        )

        tp2 = entry + (
            atr * TP2_ATR_MULTIPLIER
        )

        tp3 = entry + (
            atr * TP3_ATR_MULTIPLIER
        )

    elif signal == "SELL":

        stop_loss = entry + (
            atr * SL_ATR_MULTIPLIER
        )

        tp1 = entry - (
            atr * TP1_ATR_MULTIPLIER
        )

        tp2 = entry - (
            atr * TP2_ATR_MULTIPLIER
        )

        tp3 = entry - (
            atr * TP3_ATR_MULTIPLIER
        )

    else:

        stop_loss = None
        tp1 = None
        tp2 = None
        tp3 = None

    # -----------------------------------------------------
    # CANDLE
    # -----------------------------------------------------

    candle_time = candles[-1]["datetime"]

    return {

        "price": current_price,

        "ema9": ema9,

        "ema21": ema21,

        "rsi": rsi,

        "atr": atr,

        "signal": signal,

        "trend": trend,

        "score": score,

        "entry": entry,

        "stop_loss": stop_loss,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3,

        "support": support,

        "resistance": resistance,

        "caution_low": caution_low,

        "caution_high": caution_high,

        "breakout_up": breakout_up,

        "breakout_down": breakout_down,

        "candle": candle_time
    }


# =========================================================
# SIGNAL MESSAGE
# =========================================================

def create_signal_message(result):

    signal = result["signal"]

    if signal == "BUY":

        message = (
            "🟢 GOLD SIGNAL — BUY\n\n"
            f"Symbol: {SYMBOL}\n"
            f"Timeframe: {INTERVAL}\n\n"

            f"💰 Entry: {result['entry']:.2f}\n\n"

            f"📊 Trend: {result['trend']}\n"
            f"🎯 Trend Score: {result['score']}/100\n\n"

            f"EMA 9: {result['ema9']:.2f}\n"
            f"EMA 21: {result['ema21']:.2f}\n"
            f"RSI: {result['rsi']:.2f}\n"
            f"ATR: {result['atr']:.2f}\n\n"

            f"🎯 TP1: {result['tp1']:.2f}\n"
            f"🎯 TP2: {result['tp2']:.2f}\n"
            f"🎯 TP3: {result['tp3']:.2f}\n\n"

            f"🛑 Stop Loss: {result['stop_loss']:.2f}\n\n"

            f"📉 Support: {result['support']:.2f}\n"
            f"📈 Resistance: {result['resistance']:.2f}\n\n"

            f"🔄 Reversal/Caution Zone:\n"
            f"{result['caution_low']:.2f} - "
            f"{result['caution_high']:.2f}\n\n"

            f"🕐 Candle: {result['candle']}\n\n"

            "⚠️ Technical analysis only — "
            "not an automatic trade."
        )

        return message

    if signal == "SELL":

        message = (
            "🔴 GOLD SIGNAL — SELL\n\n"
            f"Symbol: {SYMBOL}\n"
            f"Timeframe: {INTERVAL}\n\n"

            f"💰 Entry: {result['entry']:.2f}\n\n"

            f"📊 Trend: {result['trend']}\n"
            f"🎯 Trend Score: {result['score']}/100\n\n"

            f"EMA 9: {result['ema9']:.2f}\n"
            f"EMA 21: {result['ema21']:.2f}\n"
            f"RSI: {result['rsi']:.2f}\n"
            f"ATR: {result['atr']:.2f}\n\n"

            f"🎯 TP1: {result['tp1']:.2f}\n"
            f"🎯 TP2: {result['tp2']:.2f}\n"
            f"🎯 TP3: {result['tp3']:.2f}\n\n"

            f"🛑 Stop Loss: {result['stop_loss']:.2f}\n\n"

            f"📉 Support: {result['support']:.2f}\n"
            f"📈 Resistance: {result['resistance']:.2f}\n\n"

            f"🔄 Reversal/Caution Zone:\n"
            f"{result['caution_low']:.2f} - "
            f"{result['caution_high']:.2f}\n\n"

            f"🕐 Candle: {result['candle']}\n\n"

            "⚠️ Technical analysis only — "
            "not an automatic trade."
        )

        return message

    # HOLD

    return (
        "⚪ GOLD SIGNAL — HOLD\n\n"
        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n"
        f"Price: {result['price']:.2f}\n\n"

        f"📊 Trend: {result['trend']}\n"
        f"🎯 Trend Score: {result['score']}/100\n\n"

        f"EMA 9: {result['ema9']:.2f}\n"
        f"EMA 21: {result['ema21']:.2f}\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"ATR: {result['atr']:.2f}\n\n"

        f"📉 Support: {result['support']:.2f}\n"
        f"📈 Resistance: {result['resistance']:.2f}\n\n"

        f"🔄 Reversal/Caution Zone:\n"
        f"{result['caution_low']:.2f} - "
        f"{result['caution_high']:.2f}\n\n"

        f"🕐 Candle: {result['candle']}\n\n"

        "⏸ No high-confidence entry.\n"
        "⚠️ Technical analysis only — "
        "not an automatic trade."
    )


# =========================================================
# PAPER TRADE
# =========================================================

def start_paper_trade(result):

    global PAPER_TRADE

    if not PAPER_TRADING:
        return

    if result["signal"] not in ("BUY", "SELL"):
        return

    PAPER_TRADE = {
        "direction": result["signal"],
        "entry": result["entry"],
        "stop_loss": result["stop_loss"],
        "tp1": result["tp1"],
        "tp2": result["tp2"],
        "tp3": result["tp3"],
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False
    }

    print(
        f"PAPER TRADE STARTED: "
        f"{result['signal']} "
        f"Entry={result['entry']:.2f}"
    )


# =========================================================
# PAPER TRADE MONITOR
# =========================================================

def monitor_paper_trade(price):

    global PAPER_TRADE

    if not PAPER_TRADE:
        return

    direction = PAPER_TRADE["direction"]

    # -----------------------------------------------------
    # BUY
    # -----------------------------------------------------

    if direction == "BUY":

        if price <= PAPER_TRADE["stop_loss"]:

            send_telegram(
                "🛑 PAPER TRADE — STOP LOSS HIT\n\n"
                f"BUY Entry: {PAPER_TRADE['entry']:.2f}\n"
                f"Exit: {price:.2f}"
            )

            print("PAPER BUY STOP LOSS HIT.")

            PAPER_TRADE = None

            return

        if (
            not PAPER_TRADE["tp1_hit"]
            and price >= PAPER_TRADE["tp1"]
        ):

            PAPER_TRADE["tp1_hit"] = True

            send_telegram(
                "✅ PAPER BUY — TP1 HIT\n\n"
                f"Price: {price:.2f}\n"
                "🔒 Stop Loss can now move toward BREAK EVEN."
            )

        if (
            PAPER_TRADE["tp1_hit"]
            and not PAPER_TRADE["tp2_hit"]
            and price >= PAPER_TRADE["tp2"]
        ):

            PAPER_TRADE["tp2_hit"] = True

            send_telegram(
                "✅ PAPER BUY — TP2 HIT\n\n"
                f"Price: {price:.2f}\n"
                "📈 Trend continuation confirmed."
            )

        if (
            PAPER_TRADE["tp2_hit"]
            and not PAPER_TRADE["tp3_hit"]
            and price >= PAPER_TRADE["tp3"]
        ):

            PAPER_TRADE["tp3_hit"] = True

            send_telegram(
                "🏁 PAPER BUY — TP3 HIT\n\n"
                f"Price: {price:.2f}\n"
                "🎉 Full target reached."
            )

            PAPER_TRADE = None

    # -----------------------------------------------------
    # SELL
    # -----------------------------------------------------

    elif direction == "SELL":

        if price >= PAPER_TRADE["stop_loss"]:

            send_telegram(
                "🛑 PAPER TRADE — STOP LOSS HIT\n\n"
                f"SELL Entry: {PAPER_TRADE['entry']:.2f}\n"
                f"Exit: {price:.2f}"
            )

            print("PAPER SELL STOP LOSS HIT.")

            PAPER_TRADE = None

            return

        if (
            not PAPER_TRADE["tp1_hit"]
            and price <= PAPER_TRADE["tp1"]
        ):

            PAPER_TRADE["tp1_hit"] = True

            send_telegram(
                "✅ PAPER SELL — TP1 HIT\n\n"
                f"Price: {price:.2f}\n"
                "🔒 Stop Loss can now move toward BREAK EVEN."
            )

        if (
            PAPER_TRADE["tp1_hit"]
            and not PAPER_TRADE["tp2_hit"]
            and price <= PAPER_TRADE["tp2"]
        ):

            PAPER_TRADE["tp2_hit"] = True

            send_telegram(
                "✅ PAPER SELL — TP2 HIT\n\n"
                f"Price: {price:.2f}\n"
                "📉 Trend continuation confirmed."
            )

        if (
            PAPER_TRADE["tp2_hit"]
            and not PAPER_TRADE["tp3_hit"]
            and price <= PAPER_TRADE["tp3"]
        ):

            PAPER_TRADE["tp3_hit"] = True

            send_telegram(
                "🏁 PAPER SELL — TP3 HIT\n\n"
                f"Price: {price:.2f}\n"
                "🎉 Full target reached."
            )

            PAPER_TRADE = None


# =========================================================
# MARKET STATUS
# =========================================================

def print_market_status(result):

    print("=" * 60)

    print(
        f"Price={result['price']:.2f} | "
        f"EMA9={result['ema9']:.2f} | "
        f"EMA21={result['ema21']:.2f}"
    )

    print(
        f"RSI={result['rsi']:.2f} | "
        f"ATR={result['atr']:.2f}"
    )

    print(
        f"Signal={result['signal']} | "
        f"Trend={result['trend']} | "
        f"Score={result['score']}/100"
    )

    print(
        f"Support={result['support']:.2f} | "
        f"Resistance={result['resistance']:.2f}"
    )

    if result["signal"] in ("BUY", "SELL"):

        print(
            f"Entry={result['entry']:.2f} | "
            f"SL={result['stop_loss']:.2f}"
        )

        print(
            f"TP1={result['tp1']:.2f} | "
            f"TP2={result['tp2']:.2f} | "
            f"TP3={result['tp3']:.2f}"
        )

    print(
        f"Candle={result['candle']}"
    )

    print("=" * 60)


# =========================================================
# MAIN LOOP
# =========================================================

def main():

    global LAST_SIGNAL
    global LAST_CANDLE

    print("=" * 60)
    print("GOLD SIGNAL BOT STARTED")
    print("=" * 60)

    print(f"Symbol: {SYMBOL}")
    print(f"Timeframe: {INTERVAL}")
    print(f"Minimum Trend Score: {MIN_TREND_SCORE}/100")
    print(f"Breakout: {'ON' if BREAKOUT_ENABLED else 'OFF'}")
    print(
        f"False Breakout: "
        f"{'ON' if FALSE_BREAKOUT_ENABLED else 'OFF'}"
    )
    print(
        f"Trend Weakening Alerts: "
        f"{'ON' if TREND_WEAKENING_ENABLED else 'OFF'}"
    )

    print(
        f"Paper Trading: "
        f"{'ON' if PAPER_TRADING else 'OFF'}"
    )

    print("Automatic trading: DISABLED")
    print("=" * 60)

    send_telegram(
        "🤖 GOLD SIGNAL BOT STARTED\n\n"
        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n\n"
        "🟢 BUY/SELL/HOLD: ACTIVE\n"
        "🛑 Stop Loss: ACTIVE\n"
        "🎯 TP1/TP2/TP3: ACTIVE\n"
        "📊 Trend Score: ACTIVE\n"
        "📈 Support/Resistance: ACTIVE\n"
        "🧪 Paper Trading: ACTIVE\n\n"
        "⚠️ Automatic trading is DISABLED."
    )

    while True:

        try:

            candles = get_candles()

            if not candles:

                print("No market data. Retrying...")
                time.sleep(60)
                continue

            result = analyze_market(candles)

            if not result:

                print("Unable to calculate indicators.")
                time.sleep(60)
                continue

            print_market_status(result)

            # ---------------------------------------------
            # PAPER TRADE MONITOR
            # ---------------------------------------------

            monitor_paper_trade(
                result["price"]
            )

            # ---------------------------------------------
            # NEW CANDLE CHECK
            # ---------------------------------------------

            candle = result["candle"]

            if candle == LAST_CANDLE:

                print(
                    "Same candle - "
                    "waiting for next 5-minute candle."
                )

                time.sleep(CHECK_INTERVAL)
                continue

            LAST_CANDLE = candle

            # ---------------------------------------------
            # SEND SIGNAL
            # ---------------------------------------------

            signal = result["signal"]

            # ارسال BUY / SELL
            if signal in ("BUY", "SELL"):

                # جلوگیری از تکرار پشت سر هم
                if signal != LAST_SIGNAL:

                    message = create_signal_message(
                        result
                    )

                    if send_telegram(message):

                        start_paper_trade(
                            result
                        )

                    LAST_SIGNAL = signal

                else:

                    print(
                        f"Repeated {signal} signal "
                        "not sent."
                    )

            else:

                # HOLD
                # فقط اگر سیگنال قبلی BUY/SELL بوده
                # HOLD را برای اعلام خروج/احتیاط ارسال می‌کنیم.

                if LAST_SIGNAL in ("BUY", "SELL"):

                    message = create_signal_message(
                        result
                    )

                    send_telegram(message)

                    LAST_SIGNAL = "HOLD"

            time.sleep(CHECK_INTERVAL)

        except Exception as e:

            print(
                "MAIN LOOP ERROR:",
                str(e)
            )

            time.sleep(60)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
