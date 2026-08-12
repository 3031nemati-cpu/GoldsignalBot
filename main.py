import os
import time
import logging
from datetime import datetime, timezone

import requests


# ============================================================
# GOLD SIGNAL BOT — PROFESSIONAL 5 MIN VERSION
# ============================================================
#
# Market:
#   XAU/USD
#
# Timeframe:
#   5min
#
# Data:
#   Twelve Data
#
# Features:
#   - Closed candle only
#   - No UTC/Tehran timestamps in Telegram
#   - One analysis for each new closed candle
#   - Same-direction signals allowed on new candles
#   - EMA 9 / EMA 21
#   - RSI 14
#   - ATR 14
#   - Trend scoring
#   - Multi-confirmation system
#   - Support / Resistance
#   - TP1 / TP2 / TP3
#   - ATR Stop Loss
#   - Telegram retry
#   - Twelve Data error protection
#   - Reduced API usage
#   - Automatic trading disabled
#   - HOLD messages disabled
#
# IMPORTANT:
# This bot provides technical analysis only.
# It does NOT execute trades.
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

# Check every 120 seconds.
# This keeps API usage much lower than checking every 30 seconds.
CHECK_SECONDS = 120

# Minimum directional score required for a signal.
MIN_TREND_SCORE = 55

# Maximum acceptable age of a closed candle.
MAX_CANDLE_AGE_MINUTES = 6

# Minimum number of directional confirmations.
MIN_CONFIRMATIONS = 2


# ============================================================
# TP / SL SETTINGS
# ============================================================

TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0

SL_ATR = 1.5


# ============================================================
# BOT OPTIONS
# ============================================================

AUTOMATIC_TRADING = False
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
# ENVIRONMENT CHECK
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
    "User-Agent": "GoldSignalBot/Professional-5min"
})


# ============================================================
# STATE
# ============================================================

last_processed_candle = None
last_sent_candle = None
last_sent_signal = None


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

            log.error(
                "Telegram returned error: %s",
                data
            )

            return False

        log.info("Telegram signal sent successfully.")

        return True

    except requests.RequestException as e:

        log.error(
            "Telegram request error: %s",
            e
        )

        return False

    except Exception as e:

        log.exception(
            "Unexpected Telegram error: %s",
            e
        )

        return False


# ============================================================
# DATETIME
# ============================================================

def parse_utc(value):

    if not value:
        raise ValueError("Empty datetime")

    value = str(value).strip()

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:

        dt = datetime.fromisoformat(value)

    except ValueError:

        # Fallback for Twelve Data style datetime
        dt = datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S"
        )

    if dt.tzinfo is None:

        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(timezone.utc)


# ============================================================
# GET MARKET DATA
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

        # ----------------------------------------------------
        # API ERROR
        # ----------------------------------------------------

        if data.get("status") == "error":

            log.error(
                "Twelve Data API error: %s",
                data
            )

            return []

        if "values" not in data:

            log.error(
                "Twelve Data returned no values: %s",
                data
            )

            return []

        # ----------------------------------------------------
        # API CREDIT INFORMATION
        # ----------------------------------------------------

        credits_used = response.headers.get(
            "api-credits-used"
        )

        credits_left = response.headers.get(
            "api-credits-left"
        )

        if credits_used or credits_left:

            log.info(
                "Twelve Data credits | used=%s | left=%s",
                credits_used,
                credits_left
            )

        # ----------------------------------------------------
        # PARSE CANDLES
        # ----------------------------------------------------

        result = []

        for item in data.get("values", []):

            try:

                candle = {
                    "datetime": parse_utc(
                        item["datetime"]
                    ),

                    "open": float(
                        item["open"]
                    ),

                    "high": float(
                        item["high"]
                    ),

                    "low": float(
                        item["low"]
                    ),

                    "close": float(
                        item["close"]
                    )
                }

                result.append(candle)

            except (
                KeyError,
                ValueError,
                TypeError
            ):

                continue

        # Oldest -> newest
        result.sort(
            key=lambda x: x["datetime"]
        )

        return result

    except requests.RequestException as e:

        log.error(
            "Market data request failed: %s",
            e
        )

        return []

    except Exception as e:

        log.exception(
            "Market data parsing error: %s",
            e
        )

        return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(candles):

    now = datetime.now(
        timezone.utc
    )

    closed = []

    candle_seconds = 300

    for candle in candles:

        open_time = candle["datetime"]

        close_time = (
            open_time.timestamp()
            + candle_seconds
        )

        if now.timestamp() >= close_time:

            closed.append(candle)

    return closed


# ============================================================
# CANDLE AGE
# ============================================================

def candle_age_minutes(candle):

    now = datetime.now(
        timezone.utc
    )

    close_time = (
        candle["datetime"].timestamp()
        + 300
    )

    age = (
        now.timestamp()
        - close_time
    ) / 60.0

    return max(
        0.0,
        age
    )


# ============================================================
# EMA
# ============================================================

def ema_series(values, period):

    if len(values) < period:

        return []

    multiplier = 2 / (
        period + 1
    )

    initial = sum(
        values[:period]
    ) / period

    result = (
        [None] * (period - 1)
        + [initial]
    )

    current = initial

    for price in values[period:]:

        current = (
            (price - current)
            * multiplier
            + current
        )

        result.append(current)

    return result


# ============================================================
# RSI
# ============================================================

def rsi(values, period=14):

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

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    rs = (
        avg_gain
        / avg_loss
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

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        high = current["high"]
        low = current["low"]
        previous_close = previous["close"]

        tr = max(
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

        true_ranges.append(tr)

    value = (
        sum(
            true_ranges[:period]
        )
        / period
    )

    for tr in true_ranges[period:]:

        value = (
            (
                value
                * (period - 1)
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
# ANALYSIS
# ============================================================

def calculate_analysis(candles):

    if len(candles) < 40:

        return None

    closes = [
        c["close"]
        for c in candles
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
        len(ema9_series) < 3
        or len(ema21_series) < 3
    ):

        return None

    ema9 = ema9_series[-1]
    ema21 = ema21_series[-1]

    previous_ema9 = (
        ema9_series[-2]
    )

    previous_ema21 = (
        ema21_series[-2]
    )

    price = closes[-1]

    current_rsi = rsi(
        closes,
        14
    )

    current_atr = atr(
        candles,
        14
    )

    if (
        current_rsi is None
        or current_atr is None
    ):

        return None

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    bull_score = 0
    bear_score = 0

    confirmations_bull = []
    confirmations_bear = []

    # --------------------------------------------------------
    # 1. EMA TREND
    # --------------------------------------------------------

    if ema9 > ema21:

        bull_score += 25

        confirmations_bull.append(
            "EMA trend bullish"
        )

    elif ema9 < ema21:

        bear_score += 25

        confirmations_bear.append(
            "EMA trend bearish"
        )

    # --------------------------------------------------------
    # 2. PRICE VS EMA9
    # --------------------------------------------------------

    if price > ema9:

        bull_score += 15

        confirmations_bull.append(
            "Price above EMA9"
        )

    elif price < ema9:

        bear_score += 15

        confirmations_bear.append(
            "Price below EMA9"
        )

    # --------------------------------------------------------
    # 3. EMA9 MOMENTUM
    # --------------------------------------------------------

    if ema9 > previous_ema9:

        bull_score += 15

        confirmations_bull.append(
            "EMA9 rising"
        )

    elif ema9 < previous_ema9:

        bear_score += 15

        confirmations_bear.append(
            "EMA9 falling"
        )

    # --------------------------------------------------------
    # 4. EMA21 MOMENTUM
    # --------------------------------------------------------

    if ema21 > previous_ema21:

        bull_score += 10

        confirmations_bull.append(
            "EMA21 rising"
        )

    elif ema21 < previous_ema21:

        bear_score += 10

        confirmations_bear.append(
            "EMA21 falling"
        )

    # --------------------------------------------------------
    # 5. RSI
    # --------------------------------------------------------

    if 52 <= current_rsi <= 68:

        bull_score += 15

        confirmations_bull.append(
            "RSI bullish zone"
        )

    elif 32 <= current_rsi <= 48:

        bear_score += 15

        confirmations_bear.append(
            "RSI bearish zone"
        )

    # --------------------------------------------------------
    # EXTREME RSI PROTECTION
    # --------------------------------------------------------

    if current_rsi > 72:

        bull_score -= 10

    if current_rsi < 28:

        bear_score -= 10

    # --------------------------------------------------------
    # 6. LATEST CLOSED CANDLE
    #
    # Important:
    # Candle direction is NOT mandatory.
    # It only adds confirmation.
    # --------------------------------------------------------

    latest_direction = candle_direction(
        candles[-1]
    )

    if latest_direction == "BULLISH":

        bull_score += 10

        confirmations_bull.append(
            "Latest candle bullish"
        )

    elif latest_direction == "BEARISH":

        bear_score += 10

        confirmations_bear.append(
            "Latest candle bearish"
        )

    # --------------------------------------------------------
    # LIMIT SCORE
    # --------------------------------------------------------

    bull_score = max(
        0,
        min(
            100,
            bull_score
        )
    )

    bear_score = max(
        0,
        min(
            100,
            bear_score
        )
    )

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if bull_score > bear_score:

        score = bull_score

        if score >= 80:
            trend = "Strong Bullish"

        elif score >= 60:
            trend = "Bullish"

        else:
            trend = "Neutral"

    elif bear_score > bull_score:

        score = bear_score

        if score >= 80:
            trend = "Strong Bearish"

        elif score >= 60:
            trend = "Bearish"

        else:
            trend = "Neutral"

    else:

        score = 0
        trend = "Neutral"

    # --------------------------------------------------------
    # CONFIRMATION COUNT
    # --------------------------------------------------------

    if bull_score > bear_score:

        confirmation_count = len(
            confirmations_bull
        )

        direction = "BUY"

        confirmations = (
            confirmations_bull
        )

    elif bear_score > bull_score:

        confirmation_count = len(
            confirmations_bear
        )

        direction = "SELL"

        confirmations = (
            confirmations_bear
        )

    else:

        confirmation_count = 0
        direction = "HOLD"
        confirmations = []

    return {

        "price": price,

        "ema9": ema9,

        "ema21": ema21,

        "rsi": current_rsi,

        "atr": current_atr,

        "bull": bull_score,

        "bear": bear_score,

        "trend": trend,

        "score": int(score),

        "direction": direction,

        "confirmations": confirmations,

        "confirmation_count":
            confirmation_count,

        "candle_direction":
            latest_direction
    }


# ============================================================
# SIGNAL LEVELS
# ============================================================

def calculate_levels(
    signal,
    price,
    current_atr,
    support,
    resistance
):

    if signal == "BUY":

        return {

            "entry": price,

            "tp1":
                price
                + current_atr
                * TP1_ATR,

            "tp2":
                price
                + current_atr
                * TP2_ATR,

            "tp3":
                price
                + current_atr
                * TP3_ATR,

            "sl":
                price
                - current_atr
                * SL_ATR,

            "support": support,

            "resistance": resistance
        }

    if signal == "SELL":

        return {

            "entry": price,

            "tp1":
                price
                - current_atr
                * TP1_ATR,

            "tp2":
                price
                - current_atr
                * TP2_ATR,

            "tp3":
                price
                - current_atr
                * TP3_ATR,

            "sl":
                price
                + current_atr
                * SL_ATR,

            "support": support,

            "resistance": resistance
        }

    return None


# ============================================================
# SIGNAL QUALITY
# ============================================================

def signal_quality(
    score,
    confirmation_count
):

    if (
        score >= 75
        and confirmation_count >= 4
    ):

        return "HIGH"

    if (
        score >= 65
        and confirmation_count >= 3
    ):

        return "MEDIUM-HIGH"

    if (
        score >= 55
        and confirmation_count >= 2
    ):

        return "MEDIUM"

    return "LOW"


# ============================================================
# BUILD TELEGRAM MESSAGE
# ============================================================

def build_message(
    signal,
    analysis,
    levels,
    confirmation_count
):

    if signal == "BUY":

        title = (
            "🟢 GOLD SIGNAL — BUY"
        )

    else:

        title = (
            "🔴 GOLD SIGNAL — SELL"
        )

    quality = signal_quality(
        analysis["score"],
        confirmation_count
    )

    confirmation_text = "\n".join(
        [
            f"• {item}"
            for item in analysis[
                "confirmations"
            ][:4]
        ]
    )

    return f"""{title}

Symbol: {SYMBOL}
Timeframe: {INTERVAL}

🕯️ Based on latest CLOSED 5min candle

💰 Price: {analysis["price"]:.2f}

📊 Trend: {analysis["trend"]}
Trend Score: {analysis["score"]}/100

EMA 9: {analysis["ema9"]:.2f}
EMA 21: {analysis["ema21"]:.2f}
RSI: {analysis["rsi"]:.2f}
ATR: {analysis["atr"]:.2f}

━━━━━━━━━━━━━━━━

💰 Entry: {levels["entry"]:.2f}

🎯 TP1: {levels["tp1"]:.2f}
🎯 TP2: {levels["tp2"]:.2f}
🎯 TP3: {levels["tp3"]:.2f}

🛑 Stop Loss: {levels["sl"]:.2f}

━━━━━━━━━━━━━━━━

📉 Support: {levels["support"]:.2f}
📈 Resistance: {levels["resistance"]:.2f}

━━━━━━━━━━━━━━━━

✅ Confirmation: {confirmation_count}/4
⭐ Signal Quality: {quality}

🔎 Confirmations:
{confirmation_text}

⚠️ Technical analysis only
Not automatic trading."""


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze():

    global last_processed_candle
    global last_sent_candle
    global last_sent_signal

    candles = get_candles(
        outputsize=120
    )

    # --------------------------------------------------------
    # DATA CHECK
    # --------------------------------------------------------

    if len(candles) < 40:

        log.warning(
            "Not enough market candles: %d",
            len(candles)
        )

        return

    # --------------------------------------------------------
    # CLOSED CANDLES
    # --------------------------------------------------------

    closed = get_closed_candles(
        candles
    )

    if len(closed) < 40:

        log.warning(
            "Not enough CLOSED candles: %d",
            len(closed)
        )

        return

    # --------------------------------------------------------
    # LATEST CLOSED CANDLE
    # --------------------------------------------------------

    latest = closed[-1]

    candle_id = (
        latest["datetime"]
        .isoformat()
    )

    # --------------------------------------------------------
    # DUPLICATE PROTECTION
    # --------------------------------------------------------

    if (
        candle_id
        == last_processed_candle
    ):

        log.info(
            "Same closed candle already processed."
        )

        return

    age = candle_age_minutes(
        latest
    )

    log.info(
        "New CLOSED 5min candle | age=%.2f minutes",
        age
    )

    # --------------------------------------------------------
    # STALE CANDLE PROTECTION
    # --------------------------------------------------------

    if age > MAX_CANDLE_AGE_MINUTES:

        log.warning(
            "Closed candle rejected: "
            "too old (%.2f min).",
            age
        )

        last_processed_candle = (
            candle_id
        )

        return

    # --------------------------------------------------------
    # ANALYSIS
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

    signal = analysis[
        "direction"
    ]

    confirmation_count = (
        analysis[
            "confirmation_count"
        ]
    )

    # --------------------------------------------------------
    # LOG ANALYSIS
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
        "Trend=%s | Bull=%d | Bear=%d | Score=%d",
        analysis["trend"],
        analysis["bull"],
        analysis["bear"],
        analysis["score"]
    )

    log.info(
        "Direction=%s | Confirmations=%d/4",
        signal,
        confirmation_count
    )

    # --------------------------------------------------------
    # HOLD
    # --------------------------------------------------------

    if signal == "HOLD":

        log.info(
            "No directional advantage on this candle."
        )

        last_processed_candle = (
            candle_id
        )

        return

    # --------------------------------------------------------
    # SCORE FILTER
    # --------------------------------------------------------

    if analysis["score"] < MIN_TREND_SCORE:

        log.info(
            "Signal rejected: score %d < minimum %d",
            analysis["score"],
            MIN_TREND_SCORE
        )

        last_processed_candle = (
            candle_id
        )

        return

    # --------------------------------------------------------
    # CONFIRMATION FILTER
    # --------------------------------------------------------

    if (
        confirmation_count
        < MIN_CONFIRMATIONS
    ):

        log.info(
            "Signal rejected: confirmations %d < minimum %d",
            confirmation_count,
            MIN_CONFIRMATIONS
        )

        last_processed_candle = (
            candle_id
        )

        return

    # --------------------------------------------------------
    # LEVELS
    # --------------------------------------------------------

    levels = calculate_levels(

        signal,

        analysis["price"],

        analysis["atr"],

        support,

        resistance
    )

    if not levels:

        last_processed_candle = (
            candle_id
        )

        return

    # --------------------------------------------------------
    # DUPLICATE SIGNAL PROTECTION
    #
    # Same signal on a NEW candle is allowed.
    # Same signal on the SAME candle is not.
    # --------------------------------------------------------

    if (
        last_sent_candle
        == candle_id
    ):

        log.info(
            "Signal already sent for this candle."
        )

        last_processed_candle = (
            candle_id
        )

        return

    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    message = build_message(

        signal,

        analysis,

        levels,

        confirmation_count
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    sent = send_telegram(
        message
    )

    if sent:

        last_sent_signal = (
            signal
        )

        last_sent_candle = (
            candle_id
        )

        last_processed_candle = (
            candle_id
        )

        log.info(
            "SIGNAL SENT | %s | score=%d | confirmations=%d/4",
            signal,
            analysis["score"],
            confirmation_count
        )

    else:

        # Do NOT mark the candle as processed.
        # This allows another cycle to retry.
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
        "GOLD SIGNAL BOT — PROFESSIONAL VERSION"
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
        "Minimum Confirmations: %d/4",
        MIN_CONFIRMATIONS
    )

    log.info(
        "Market Scan Interval: %d seconds",
        CHECK_SECONDS
    )

    log.info(
        "Closed Candle Protection: ON"
    )

    log.info(
        "Duplicate Candle Protection: ON"
    )

    log.info(
        "Same Direction Signals on New Candles: ON"
    )

    log.info(
        "Telegram Retry: ON"
    )

    log.info(
        "HOLD Messages: OFF"
    )

    log.info(
        "Automatic Trading: DISABLED"
    )

    log.info(
        "Telegram Time Display: OFF"
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
                "Bot stopped manually."
            )

            break

        except Exception as e:

            log.exception(
                "Unexpected error: %s",
                e
            )

        # ----------------------------------------------------
        # Wait before next API request.
        # ----------------------------------------------------

        time.sleep(
            CHECK_SECONDS
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
