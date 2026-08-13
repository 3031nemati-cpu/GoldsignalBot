import os
import time
import logging
from datetime import datetime, timezone

import requests

# ============================================================
# GOLD SIGNAL BOT - PROFESSIONAL PERSIAN VERSION
# ============================================================
# ویژگی‌ها:
# - XAU/USD
# - تایم‌فریم 5 دقیقه
# - بررسی هر 30 ثانیه
# - فقط استفاده از کندل بسته‌شده
# - ارسال سیگنال جدید در هر کندل بسته‌شده
# - EMA 9 / EMA 21
# - RSI
# - ATR
# - Trend Score
# - Support / Resistance
# - TP1 / TP2 / TP3
# - Stop Loss
# - جلوگیری از ارسال تکراری برای همان کندل
# - پیام‌های HOLD خاموش
# - معاملات خودکار خاموش
# - پیام تلگرام کاملاً فارسی
# - بدون نمایش ساعت UTC و تهران در پیام تلگرام
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

# بررسی بازار هر 30 ثانیه
CHECK_SECONDS = 30

# حداقل امتیاز لازم برای صدور سیگنال
MIN_TREND_SCORE = 55

# حداکثر عمر مجاز کندل
MAX_CANDLE_AGE_MINUTES = 8

# تنظیمات تارگت و حد ضرر بر اساس ATR
TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0
SL_ATR = 1.5

# وضعیت معاملات خودکار
AUTOMATIC_TRADING = False

# ارسال HOLD خاموش
SEND_HOLD_MESSAGES = False


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(levelname)s | %(message)s"
)

log = logging.getLogger("GoldSignalBot")


# ============================================================
# CHECK ENVIRONMENT VARIABLES
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not CHAT_ID:
    raise RuntimeError("CHAT_ID is missing")

if not API_KEY:
    raise RuntimeError("API_KEY is missing")


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "GoldSignalBot/Professional"
})


# ============================================================
# SIGNAL MEMORY
# ============================================================

last_processed_candle = None
last_sent_signal = None
last_sent_candle = None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:

        response = session.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            log.error("Telegram error: %s", data)
            return False

        log.info("Telegram message sent successfully.")

        return True

    except Exception as e:

        log.error("Telegram error: %s", e)

        return False


# ============================================================
# DATE / TIME PARSER
# ============================================================

def parse_utc(value):

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


# ============================================================
# GET MARKET DATA FROM TWELVE DATA
# ============================================================

def get_candles(outputsize=120):

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": outputsize,
        "apikey": API_KEY,
        "timezone": "UTC",
        "format": "JSON"
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        # خطای Twelve Data
        if data.get("status") == "error":

            log.error(
                "Twelve Data error: %s",
                data
            )

            return []

        result = []

        for item in data.get("values", []):

            try:

                result.append({
                    "datetime": parse_utc(item["datetime"]),
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"])
                })

            except (
                KeyError,
                ValueError,
                TypeError
            ):

                continue

        result.sort(
            key=lambda x: x["datetime"]
        )

        return result

    except Exception as e:

        log.error(
            "Market data error: %s",
            e
        )

        return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(candles):

    now = datetime.now(timezone.utc)

    closed = []

    for candle in candles:

        # تایم‌فریم 5 دقیقه است.
        # datetime کندل = زمان شروع کندل
        close_time = (
            candle["datetime"].timestamp() + 300
        )

        if now.timestamp() >= close_time:

            closed.append(candle)

    return closed


# ============================================================
# CANDLE AGE
# ============================================================

def candle_age_minutes(candle):

    now = datetime.now(timezone.utc)

    close_time = (
        candle["datetime"].timestamp() + 300
    )

    age = (
        now.timestamp() - close_time
    ) / 60.0

    return max(0.0, age)


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    value = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
            + value
        )

    return value


# ============================================================
# EMA SERIES
# ============================================================

def ema_series(values, period):

    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)

    value = sum(
        values[:period]
    ) / period

    output = (
        [None] * (period - 1)
        + [value]
    )

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
            + value
        )

        output.append(value)

    return output


# ============================================================
# RSI
# ============================================================

def rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        difference = (
            values[i] - values[i - 1]
        )

        gains.append(
            max(difference, 0)
        )

        losses.append(
            max(-difference, 0)
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

    rs = (
        average_gain
        / average_loss
    )

    return 100 - (
        100 / (1 + rs)
    )


# ============================================================
# ATR
# ============================================================

def atr(candles, period=14):

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
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(
            true_range
        )

    value = (
        sum(true_ranges[:period])
        / period
    )

    for tr in true_ranges[period:]:

        value = (
            (
                value * (period - 1)
            )
            + tr
        ) / period

    return value


# ============================================================
# CANDLE DIRECTION
# ============================================================

def candle_direction(candle):

    if candle["close"] > candle["open"]:
        return "BULLISH"

    if candle["close"] < candle["open"]:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# MARKET ANALYSIS
# ============================================================

def calculate_analysis(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

    ema9_series = ema_series(
        closes,
        9
    )

    ema21_series = ema_series(
        closes,
        21
    )

    if (
        len(ema9_series) < 2
        or len(ema21_series) < 2
    ):
        return None

    ema9 = ema9_series[-1]
    ema21 = ema21_series[-1]

    previous_ema9 = ema9_series[-2]
    previous_ema21 = ema21_series[-2]

    price = closes[-1]

    rsi_value = rsi(closes)

    atr_value = atr(candles)

    if (
        rsi_value is None
        or atr_value is None
    ):
        return None

    bullish_score = 0
    bearish_score = 0


    # --------------------------------------------------------
    # EMA 9 vs EMA 21
    # --------------------------------------------------------

    if ema9 > ema21:

        bullish_score += 25

    elif ema9 < ema21:

        bearish_score += 25


    # --------------------------------------------------------
    # PRICE vs EMA 9
    # --------------------------------------------------------

    if price > ema9:

        bullish_score += 15

    elif price < ema9:

        bearish_score += 15


    # --------------------------------------------------------
    # EMA 9 MOMENTUM
    # --------------------------------------------------------

    if ema9 > previous_ema9:

        bullish_score += 10

    elif ema9 < previous_ema9:

        bearish_score += 10


    # --------------------------------------------------------
    # EMA 21 MOMENTUM
    # --------------------------------------------------------

    if ema21 > previous_ema21:

        bullish_score += 10

    elif ema21 < previous_ema21:

        bearish_score += 10


    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if 52 <= rsi_value <= 68:

        bullish_score += 20

    elif 32 <= rsi_value <= 48:

        bearish_score += 20


    # --------------------------------------------------------
    # EXTREME RSI PROTECTION
    # --------------------------------------------------------

    if rsi_value > 72:

        bullish_score -= 10

    if rsi_value < 28:

        bearish_score -= 10


    # --------------------------------------------------------
    # LATEST CLOSED CANDLE
    # --------------------------------------------------------

    latest = candles[-1]

    if latest["close"] > latest["open"]:

        bullish_score += 10

    elif latest["close"] < latest["open"]:

        bearish_score += 10


    # --------------------------------------------------------
    # PREVIOUS CLOSED CANDLE
    # --------------------------------------------------------

    previous = candles[-2]

    if previous["close"] > previous["open"]:

        bullish_score += 10

    elif previous["close"] < previous["open"]:

        bearish_score += 10


    # --------------------------------------------------------
    # LIMIT SCORE
    # --------------------------------------------------------

    bullish_score = max(
        0,
        min(100, bullish_score)
    )

    bearish_score = max(
        0,
        min(100, bearish_score)
    )


    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if bullish_score >= bearish_score:

        score = bullish_score

        if score >= 80:

            trend = "Strong Bullish"

        elif score >= 60:

            trend = "Bullish"

        else:

            trend = "Neutral"

    else:

        score = bearish_score

        if score >= 80:

            trend = "Strong Bearish"

        elif score >= 60:

            trend = "Bearish"

        else:

            trend = "Neutral"


    return {
        "price": price,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi_value,
        "atr": atr_value,
        "bull": bullish_score,
        "bear": bearish_score,
        "trend": trend,
        "score": int(score)
    }


# ============================================================
# SIGNAL CONFIRMATION
# ============================================================

def confirm_signal(candles, analysis):

    latest = candles[-1]

    bullish_candle = (
        latest["close"]
        > latest["open"]
    )

    bearish_candle = (
        latest["close"]
        < latest["open"]
    )


    # BUY
    if (
        analysis["bull"]
        > analysis["bear"]
        and bullish_candle
    ):

        return "BUY", 1


    # SELL
    if (
        analysis["bear"]
        > analysis["bull"]
        and bearish_candle
    ):

        return "SELL", 1


    return "HOLD", 0


# ============================================================
# TP / SL LEVELS
# ============================================================

def levels(
    signal,
    price,
    atr_value,
    support,
    resistance
):

    if signal == "BUY":

        return {
            "entry": price,

            "tp1": (
                price
                + atr_value * TP1_ATR
            ),

            "tp2": (
                price
                + atr_value * TP2_ATR
            ),

            "tp3": (
                price
                + atr_value * TP3_ATR
            ),

            "sl": (
                price
                - atr_value * SL_ATR
            ),

            "support": support,
            "resistance": resistance
        }


    if signal == "SELL":

        return {
            "entry": price,

            "tp1": (
                price
                - atr_value * TP1_ATR
            ),

            "tp2": (
                price
                - atr_value * TP2_ATR
            ),

            "tp3": (
                price
                - atr_value * TP3_ATR
            ),

            "sl": (
                price
                + atr_value * SL_ATR
            ),

            "support": support,
            "resistance": resistance
        }


    return None


# ============================================================
# PERSIAN TEXT CONVERSION
# ============================================================

def persian_trend(trend):

    mapping = {

        "Strong Bullish":
            "صعودی قوی",

        "Bullish":
            "صعودی",

        "Strong Bearish":
            "نزولی قوی",

        "Bearish":
            "نزولی",

        "Neutral":
            "خنثی"
    }

    return mapping.get(
        trend,
        trend
    )


# ============================================================
# PERSIAN SIGNAL MESSAGE
# ============================================================

def build_message(
    signal,
    analysis,
    levels_data,
    confirmation_count
):

    if signal == "BUY":

        title = "🟢 سیگنال طلا — خرید"

    else:

        title = "🔴 سیگنال طلا — فروش"


    if analysis["score"] >= 75:

        quality = "بالا"

    else:

        quality = "متوسط"


    trend = persian_trend(
        analysis["trend"]
    )


    message = f"""
{title}

نماد: {SYMBOL}
تایم‌فریم: 5 دقیقه

💰 قیمت: {analysis["price"]:.2f}

📊 روند: {trend}
امتیاز روند: {analysis["score"]}/100

EMA 9: {analysis["ema9"]:.2f}
EMA 21: {analysis["ema21"]:.2f}
RSI: {analysis["rsi"]:.2f}
ATR: {analysis["atr"]:.2f}

━━━━━━━━━━━━━━━━

💰 نقطه ورود: {levels_data["entry"]:.2f}

🎯 هدف اول: {levels_data["tp1"]:.2f}
🎯 هدف دوم: {levels_data["tp2"]:.2f}
🎯 هدف سوم: {levels_data["tp3"]:.2f}

🛑 حد ضرر: {levels_data["sl"]:.2f}

━━━━━━━━━━━━━━━━

📉 حمایت: {levels_data["support"]:.2f}
📈 مقاومت: {levels_data["resistance"]:.2f}

✅ تأیید سیگنال: {confirmation_count}/2
⭐ کیفیت سیگنال: {quality}

⚠️ صرفاً تحلیل تکنیکال
معامله خودکار فعال نیست.
"""

    return message.strip()


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze():

    global last_processed_candle
    global last_sent_signal
    global last_sent_candle


    # --------------------------------------------------------
    # GET DATA
    # --------------------------------------------------------

    candles = get_candles()

    if len(candles) < 30:

        log.warning(
            "Not enough candles."
        )

        return


    # --------------------------------------------------------
    # ONLY CLOSED CANDLES
    # --------------------------------------------------------

    closed = get_closed_candles(
        candles
    )

    if len(closed) < 30:

        log.warning(
            "Not enough CLOSED candles."
        )

        return


    # --------------------------------------------------------
    # LATEST CLOSED CANDLE
    # --------------------------------------------------------

    latest = closed[-1]

    candle_id = (
        latest["datetime"].isoformat()
    )


    # --------------------------------------------------------
    # DUPLICATE PROTECTION
    # --------------------------------------------------------

    if candle_id == last_processed_candle:

        return


    age = candle_age_minutes(
        latest
    )

    log.info(
        "New CLOSED candle detected | age=%.2f min",
        age
    )


    # --------------------------------------------------------
    # STALE CANDLE PROTECTION
    # --------------------------------------------------------

    if age > MAX_CANDLE_AGE_MINUTES:

        log.warning(
            "Candle rejected as stale."
        )

        last_processed_candle = candle_id

        return


    # --------------------------------------------------------
    # CALCULATE INDICATORS
    # --------------------------------------------------------

    analysis = calculate_analysis(
        closed
    )

    if not analysis:

        log.warning(
            "Indicator calculation failed."
        )

        return


    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    recent = closed[-20:]

    support = min(
        candle["low"]
        for candle in recent
    )

    resistance = max(
        candle["high"]
        for candle in recent
    )


    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    signal, confirmation_count = (
        confirm_signal(
            closed,
            analysis
        )
    )


    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    log.info(
        "Price=%.2f | EMA9=%.2f | EMA21=%.2f | RSI=%.2f | ATR=%.2f",
        analysis["price"],
        analysis["ema9"],
        analysis["ema21"],
        analysis["rsi"],
        analysis["atr"]
    )

    log.info(
        "Signal=%s | Trend=%s | Score=%d | Confirmation=%d",
        signal,
        analysis["trend"],
        analysis["score"],
        confirmation_count
    )


    # --------------------------------------------------------
    # HOLD
    # --------------------------------------------------------

    if signal == "HOLD":

        log.info(
            "No actionable signal."
        )

        last_processed_candle = (
            candle_id
        )

        return


    # --------------------------------------------------------
    # MINIMUM SCORE
    # --------------------------------------------------------

    if analysis["score"] < MIN_TREND_SCORE:

        log.info(
            "Signal rejected: score=%d < minimum=%d",
            analysis["score"],
            MIN_TREND_SCORE
        )

        last_processed_candle = (
            candle_id
        )

        return


    # --------------------------------------------------------
    # DIRECTION CHECK
    # --------------------------------------------------------

    if (
        signal == "BUY"
        and analysis["bull"]
        <= analysis["bear"]
    ):

        last_processed_candle = (
            candle_id
        )

        return


    if (
        signal == "SELL"
        and analysis["bear"]
        <= analysis["bull"]
    ):

        last_processed_candle = (
            candle_id
        )

        return


    # --------------------------------------------------------
    # CALCULATE LEVELS
    # --------------------------------------------------------

    levels_data = levels(
        signal,
        analysis["price"],
        analysis["atr"],
        support,
        resistance
    )


    if not levels_data:

        last_processed_candle = (
            candle_id
        )

        return


    # --------------------------------------------------------
    # BUILD PERSIAN MESSAGE
    # --------------------------------------------------------

    message = build_message(
        signal,
        analysis,
        levels_data,
        confirmation_count
    )


    # --------------------------------------------------------
    # SEND TELEGRAM
    # --------------------------------------------------------

    if send_telegram(message):

        last_sent_signal = signal

        last_sent_candle = candle_id

        last_processed_candle = (
            candle_id
        )

        log.info(
            "Signal delivered | direction=%s",
            signal
        )

    else:

        log.warning(
            "Telegram failed. "
            "Candle will be retried."
        )


# ============================================================
# STARTUP
# ============================================================

def startup():

    log.info(
        "================================================"
    )

    log.info(
        "GOLD SIGNAL BOT - PERSIAN VERSION"
    )

    log.info(
        "Professional Analysis Mode: ON"
    )

    log.info(
        "Symbol: %s",
        SYMBOL
    )

    log.info(
        "Timeframe: %s",
        INTERVAL
    )

    log.info(
        "Minimum Trend Score: %d/100",
        MIN_TREND_SCORE
    )

    log.info(
        "Scan interval: %d seconds",
        CHECK_SECONDS
    )

    log.info(
        "Maximum Candle Age: %d minutes",
        MAX_CANDLE_AGE_MINUTES
    )

    log.info(
        "Closed Candle Protection: ON"
    )

    log.info(
        "Duplicate Candle Protection: ON"
    )

    log.info(
        "Same-direction signal updates: ON"
    )

    log.info(
        "Telegram retry on failure: ON"
    )

    log.info(
        "HOLD Messages: OFF"
    )

    log.info(
        "Automatic Trading: DISABLED"
    )

    log.info(
        "Telegram timestamps: OFF"
    )

    log.info(
        "Persian Telegram Messages: ON"
    )

    log.info(
        "================================================"
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    startup()

    while True:

        try:

            analyze()

        except KeyboardInterrupt:

            log.info(
                "Bot stopped."
            )

            break

        except Exception as e:

            log.exception(
                "Unexpected error: %s",
                e
            )

        time.sleep(
            CHECK_SECONDS
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
