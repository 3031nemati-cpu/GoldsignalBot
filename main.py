import os
import time
from datetime import datetime, timezone
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"
CHECK_INTERVAL = 300

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

# حداقل قدرت روند برای BUY/SELL
MIN_TREND_SCORE = 60

# مدیریت ریسک بر اساس ATR
SL_ATR = 1.5
TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0

# تشخیص شکست حمایت/مقاومت
BREAKOUT_LOOKBACK = 12
BREAKOUT_BUFFER_ATR = 0.10

last_signal = None
last_warning = None


def log(message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


def get_candles():

    try:

        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": SYMBOL,
                "interval": INTERVAL,
                "outputsize": 100,
                "apikey": API_KEY,
                "format": "JSON",
                "timezone": "UTC",
            },
            timeout=20,
        )

        data = response.json()

        if data.get("status") == "error":
            log(f"Twelve Data API Error: {data}")
            return None

        values = data.get("values")

        if not values or len(values) < 40:
            log("Not enough candle data.")
            return None

        values.reverse()

        candles = []

        for x in values:

            candles.append({
                "datetime": x["datetime"],
                "open": float(x["open"]),
                "high": float(x["high"]),
                "low": float(x["low"]),
                "close": float(x["close"]),
            })

        return candles

    except Exception as e:

        log(f"Market data error: {e}")
        return None


def ema(values, period):

    if len(values) < period:
        return None

    k = 2 / (period + 1)

    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (value - result) * k + result

    return result


def rsi(values, period=14):

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

        avg_gain = (
            (avg_gain * (period - 1)) + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1)) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]

        previous_close = candles[i - 1]["close"]

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        trs.append(true_range)

    return sum(trs[-period:]) / period


def data_is_fresh(candles):

    try:

        candle_time = datetime.strptime(
            candles[-1]["datetime"],
            "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        age = (
            datetime.now(timezone.utc) - candle_time
        ).total_seconds() / 60

        log(f"Latest candle age: {age:.1f} minutes")

        if age > 30:

            log("Market data is stale. Analysis paused.")

            return False

        return True

    except Exception as e:

        log(f"Freshness check error: {e}")

        return False


def trend_strength(
    price,
    ema9,
    ema21,
    current_rsi,
    current_atr
):

    if current_atr <= 0:
        return "Neutral", 0

    gap_score = min(
        40,
        abs(ema9 - ema21) / current_atr * 40
    )

    price_score = min(
        30,
        abs(price - ema9) / current_atr * 30
    )

    if ema9 > ema21 and current_rsi >= RSI_BUY:

        direction = "Bullish"

        rsi_score = min(
            30,
            (current_rsi - 50) * 1.5
        )

    elif ema9 < ema21 and current_rsi <= RSI_SELL:

        direction = "Bearish"

        rsi_score = min(
            30,
            (50 - current_rsi) * 1.5
        )

    else:

        direction = "Neutral"

        rsi_score = 0

    score = round(
        min(
            100,
            gap_score + price_score + rsi_score
        )
    )

    if direction == "Bullish":

        label = (
            "Strong Bullish"
            if score >= 70
            else "Bullish"
        )

    elif direction == "Bearish":

        label = (
            "Strong Bearish"
            if score >= 70
            else "Bearish"
        )

    else:

        label = "Neutral"

    return label, score


def market_structure(candles, current_atr):

    if len(candles) < BREAKOUT_LOOKBACK + 2:

        return "NONE", None, None

    previous = candles[
        -BREAKOUT_LOOKBACK - 1:-1
    ]

    resistance = max(
        c["high"] for c in previous
    )

    support = min(
        c["low"] for c in previous
    )

    price = candles[-1]["close"]

    buffer = current_atr * BREAKOUT_BUFFER_ATR

    if price > resistance + buffer:

        return (
            "BREAKOUT_UP",
            support,
            resistance
        )

    if price < support - buffer:

        return (
            "BREAKOUT_DOWN",
            support,
            resistance
        )

    return (
        "NONE",
        support,
        resistance
    )


def detect_false_breakout(candles, current_atr):

    if len(candles) < BREAKOUT_LOOKBACK + 3:

        return False, None

    previous = candles[
        -BREAKOUT_LOOKBACK - 2:-2
    ]

    resistance = max(
        c["high"] for c in previous
    )

    support = min(
        c["low"] for c in previous
    )

    buffer = current_atr * BREAKOUT_BUFFER_ATR

    previous_candle = candles[-2]
    last_candle = candles[-1]

    if (
        previous_candle["high"]
        > resistance + buffer
        and last_candle["close"]
        < resistance
    ):

        return True, "FALSE_BREAKOUT_UP"

    if (
        previous_candle["low"]
        < support - buffer
        and last_candle["close"]
        > support
    ):

        return True, "FALSE_BREAKOUT_DOWN"

    return False, None


def analyze_market(candles):

    if len(candles) < 40:
        return None

    if not data_is_fresh(candles):
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    price = closes[-1]

    ema9 = ema(
        closes,
        EMA_FAST
    )

    ema21 = ema(
        closes,
        EMA_SLOW
    )

    current_rsi = rsi(
        closes,
        RSI_PERIOD
    )

    current_atr = atr(
        candles,
        ATR_PERIOD
    )

    if None in (
        ema9,
        ema21,
        current_rsi,
        current_atr
    ):

        return None

    trend, score = trend_strength(
        price,
        ema9,
        ema21,
        current_rsi,
        current_atr
    )

    structure, support, resistance = market_structure(
        candles,
        current_atr
    )

    false_breakout, false_type = detect_false_breakout(
        candles,
        current_atr
    )

    # =========================
    # BUY / SELL / HOLD
    # =========================

    if (
        ema9 > ema21
        and price > ema9
        and current_rsi >= RSI_BUY
        and score >= MIN_TREND_SCORE
        and not false_breakout
    ):

        signal = "BUY"

    elif (
        ema9 < ema21
        and price < ema9
        and current_rsi <= RSI_SELL
        and score >= MIN_TREND_SCORE
        and not false_breakout
    ):

        signal = "SELL"

    else:

        signal = "HOLD"

    # =========================
    # TARGETS
    # =========================

    if signal == "BUY":

        entry = price

        stop = (
            entry
            - SL_ATR * current_atr
        )

        tp1 = (
            entry
            + TP1_ATR * current_atr
        )

        tp2 = (
            entry
            + TP2_ATR * current_atr
        )

        tp3 = (
            entry
            + TP3_ATR * current_atr
        )

    elif signal == "SELL":

        entry = price

        stop = (
            entry
            + SL_ATR * current_atr
        )

        tp1 = (
            entry
            - TP1_ATR * current_atr
        )

        tp2 = (
            entry
            - TP2_ATR * current_atr
        )

        tp3 = (
            entry
            - TP3_ATR * current_atr
        )

    else:

        entry = None
        stop = None
        tp1 = None
        tp2 = None
        tp3 = None

    # =========================
    # REVERSAL ZONE
    # =========================

    reversal_width = max(
        current_atr * 0.50,
        abs(ema9 - ema21)
    )

    reversal_low = (
        ema21 - reversal_width
    )

    reversal_high = (
        ema21 + reversal_width
    )

    # =========================
    # TREND WEAKENING
    # =========================

    weakening = False
    weakening_reason = ""

    if signal == "BUY":

        if price < ema9:

            weakening = True
            weakening_reason = (
                "Price dropped below EMA9."
            )

        elif current_rsi < RSI_BUY:

            weakening = True
            weakening_reason = (
                "RSI lost bullish confirmation."
            )

        elif ema9 <= ema21:

            weakening = True
            weakening_reason = (
                "EMA trend weakened."
            )

    elif signal == "SELL":

        if price > ema9:

            weakening = True
            weakening_reason = (
                "Price moved above EMA9."
            )

        elif current_rsi > RSI_SELL:

            weakening = True
            weakening_reason = (
                "RSI lost bearish confirmation."
            )

        elif ema9 >= ema21:

            weakening = True
            weakening_reason = (
                "EMA trend weakened."
            )

    return {

        "signal": signal,

        "price": price,

        "ema9": ema9,

        "ema21": ema21,

        "rsi": current_rsi,

        "atr": current_atr,

        "trend": trend,

        "score": score,

        "structure": structure,

        "support": support,

        "resistance": resistance,

        "false_breakout": false_breakout,

        "false_type": false_type,

        "entry": entry,

        "stop": stop,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3,

        "reversal_low": reversal_low,

        "reversal_high": reversal_high,

        "weakening": weakening,

        "weakening_reason": weakening_reason,

        "candle_time": candles[-1]["datetime"]

    }


def format_signal(result):

    if result["signal"] == "BUY":

        title = "🟢 GOLD SIGNAL — BUY"

    elif result["signal"] == "SELL":

        title = "🔴 GOLD SIGNAL — SELL"

    else:

        title = "⚪ GOLD SIGNAL — HOLD"

    text = (

        f"{title}\n\n"

        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n"
        f"Price: {result['price']:.2f}\n\n"

        f"📊 Trend: {result['trend']}\n"
        f"Trend Score: {result['score']}/100\n\n"

        f"EMA 9: {result['ema9']:.2f}\n"
        f"EMA 21: {result['ema21']:.2f}\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"ATR: {result['atr']:.2f}\n"
    )

    if result["signal"] in (
        "BUY",
        "SELL"
    ):

        text += (

            f"\n💰 Entry: "
            f"{result['entry']:.2f}\n"

            f"🎯 TP1: "
            f"{result['tp1']:.2f}\n"

            f"🎯 TP2: "
            f"{result['tp2']:.2f}\n"

            f"🎯 TP3: "
            f"{result['tp3']:.2f}\n"

            f"🛑 Stop Loss: "
            f"{result['stop']:.2f}\n"
        )

    if result["support"] is not None:

        text += (

            f"\n📉 Support: "
            f"{result['support']:.2f}\n"

            f"📈 Resistance: "
            f"{result['resistance']:.2f}\n"
        )

    text += (

        f"\n🔄 Reversal/Caution Zone:\n"

        f"{result['reversal_low']:.2f}"
        f" - "
        f"{result['reversal_high']:.2f}\n"
    )

    if result["structure"] == "BREAKOUT_UP":

        text += (
            "\n🚀 Breakout: UP\n"
        )

    elif result["structure"] == "BREAKOUT_DOWN":

        text += (
            "\n📉 Breakout: DOWN\n"
        )

    if result["false_breakout"]:

        text += (

            "\n⚠️ Possible FALSE BREAKOUT "
            "detected.\n"

            "Wait for confirmation.\n"
        )

    if result["weakening"]:

        text += (

            "\n⚠️ TREND WEAKENING\n"

            f"{result['weakening_reason']}\n"
        )

    text += (

        f"\nCandle: "
        f"{result['candle_time']}\n"

        "⚠️ Technical analysis only — "
        "not an automatic trade."
    )

    return text


def send_message(text):

    if not BOT_TOKEN:

        log(
            "ERROR: BOT_TOKEN is missing."
        )

        return False

    if not CHAT_ID:

        log(
            "ERROR: CHAT_ID is missing."
        )

        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(

            url,

            data={
                "chat_id": CHAT_ID,
                "text": text
            },

            timeout=20
        )

        data = response.json()

        if (
            response.ok
            and data.get("ok")
        ):

            log(
                "Telegram message sent."
            )

            return True

        log(
            f"Telegram API Error: {data}"
        )

        return False

    except Exception as e:

        log(
            f"Telegram connection error: {e}"
        )

        return False


def main():

    global last_signal
    global last_warning

    log(
        "=========================================="
    )

    log(
        "GoldSignalRezaBot - "
        "Professional Analyzer v2"
    )

    log(
        f"Symbol: {SYMBOL}"
    )

    log(
        f"Timeframe: {INTERVAL}"
    )

    log(
        f"Minimum Trend Score: "
        f"{MIN_TREND_SCORE}/100"
    )

    log(
        "Breakout + False Breakout: ON"
    )

    log(
        "Trend Weakening Alerts: ON"
    )

    log(
        "Automatic trading: DISABLED"
    )

    log(
        "=========================================="
    )

    while True:

        try:

            candles = get_candles()

            if candles:

                result = analyze_market(
                    candles
                )

                if result:

                    log(

                        f"Price="
                        f"{result['price']:.2f} | "

                        f"EMA9="
                        f"{result['ema9']:.2f} | "

                        f"EMA21="
                        f"{result['ema21']:.2f} | "

                        f"RSI="
                        f"{result['rsi']:.2f} | "

                        f"ATR="
                        f"{result['atr']:.2f}"
                    )

                    log(

                        f"Signal="
                        f"{result['signal']} | "

                        f"Trend="
                        f"{result['trend']} | "

                        f"Score="
                        f"{result['score']}/100 | "

                        f"Structure="
                        f"{result['structure']}"
                    )

                    if result["false_breakout"]:

                        log(

                            "Possible false breakout: "

                            f"{result['false_type']}"
                        )

                    # ارسال پیام فقط هنگام تغییر سیگنال
                    if (
                        result["signal"]
                        != last_signal
                    ):

                        if send_message(
                            format_signal(result)
                        ):

                            last_signal = (
                                result["signal"]
                            )

                            last_warning = None

                    # هشدار ضعیف شدن روند
                    elif (

                        result["weakening"]

                        and result["signal"]
                        in ("BUY", "SELL")

                        and result[
                            "weakening_reason"
                        ]
                        != last_warning

                    ):

                        warning = (

                            "⚠️ GOLD TREND WARNING\n\n"

                            f"Symbol: {SYMBOL}\n"

                            f"Timeframe: {INTERVAL}\n"

                            f"Price: "
                            f"{result['price']:.2f}\n"

                            f"Trend: "
                            f"{result['trend']}\n"

                            f"Score: "
                            f"{result['score']}/100\n\n"

                            "Reason:\n"

                            f"{result['weakening_reason']}\n\n"

                            "⚠️ Momentum may be "
                            "weakening. "
                            "Wait for confirmation."
                        )

                        if send_message(
                            warning
                        ):

                            last_warning = (
                                result[
                                    "weakening_reason"
                                ]
                            )

                    else:

                        log(

                            f"Signal unchanged "
                            f"({result['signal']}). "
                            "No Telegram message sent."
                        )

            time.sleep(
                CHECK_INTERVAL
            )

        except Exception as e:

            log(
                f"Main loop error: {e}"
            )

            time.sleep(60)


if __name__ == "__main__":

    main()
