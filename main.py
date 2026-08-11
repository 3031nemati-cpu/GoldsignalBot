import os
import time
import logging
from datetime import datetime, timezone, timedelta

import requests


# =========================================================
# GOLD SIGNAL BOT
# PROFESSIONAL ANALYSIS VERSION
# XAU/USD - 5 MINUTES
# =========================================================


# =========================================================
# CONFIGURATION
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

CHECK_SECONDS = 60

# Minimum score required for BUY/SELL
MIN_TREND_SCORE = 60

# Maximum acceptable age of market data
MAX_CANDLE_AGE_MINUTES = 8

# Confirmation candles
REQUIRED_CONFIRMATIONS = 2

# HOLD messages disabled
SEND_HOLD_MESSAGES = False

# Automatic trading remains OFF
AUTO_TRADING = False

# API request timeout
REQUEST_TIMEOUT = 20

# Number of candles requested
OUTPUT_SIZE = 120

# ATR multiplier for Stop Loss
SL_ATR_MULTIPLIER = 1.5

# TP multipliers
TP1_ATR_MULTIPLIER = 1.0
TP2_ATR_MULTIPLIER = 2.0
TP3_ATR_MULTIPLIER = 3.0

# Tehran timezone
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(levelname)s | %(message)s"
)

logger = logging.getLogger("GoldSignalBot")


# =========================================================
# VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not CHAT_ID:
    raise RuntimeError("CHAT_ID is missing")

if not API_KEY:
    raise RuntimeError("API_KEY is missing")


# =========================================================
# HTTP SESSION
# =========================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "GoldSignalBot/Professional"
})


# =========================================================
# STATE
# =========================================================

last_processed_candle = None
last_sent_signal = None
last_sent_candle = None

last_market_price = None
last_data_status = None


# =========================================================
# TIME FUNCTIONS
# =========================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_tehran():
    return now_utc().astimezone(IRAN_TZ)


def format_utc(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_tehran(dt):
    return dt.astimezone(IRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def parse_api_datetime(value):
    """
    Twelve Data Forex timestamps are normally UTC.
    We explicitly treat naive timestamps as UTC.
    """

    if not value:
        raise ValueError("Empty candle datetime")

    value = value.strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M"
    ]

    parsed = None

    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue

    if parsed is None:
        raise ValueError(f"Invalid datetime format: {value}")

    return parsed.replace(tzinfo=timezone.utc)


# =========================================================
# MARKET DATA
# =========================================================

def get_market_data():

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": OUTPUT_SIZE,
        "timezone": "UTC",
        "apikey": API_KEY
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

    except Exception as exc:

        logger.error(
            "Market data request failed: %s",
            exc
        )

        return None

    if data.get("status") == "error":

        logger.error(
            "Twelve Data error: %s",
            data.get("message")
        )

        return None

    values = data.get("values")

    if not values:

        logger.error("No candle data received")

        return None

    candles = []

    for item in values:

        try:

            candle = {
                "datetime": parse_api_datetime(
                    item["datetime"]
                ),

                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            }

            candles.append(candle)

        except Exception as exc:

            logger.warning(
                "Invalid candle skipped: %s",
                exc
            )

    if len(candles) < 30:

        logger.error(
            "Not enough candles: %s",
            len(candles)
        )

        return None

    # Twelve Data normally returns newest first.
    # Sort explicitly to remove dependency on API order.

    candles.sort(
        key=lambda x: x["datetime"]
    )

    return candles


# =========================================================
# CLOSED CANDLE VALIDATION
# =========================================================

def get_closed_candles(candles):

    current_time = now_utc()

    closed = []

    for candle in candles:

        candle_start = candle["datetime"]

        candle_end = candle_start + timedelta(
            minutes=5
        )

        # Small safety buffer
        candle_end += timedelta(
            seconds=10
        )

        if candle_end <= current_time:

            closed.append(candle)

    if len(closed) < 30:

        logger.error(
            "Not enough CLOSED candles"
        )

        return None

    return closed


# =========================================================
# DATA FRESHNESS
# =========================================================

def validate_data_freshness(candles):

    latest = candles[-1]

    current = now_utc()

    candle_start = latest["datetime"]

    candle_end = candle_start + timedelta(
        minutes=5
    )

    age_seconds = (
        current - candle_end
    ).total_seconds()

    age_minutes = max(
        0,
        age_seconds / 60
    )

    logger.info(
        "Latest closed candle UTC: %s",
        format_utc(candle_start)
    )

    logger.info(
        "Latest candle Tehran: %s",
        format_tehran(candle_start)
    )

    logger.info(
        "Current UTC: %s",
        format_utc(current)
    )

    logger.info(
        "Current Tehran: %s",
        format_tehran(current)
    )

    logger.info(
        "Closed candle age: %.2f minutes",
        age_minutes
    )

    if age_minutes > MAX_CANDLE_AGE_MINUTES:

        logger.warning(
            "STALE DATA - SIGNAL BLOCKED"
        )

        return False, age_minutes

    return True, age_minutes


# =========================================================
# EMA
# =========================================================

def calculate_ema(values, period):

    if len(values) < period:

        return None

    multiplier = 2 / (period + 1)

    ema = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        ema = (
            price - ema
        ) * multiplier + ema

    return ema


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
            values[i] -
            values[i - 1]
        )

        if change >= 0:

            gains.append(change)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(
        gains[:period]
    ) / period

    avg_loss = sum(
        losses[:period]
    ) / period

    for i in range(period, len(gains)):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
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

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(tr)

    if len(true_ranges) < period:

        return None

    atr = (
        sum(true_ranges[:period])
        / period
    )

    for tr in true_ranges[period:]:

        atr = (
            (atr * (period - 1))
            + tr
        ) / period

    return atr


# =========================================================
# SUPPORT / RESISTANCE
# =========================================================

def calculate_support_resistance(candles):

    recent = candles[-30:]

    support = min(
        c["low"] for c in recent
    )

    resistance = max(
        c["high"] for c in recent
    )

    return support, resistance


# =========================================================
# CANDLE DIRECTION
# =========================================================

def candle_direction(candle):

    if candle["close"] > candle["open"]:

        return "BULLISH"

    if candle["close"] < candle["open"]:

        return "BEARISH"

    return "NEUTRAL"


# =========================================================
# CONFIRMATION
# =========================================================

def get_confirmation(candles, direction):

    confirmations = 0

    recent = candles[-REQUIRED_CONFIRMATIONS:]

    for candle in recent:

        direction_candle = candle_direction(
            candle
        )

        if direction == "BUY":

            if direction_candle == "BULLISH":

                confirmations += 1

        elif direction == "SELL":

            if direction_candle == "BEARISH":

                confirmations += 1

    return confirmations


# =========================================================
# TREND ANALYSIS
# =========================================================

def analyze_market(candles):

    closes = [
        c["close"]
        for c in candles
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

    if (
        ema9 is None
        or ema21 is None
        or rsi is None
        or atr is None
    ):

        return None

    price = closes[-1]

    support, resistance = (
        calculate_support_resistance(
            candles
        )
    )

    score_buy = 0
    score_sell = 0

    # =====================================================
    # EMA STRUCTURE
    # =====================================================

    if ema9 > ema21:

        score_buy += 25

    elif ema9 < ema21:

        score_sell += 25


    # =====================================================
    # PRICE vs EMA9
    # =====================================================

    if price > ema9:

        score_buy += 15

    elif price < ema9:

        score_sell += 15


    # =====================================================
    # PRICE vs EMA21
    # =====================================================

    if price > ema21:

        score_buy += 15

    elif price < ema21:

        score_sell += 15


    # =====================================================
    # RSI
    # =====================================================

    if 52 <= rsi <= 68:

        score_buy += 15

    elif 32 <= rsi <= 48:

        score_sell += 15


    # Stronger momentum
    if rsi > 55:

        score_buy += 10

    elif rsi < 45:

        score_sell += 10


    # =====================================================
    # LAST CANDLE
    # =====================================================

    last_candle = candles[-1]

    direction = candle_direction(
        last_candle
    )

    if direction == "BULLISH":

        score_buy += 10

    elif direction == "BEARISH":

        score_sell += 10


    # =====================================================
    # TREND
    # =====================================================

    if score_buy > score_sell:

        trend = "Bullish"

    elif score_sell > score_buy:

        trend = "Bearish"

    else:

        trend = "Neutral"


    # =====================================================
    # SIGNAL
    # =====================================================

    if (
        score_buy >= MIN_TREND_SCORE
        and score_buy > score_sell
    ):

        signal = "BUY"
        score = score_buy

    elif (
        score_sell >= MIN_TREND_SCORE
        and score_sell > score_buy
    ):

        signal = "SELL"
        score = score_sell

    else:

        signal = "HOLD"
        score = max(
            score_buy,
            score_sell
        )


    # =====================================================
    # CONFIRMATION
    # =====================================================

    confirmation = 0

    if signal in ("BUY", "SELL"):

        confirmation = get_confirmation(
            candles,
            signal
        )

        if confirmation < REQUIRED_CONFIRMATIONS:

            logger.info(
                "Signal rejected: confirmation %s/%s",
                confirmation,
                REQUIRED_CONFIRMATIONS
            )

            signal = "HOLD"


    return {
        "price": price,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi,
        "atr": atr,
        "support": support,
        "resistance": resistance,
        "trend": trend,
        "score": score,
        "signal": signal,
        "confirmation": confirmation,
        "candle": last_candle
    }


# =========================================================
# TRADE LEVELS
# =========================================================

def calculate_trade_levels(analysis):

    signal = analysis["signal"]
    price = analysis["price"]
    atr = analysis["atr"]

    if signal == "BUY":

        stop_loss = (
            price -
            atr * SL_ATR_MULTIPLIER
        )

        tp1 = (
            price +
            atr * TP1_ATR_MULTIPLIER
        )

        tp2 = (
            price +
            atr * TP2_ATR_MULTIPLIER
        )

        tp3 = (
            price +
            atr * TP3_ATR_MULTIPLIER
        )

    elif signal == "SELL":

        stop_loss = (
            price +
            atr * SL_ATR_MULTIPLIER
        )

        tp1 = (
            price -
            atr * TP1_ATR_MULTIPLIER
        )

        tp2 = (
            price -
            atr * TP2_ATR_MULTIPLIER
        )

        tp3 = (
            price -
            atr * TP3_ATR_MULTIPLIER
        )

    else:

        return None

    return {
        "entry": price,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stop_loss": stop_loss
    }


# =========================================================
# SIGNAL QUALITY
# =========================================================

def signal_quality(analysis):

    score = analysis["score"]
    confirmation = analysis["confirmation"]

    if score >= 80 and confirmation >= 2:

        return "HIGH"

    if score >= 70 and confirmation >= 2:

        return "GOOD"

    if score >= 60 and confirmation >= 2:

        return "VALID"

    return "WEAK"


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):

            logger.error(
                "Telegram error: %s",
                result
            )

            return False

        logger.info(
            "Telegram message sent."
        )

        return True

    except Exception as exc:

        logger.error(
            "Telegram request failed: %s",
            exc
        )

        return False


# =========================================================
# FORMAT SIGNAL
# =========================================================

def build_signal_message(
    analysis,
    levels,
    candle_age
):

    signal = analysis["signal"]

    candle = analysis["candle"]

    candle_utc = candle["datetime"]

    current = now_utc()

    quality = signal_quality(
        analysis
    )

    if signal == "BUY":

        emoji = "🟢"

    elif signal == "SELL":

        emoji = "🔴"

    else:

        emoji = "⚪"

    message = (
        f"{emoji} GOLD SIGNAL — {signal}\n\n"

        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n\n"

        f"💰 Price: {analysis['price']:.2f}\n\n"

        f"📊 Trend: {analysis['trend']}\n"
        f"Trend Score: "
        f"{analysis['score']}/100\n\n"

        f"EMA 9: {analysis['ema9']:.2f}\n"
        f"EMA 21: {analysis['ema21']:.2f}\n"
        f"RSI: {analysis['rsi']:.2f}\n"
        f"ATR: {analysis['atr']:.2f}\n"
    )

    if levels:

        message += (
            "\n━━━━━━━━━━━━━━━━\n"

            f"💰 Entry: "
            f"{levels['entry']:.2f}\n"

            f"🎯 TP1: "
            f"{levels['tp1']:.2f}\n"

            f"🎯 TP2: "
            f"{levels['tp2']:.2f}\n"

            f"🎯 TP3: "
            f"{levels['tp3']:.2f}\n"

            f"🛑 Stop Loss: "
            f"{levels['stop_loss']:.2f}\n"

            "━━━━━━━━━━━━━━━━\n"
        )

    message += (
        f"\n📉 Support: "
        f"{analysis['support']:.2f}\n"

        f"📈 Resistance: "
        f"{analysis['resistance']:.2f}\n\n"

        f"✅ Confirmation: "
        f"{analysis['confirmation']}/"
        f"{REQUIRED_CONFIRMATIONS}\n"

        f"⭐ Signal Quality: "
        f"{quality}\n\n"

        f"🕐 Candle UTC:\n"
        f"{format_utc(candle_utc)}\n"

        f"🇮🇷 Candle Tehran:\n"
        f"{format_tehran(candle_utc)}\n\n"

        f"🕒 Analysis UTC:\n"
        f"{format_utc(current)}\n"

        f"🇮🇷 Analysis Tehran:\n"
        f"{format_tehran(current)}\n\n"

        f"⏱ Candle Age: "
        f"{candle_age:.1f} minutes\n\n"

        "⚠️ Technical analysis only\n"
        "Not automatic trading."
    )

    return message


# =========================================================
# HOLD MESSAGE
# =========================================================

def build_hold_message(
    analysis,
    candle_age
):

    if not SEND_HOLD_MESSAGES:

        return None

    candle = analysis["candle"]

    return (
        "⚪ GOLD ANALYSIS — HOLD\n\n"

        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n"
        f"Price: {analysis['price']:.2f}\n\n"

        f"Trend: {analysis['trend']}\n"
        f"Score: {analysis['score']}/100\n"

        f"EMA9: {analysis['ema9']:.2f}\n"
        f"EMA21: {analysis['ema21']:.2f}\n"

        f"RSI: {analysis['rsi']:.2f}\n"
        f"ATR: {analysis['atr']:.2f}\n\n"

        f"🕐 Candle UTC:\n"
        f"{format_utc(candle['datetime'])}\n"

        f"🇮🇷 Candle Tehran:\n"
        f"{format_tehran(candle['datetime'])}\n\n"

        f"⏱ Age: {candle_age:.1f} minutes\n\n"

        "No confirmed BUY/SELL signal."
    )


# =========================================================
# MAIN ANALYSIS CYCLE
# =========================================================

def run_analysis():

    global last_processed_candle
    global last_sent_signal
    global last_sent_candle
    global last_market_price
    global last_data_status

    logger.info(
        "=================================================="
    )

    logger.info(
        "Starting professional market analysis"
    )

    logger.info(
        "Symbol: %s",
        SYMBOL
    )

    logger.info(
        "Timeframe: %s",
        INTERVAL
    )

    logger.info(
        "Minimum Trend Score: %s/100",
        MIN_TREND_SCORE
    )

    logger.info(
        "Maximum Candle Age: %s minutes",
        MAX_CANDLE_AGE_MINUTES
    )

    logger.info(
        "HOLD messages: %s",
        "ON" if SEND_HOLD_MESSAGES else "OFF"
    )

    logger.info(
        "Automatic Trading: %s",
        "ENABLED" if AUTO_TRADING else "DISABLED"
    )

    # =====================================================
    # DATA
    # =====================================================

    candles = get_market_data()

    if not candles:

        last_data_status = "NO_DATA"

        return

    # =====================================================
    # CLOSED CANDLES
    # =====================================================

    closed_candles = get_closed_candles(
        candles
    )

    if not closed_candles:

        last_data_status = "NO_CLOSED_CANDLE"

        return

    # =====================================================
    # FRESHNESS
    # =====================================================

    fresh, candle_age = (
        validate_data_freshness(
            closed_candles
        )
    )

    if not fresh:

        last_data_status = "STALE_DATA"

        logger.warning(
            "No signal generated because market data is stale."
        )

        return

    last_data_status = "OK"

    # =====================================================
    # LATEST CLOSED CANDLE
    # =====================================================

    latest_candle = closed_candles[-1]

    candle_id = latest_candle["datetime"]

    # Do not analyze the same candle repeatedly

    if (
        last_processed_candle
        == candle_id
    ):

        logger.info(
            "Same closed candle already processed."
        )

        return

    last_processed_candle = candle_id

    # =====================================================
    # ANALYSIS
    # =====================================================

    analysis = analyze_market(
        closed_candles
    )

    if not analysis:

        logger.error(
            "Analysis failed."
        )

        return

    last_market_price = (
        analysis["price"]
    )

    logger.info(
        "Price=%.2f | EMA9=%.2f | EMA21=%.2f",
        analysis["price"],
        analysis["ema9"],
        analysis["ema21"]
    )

    logger.info(
        "RSI=%.2f | ATR=%.2f",
        analysis["rsi"],
        analysis["atr"]
    )

    logger.info(
        "Trend=%s | Score=%s",
        analysis["trend"],
        analysis["score"]
    )

    logger.info(
        "Signal=%s",
        analysis["signal"]
    )

    logger.info(
        "Confirmation=%s/%s",
        analysis["confirmation"],
        REQUIRED_CONFIRMATIONS
    )

    # =====================================================
    # TRADE SIGNAL
    # =====================================================

    if analysis["signal"] in (
        "BUY",
        "SELL"
    ):

        signal = analysis["signal"]

        # Prevent same signal on same candle

        if (
            last_sent_signal == signal
            and last_sent_candle == candle_id
        ):

            logger.info(
                "Duplicate signal blocked."
            )

            return

        levels = calculate_trade_levels(
            analysis
        )

        if not levels:

            logger.error(
                "Trade levels calculation failed."
            )

            return

        message = build_signal_message(
            analysis,
            levels,
            candle_age
        )

        if send_telegram(message):

            last_sent_signal = signal
            last_sent_candle = candle_id

        return

    # =====================================================
    # HOLD
    # =====================================================

    hold_message = build_hold_message(
        analysis,
        candle_age
    )

    if hold_message:

        send_telegram(
            hold_message
        )


# =========================================================
# STARTUP MESSAGE
# =========================================================

def startup_message():

    current = now_utc()

    message = (
        "🟡 GOLD SIGNAL BOT STARTED\n\n"

        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n\n"

        "Professional Analysis Mode: ON\n"
        "Fresh Data Protection: ON\n"
        "Closed Candle Protection: ON\n"
        "Duplicate Signal Protection: ON\n"
        "HOLD Messages: OFF\n"
        "Automatic Trading: OFF\n\n"

        f"UTC Time:\n"
        f"{format_utc(current)}\n\n"

        f"Tehran Time:\n"
        f"{format_tehran(current)}\n\n"

        "Waiting for a fresh closed candle..."
    )

    send_telegram(
        message
    )


# =========================================================
# MAIN LOOP
# =========================================================

def main():

    logger.info(
        "=================================================="
    )

    logger.info(
        "GOLD SIGNAL BOT"
    )

    logger.info(
        "PROFESSIONAL ANALYSIS MODE"
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "Automatic Trading: DISABLED"
    )

    logger.info(
        "HOLD Messages: DISABLED"
    )

    logger.info(
        "Fresh Data Protection: ENABLED"
    )

    logger.info(
        "Closed Candle Protection: ENABLED"
    )

    logger.info(
        "Duplicate Signal Protection: ENABLED"
    )

    startup_message()

    while True:

        try:

            run_analysis()

        except Exception as exc:

            logger.exception(
                "Unexpected error: %s",
                exc
            )

        time.sleep(
            CHECK_SECONDS
        )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    main()
