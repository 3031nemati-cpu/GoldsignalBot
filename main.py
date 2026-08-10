import os
import time
import requests

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

CHECK_SECONDS = 300
CANDLE_COUNT = 100

# Risk / Trade Management
ATR_SL_MULTIPLIER = 1.5

TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0

TRAILING_ATR = 1.2

MIN_TREND_SCORE = 60

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

TELEGRAM_URL = (
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
)

session = requests.Session()

# Current simulated position
position = None

last_candle_time = None
last_signal = None


# =========================================================
# LOG
# =========================================================

def log(message):
    print(message, flush=True)


# =========================================================
# CHECK VARIABLES
# =========================================================

def validate_environment():

    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not CHAT_ID:
        missing.append("CHAT_ID")

    if not API_KEY:
        missing.append("API_KEY")

    if missing:

        raise RuntimeError(
            "Missing Railway variables: "
            + ", ".join(missing)
        )


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

    try:

        response = session.post(
            TELEGRAM_URL,
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):

            log(
                f"Telegram API error: {data}"
            )

            return False

        log("Telegram message sent.")

        return True

    except Exception as error:

        log(
            f"Telegram send error: {error}"
        )

        return False


# =========================================================
# GET GOLD CANDLES
# =========================================================

def get_candles():

    params = {

        "symbol": SYMBOL,

        "interval": INTERVAL,

        "outputsize": CANDLE_COUNT,

        "apikey": API_KEY,

        "format": "JSON"
    }

    try:

        response = session.get(
            TWELVEDATA_URL,
            params=params,
            timeout=20
        )

        data = response.json()

        if response.status_code != 200:

            log(
                f"TwelveData HTTP error: {data}"
            )

            return []

        if "values" not in data:

            log(
                f"TwelveData API error: {data}"
            )

            return []

        candles = []

        for item in data["values"]:

            try:

                candles.append({

                    "datetime":
                        item["datetime"],

                    "open":
                        float(item["open"]),

                    "high":
                        float(item["high"]),

                    "low":
                        float(item["low"]),

                    "close":
                        float(item["close"])
                })

            except Exception:

                continue

        candles.sort(
            key=lambda x: x["datetime"]
        )

        return candles

    except Exception as error:

        log(
            f"Market data error: {error}"
        )

        return []


# =========================================================
# EMA
# =========================================================

def calculate_ema(values, period):

    if len(values) < period:

        return None

    multiplier = 2 / (period + 1)

    ema_value = (
        sum(values[:period])
        / period
    )

    for price in values[period:]:

        ema_value = (
            (price - ema_value)
            * multiplier
            + ema_value
        )

    return ema_value


# =========================================================
# RSI
# =========================================================

def calculate_rsi(values, period=14):

    if len(values) < period + 1:

        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    average_gain = (
        sum(gains[:period])
        / period
    )

    average_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(period, len(gains)):

        average_gain = (
            (
                average_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        average_loss = (
            (
                average_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

    if average_loss == 0:

        return 100.0

    relative_strength = (
        average_gain
        / average_loss
    )

    return (
        100
        - (
            100
            / (1 + relative_strength)
        )
    )


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

        previous_close = (
            candles[i - 1]["close"]
        )

        true_range = max(

            high - low,

            abs(
                high
                - previous_close
            ),

            abs(
                low
                - previous_close
            )
        )

        true_ranges.append(
            true_range
        )

    atr_value = (
        sum(true_ranges[:period])
        / period
    )

    for tr in true_ranges[period:]:

        atr_value = (
            (
                atr_value
                * (period - 1)
            )
            + tr
        ) / period

    return atr_value


# =========================================================
# ANALYZE MARKET
# =========================================================

def analyze_market(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

    ema9 = calculate_ema(
        closes,
        9
    )

    ema21 = calculate_ema(
        closes,
        21
    )

    rsi = calculate_rsi(
        closes,
        14
    )

    atr = calculate_atr(
        candles,
        14
    )

    if None in (
        ema9,
        ema21,
        rsi,
        atr
    ):

        return None

    price = closes[-1]

    recent = candles[-20:]

    support = min(
        candle["low"]
        for candle in recent
    )

    resistance = max(
        candle["high"]
        for candle in recent
    )

    # =====================================================
    # TREND SCORE
    # =====================================================

    score = 50

    # EMA direction
    if ema9 > ema21:

        score += 15

    else:

        score -= 15

    # Price relative to EMA9
    if price > ema9:

        score += 10

    else:

        score -= 10

    # RSI
    if rsi >= 55:

        score += 15

    elif rsi <= 45:

        score -= 15

    else:

        score += int(
            (rsi - 50) * 1.5
        )

    # EMA distance
    ema_distance = (
        abs(ema9 - ema21)
        / atr
    )

    if ema_distance >= 0.75:

        if ema9 > ema21:

            score += 10

        else:

            score -= 10

    score = max(
        0,
        min(100, score)
    )

    # =====================================================
    # SIGNAL
    # =====================================================

    if (

        score >= MIN_TREND_SCORE

        and ema9 > ema21

        and price > ema9

        and rsi >= 52

    ):

        signal = "BUY"

        if score >= 75:

            trend = "Strong Bullish"

        else:

            trend = "Bullish"

    elif (

        score <= 100 - MIN_TREND_SCORE

        and ema9 < ema21

        and price < ema9

        and rsi <= 48

    ):

        signal = "SELL"

        if score <= 25:

            trend = "Strong Bearish"

        else:

            trend = "Bearish"

    else:

        signal = "HOLD"

        trend = "Neutral"

    # =====================================================
    # CAUTION ZONE
    # =====================================================

    caution_low = (
        ema21
        - (0.5 * atr)
    )

    caution_high = (
        ema21
        + (0.5 * atr)
    )

    return {

        "price": price,

        "ema9": ema9,

        "ema21": ema21,

        "rsi": rsi,

        "atr": atr,

        "score": score,

        "signal": signal,

        "trend": trend,

        "support": support,

        "resistance": resistance,

        "caution_low":
            caution_low,

        "caution_high":
            caution_high,

        "candle":
            candles[-1]["datetime"]
    }


# =========================================================
# CREATE TP / SL
# =========================================================

def create_levels(info):

    price = info["price"]

    atr = info["atr"]

    signal = info["signal"]

    if signal == "BUY":

        stop_loss = (
            price
            - ATR_SL_MULTIPLIER * atr
        )

        tp1 = (
            price
            + TP1_ATR * atr
        )

        tp2 = (
            price
            + TP2_ATR * atr
        )

        tp3 = (
            price
            + TP3_ATR * atr
        )

    elif signal == "SELL":

        stop_loss = (
            price
            + ATR_SL_MULTIPLIER * atr
        )

        tp1 = (
            price
            - TP1_ATR * atr
        )

        tp2 = (
            price
            - TP2_ATR * atr
        )

        tp3 = (
            price
            - TP3_ATR * atr
        )

    else:

        return None

    return {

        "side": signal,

        "entry": price,

        "sl": stop_loss,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3,

        "tp1_hit": False,

        "tp2_hit": False,

        "tp3_hit": False,

        "break_even": False,

        "trail": stop_loss,

        "opened_candle":
            info["candle"]
    }


# =========================================================
# FORMAT PRICE
# =========================================================

def price_format(value):

    return f"{value:.2f}"


# =========================================================
# SIGNAL MESSAGE
# =========================================================

def build_signal_message(
    info,
    levels=None
):

    signal = info["signal"]

    if signal == "BUY":

        icon = "🟢"

    elif signal == "SELL":

        icon = "🔴"

    else:

        icon = "⚪"

    message = (

        f"{icon} GOLD SIGNAL — {signal}\n"

        f"Symbol: {SYMBOL}\n"

        f"Timeframe: {INTERVAL}\n"

        f"Price: "
        f"{price_format(info['price'])}\n"

        f"📊 Trend: "
        f"{info['trend']}\n"

        f"Trend Score: "
        f"{info['score']}/100\n"

        f"EMA 9: "
        f"{price_format(info['ema9'])}\n"

        f"EMA 21: "
        f"{price_format(info['ema21'])}\n"

        f"RSI: "
        f"{info['rsi']:.2f}\n"

        f"ATR: "
        f"{price_format(info['atr'])}\n"
    )

    if levels:

        message += (

            f"💰 Entry: "
            f"{price_format(levels['entry'])}\n"

            f"🎯 TP1: "
            f"{price_format(levels['tp1'])}\n"

            f"🎯 TP2: "
            f"{price_format(levels['tp2'])}\n"

            f"🎯 TP3: "
            f"{price_format(levels['tp3'])}\n"

            f"🛑 Stop Loss: "
            f"{price_format(levels['sl'])}\n"
        )

    message += (

        f"📉 Support: "
        f"{price_format(info['support'])}\n"

        f"📈 Resistance: "
        f"{price_format(info['resistance'])}\n"

        f"🔄 Reversal/Caution Zone: "

        f"{price_format(info['caution_low'])}"

        f" - "

        f"{price_format(info['caution_high'])}\n"

        f"Candle: "
        f"{info['candle']}\n"

        f"⚠️ Technical analysis only — "
        f"not an automatic trade."
    )

    return message


# =========================================================
# MANAGE SIMULATED POSITION
# =========================================================

def manage_position(info):

    global position

    if position is None:

        return

    side = position["side"]

    price = info["price"]

    atr = info["atr"]

    # =====================================================
    # BUY MANAGEMENT
    # =====================================================

    if side == "BUY":

        if (
            price >= position["tp3"]
            and not position["tp3_hit"]
        ):

            position["tp3_hit"] = True

            send_telegram(

                "🏁 TP3 HIT — BUY\n"

                f"Price: "
                f"{price_format(price)}\n"

                f"Entry: "
                f"{price_format(position['entry'])}\n"

                f"TP3: "
                f"{price_format(position['tp3'])}\n"

                "📌 Simulation position closed."
            )

            position = None

            return

        if (
            price >= position["tp2"]
            and not position["tp2_hit"]
        ):

            position["tp2_hit"] = True

            send_telegram(

                "✅ TP2 HIT — BUY\n"

                f"Price: "
                f"{price_format(price)}\n"

                f"TP2: "
                f"{price_format(position['tp2'])}"
            )

        if (
            price >= position["tp1"]
            and not position["tp1_hit"]
        ):

            position["tp1_hit"] = True

            position["break_even"] = True

            position["trail"] = max(

                position["trail"],

                position["entry"]
            )

            send_telegram(

                "✅ TP1 HIT — BUY\n"

                f"Price: "
                f"{price_format(price)}\n"

                f"TP1: "
                f"{price_format(position['tp1'])}\n"

                "🔒 Stop Loss moved to "
                "BREAK-EVEN\n"

                f"New SL: "
                f"{price_format(position['entry'])}"
            )

        # Trailing stop after TP1
        if position["tp1_hit"]:

            new_trail = (
                price
                - TRAILING_ATR * atr
            )

            if new_trail > position["trail"]:

                position["trail"] = new_trail

        # Stop / trailing hit
        if price <= position["trail"]:

            send_telegram(

                "🛑 STOP / TRAILING HIT — BUY\n"

                f"Price: "
                f"{price_format(price)}\n"

                f"Exit: "
                f"{price_format(position['trail'])}\n"

                "📌 Simulation position closed."
            )

            position = None

    # =====================================================
    # SELL MANAGEMENT
    # =====================================================

    elif side == "SELL":

        if (
            price <= position["tp3"]
            and not position["tp3_hit"]
        ):

            position["tp3_hit"] = True

            send_telegram(

                "🏁 TP3 HIT — SELL\n"

                f"Price: "
                f"{price_format(price)}\n"

                f"Entry: "
                f"{price_format(position['entry'])}\n"

                f"TP3: "
                f"{price_format(position['tp3'])}\n"

                "📌 Simulation position closed."
            )

            position = None

            return

        if (
            price <= position["tp2"]
            and not position["tp2_hit"]
        ):

            position["tp2_hit"] = True

            send_telegram(

                "✅ TP2 HIT — SELL\n"

                f"Price: "
                f"{price_format(price)}\n"

                f"TP2: "
                f"{price_format(position['tp2'])}"
            )

        if (
            price <= position["tp1"]
            and not position["tp1_hit"]
        ):

            position["tp1_hit"] = True

            position["break_even"] = True

            position["trail"] = min(

                position["trail"],

                position["entry"]
            )

            send_telegram(

                "✅ TP1 HIT — SELL\n"

                f"Price: "
                f"{price_format(price)}\n"

                f"TP1: "
                f"{price_format(position['tp1'])}\n"

                "🔒 Stop Loss moved to "
                "BREAK-EVEN\n"

                f"New SL: "
                f"{price_format(position['entry'])}"
            )

        # Trailing stop
        if position["tp1_hit"]:

            new_trail = (
                price
                + TRAILING_ATR * atr
            )

            if new_trail < position["trail"]:

                position["trail"] = new_trail

        # Stop / trailing hit
        if price >= position["trail"]:

            send_telegram(

                "🛑 STOP / TRAILING HIT — SELL\n"

                f"Price: "
                f"{price_format(price)}\n"

                f"Exit: "
                f"{price_format(position['trail'])}\n"

                "📌 Simulation position closed."
            )

            position = None


# =========================================================
# PROCESS MARKET
# =========================================================

def process_market():

    global position
    global last_candle_time
    global last_signal

    candles = get_candles()

    if len(candles) < 30:

        log(
            "Not enough candle data."
        )

        return

    info = analyze_market(
        candles
    )

    if not info:

        log(
            "Indicators not ready."
        )

        return

    log(

        f"Current gold price: "
        f"{price_format(info['price'])}"
    )

    log(

        f"Signal: "
        f"{info['signal']}"
    )

    log(

        f"Trend Score: "
        f"{info['score']}/100"
    )

    log(

        f"EMA 9: "
        f"{price_format(info['ema9'])}"
    )

    log(

        f"EMA 21: "
        f"{price_format(info['ema21'])}"
    )

    log(

        f"RSI: "
        f"{info['rsi']:.2f}"
    )

    log(

        f"ATR: "
        f"{price_format(info['atr'])}"
    )

    # Manage existing simulated trade
    manage_position(info)

    new_candle = (
        info["candle"]
        != last_candle_time
    )

    # =====================================================
    # NEW BUY / SELL
    # =====================================================

    if (

        position is None

        and info["signal"]
        in ("BUY", "SELL")

        and new_candle

    ):

        position = create_levels(
            info
        )

        send_telegram(

            build_signal_message(
                info,
                position
            )
        )

        last_signal = (
            info["signal"]
        )

    # =====================================================
    # HOLD
    # =====================================================

    elif (

        position is None

        and info["signal"]
        == "HOLD"

        and new_candle

    ):

        # Only send HOLD when signal changes
        if last_signal != "HOLD":

            send_telegram(

                build_signal_message(
                    info
                )
            )

        last_signal = "HOLD"

    last_candle_time = (
        info["candle"]
    )


# =========================================================
# MAIN
# =========================================================

def main():

    validate_environment()

    log(
        "================================"
    )

    log(
        "GOLD SIGNAL BOT STARTED"
    )

    log(
        "================================"
    )

    log(
        f"Symbol: {SYMBOL}"
    )

    log(
        f"Timeframe: {INTERVAL}"
    )

    log(
        "Checking market every 5 minutes."
    )

    log(
        "BUY / SELL / HOLD: ENABLED"
    )

    log(
        "TP1 / TP2 / TP3: ENABLED"
    )

    log(
        "Break-even: ENABLED"
    )

    log(
        "Trailing Stop: ENABLED"
    )

    log(
        "Real trading: DISABLED"
    )

    send_telegram(

        "🤖 GOLD SIGNAL BOT ONLINE\n\n"

        f"Symbol: {SYMBOL}\n"

        f"Timeframe: {INTERVAL}\n"

        "BUY / SELL / HOLD: ON\n"

        "TP1 / TP2 / TP3: ON\n"

        "Break-even: ON\n"

        "Trailing Stop: ON\n\n"

        "🧪 Mode: SIMULATION\n"

        "⚠️ No real order will be placed."
    )

    while True:

        try:

            process_market()

        except Exception as error:

            log(
                f"Main loop error: {error}"
            )

        time.sleep(
            CHECK_SECONDS
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()
