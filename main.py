import os
import time
import logging
from datetime import datetime, timezone

import requests


# ============================================================
# GOLD SIGNAL BOT - PROFESSIONAL v3
# ============================================================
# XAU/USD - 5 Minute
#
# Indicators:
# EMA 9 / EMA 21
# RSI 14
# ATR 14
# ADX 14
# +DI / -DI
#
# Features:
# - Closed candle protection
# - Dynamic ATR targets
# - Dynamic Stop Loss
# - Controlled signal scoring
# - ADX as trend-strength filter, NOT a hard blocker
# - Candle confirmation
# - Extreme wick protection
# - Duplicate candle protection
# - Telegram Persian messages
# - Telegram error diagnostics
# - Automatic trading OFF
# - HOLD messages OFF
# - Reduced API requests to avoid rate limiting
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")


SYMBOL = "XAU/USD"
INTERVAL = "5min"


# IMPORTANT:
# Do not check Twelve Data every 30 seconds.
# It can cause "Many Requests".
CHECK_SECONDS = 300


# Minimum total score required
MIN_SIGNAL_SCORE = 60


# Minimum difference between BUY and SELL scores
MIN_DIRECTION_GAP = 5


# Maximum acceptable age of the latest closed candle
MAX_CANDLE_AGE_MINUTES = 8


# ATR targets
TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0

# Stop Loss
SL_ATR = 1.5


# Automatic trading remains OFF
AUTOMATIC_TRADING = False

# Do not send HOLD messages
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
    "User-Agent": "GoldSignalBot/3.0"
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
                "Telegram API rejected message: %s",
                data
            )

            return False

        log.info("Telegram message sent successfully.")

        return True

    except requests.exceptions.RequestException as e:

        log.error(
            "Telegram connection error: %s",
            e
        )

        return False

    except Exception as e:

        log.exception(
            "Telegram unexpected error: %s",
            e
        )

        return False


# ============================================================
# DATE PARSER
# ============================================================

def parse_utc(value):

    try:

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:

        return None


# ============================================================
# MARKET DATA
# ============================================================

def get_candles(outputsize=100):

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

        # Twelve Data error
        if data.get("status") == "error":

            log.error(
                "Twelve Data error: %s",
                data
            )

            return []

        # Rate limit / API message
        if "message" in data and "values" not in data:

            log.error(
                "Twelve Data message: %s",
                data.get("message")
            )

            return []

        values = data.get("values", [])

        if not values:

            log.warning(
                "Twelve Data returned zero candles."
            )

            return []

        result = []

        for item in values:

            try:

                dt = parse_utc(
                    item["datetime"]
                )

                if dt is None:
                    continue

                result.append({

                    "datetime": dt,

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

        log.info(
            "Market data received: %d candles",
            len(result)
        )

        return result

    except requests.exceptions.RequestException as e:

        log.error(
            "Market data request failed: %s",
            e
        )

        return []

    except Exception as e:

        log.exception(
            "Market data unexpected error: %s",
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

        # 5 minute candle
        close_time = (
            candle["datetime"].timestamp()
            + 300
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
        candle["datetime"].timestamp()
        + 300
    )

    age = (
        now.timestamp()
        - close_time
    ) / 60

    return max(0.0, age)


# ============================================================
# EMA
# ============================================================

def ema_series(values, period):

    if len(values) < period:

        return []

    multiplier = 2 / (period + 1)

    initial = sum(
        values[:period]
    ) / period

    result = (
        [None] * (period - 1)
        + [initial]
    )

    value = initial

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
            + value
        )

        result.append(value)

    return result


# ============================================================
# RSI
# ============================================================

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

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:

        return None

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        high = candles[i]["high"]
        low = candles[i]["low"]

        previous_close = (
            candles[i - 1]["close"]
        )

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

    atr_value = (
        sum(
            true_ranges[:period]
        ) / period
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


# ============================================================
# ADX / DI
# ============================================================

def calculate_adx(candles, period=14):

    if len(candles) < (
        period * 2 + 1
    ):

        return None

    tr_values = []
    plus_dm = []
    minus_dm = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        high = current["high"]
        low = current["low"]

        previous_high = previous["high"]
        previous_low = previous["low"]
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

        up_move = (
            high - previous_high
        )

        down_move = (
            previous_low - low
        )

        if (
            up_move > down_move
            and up_move > 0
        ):

            p_dm = up_move

        else:

            p_dm = 0.0

        if (
            down_move > up_move
            and down_move > 0
        ):

            m_dm = down_move

        else:

            m_dm = 0.0

        tr_values.append(tr)
        plus_dm.append(p_dm)
        minus_dm.append(m_dm)

    if len(tr_values) < period:

        return None

    atr_smoothed = (
        sum(tr_values[:period])
        / period
    )

    plus_smoothed = (
        sum(plus_dm[:period])
        / period
    )

    minus_smoothed = (
        sum(minus_dm[:period])
        / period
    )

    dx_values = []

    for i in range(
        period,
        len(tr_values)
    ):

        atr_smoothed = (
            (
                atr_smoothed
                * (period - 1)
            )
            + tr_values[i]
        ) / period

        plus_smoothed = (
            (
                plus_smoothed
                * (period - 1)
            )
            + plus_dm[i]
        ) / period

        minus_smoothed = (
            (
                minus_smoothed
                * (period - 1)
            )
            + minus_dm[i]
        ) / period

        if atr_smoothed == 0:

            plus_di = 0
            minus_di = 0

        else:

            plus_di = (
                100
                * plus_smoothed
                / atr_smoothed
            )

            minus_di = (
                100
                * minus_smoothed
                / atr_smoothed
            )

        denominator = (
            plus_di
            + minus_di
        )

        if denominator == 0:

            dx = 0

        else:

            dx = (
                100
                * abs(
                    plus_di
                    - minus_di
                )
                / denominator
            )

        dx_values.append(
            (
                dx,
                plus_di,
                minus_di
            )
        )

    if len(dx_values) < period:

        return None

    adx = sum(
        x[0]
        for x in dx_values[:period]
    ) / period

    latest_plus_di = dx_values[-1][1]
    latest_minus_di = dx_values[-1][2]

    for i in range(
        period,
        len(dx_values)
    ):

        adx = (
            (
                adx
                * (period - 1)
            )
            + dx_values[i][0]
        ) / period

    return {
        "adx": adx,
        "plus_di": latest_plus_di,
        "minus_di": latest_minus_di
    }


# ============================================================
# CANDLE INFORMATION
# ============================================================

def candle_direction(candle):

    if candle["close"] > candle["open"]:
        return "BULLISH"

    if candle["close"] < candle["open"]:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# WICK ANALYSIS
# ============================================================

def candle_wick_data(candle):

    body = abs(
        candle["close"]
        - candle["open"]
    )

    upper_wick = (
        candle["high"]
        - max(
            candle["open"],
            candle["close"]
        )
    )

    lower_wick = (
        min(
            candle["open"],
            candle["close"]
        )
        - candle["low"]
    )

    total_range = (
        candle["high"]
        - candle["low"]
    )

    if total_range <= 0:

        return {
            "body": 0,
            "upper": 0,
            "lower": 0,
            "range": 0
        }

    return {
        "body": body,
        "upper": upper_wick,
        "lower": lower_wick,
        "range": total_range
    }


# ============================================================
# ANALYSIS
# ============================================================

def calculate_analysis(candles):

    if len(candles) < 50:

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
        len(ema9_series) < 2
        or len(ema21_series) < 2
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

    rsi = calculate_rsi(
        closes,
        14
    )

    atr = calculate_atr(
        candles,
        14
    )

    adx_data = calculate_adx(
        candles,
        14
    )

    if (
        rsi is None
        or atr is None
        or adx_data is None
    ):

        return None

    adx = adx_data["adx"]
    plus_di = adx_data["plus_di"]
    minus_di = adx_data["minus_di"]

    bull = 0
    bear = 0

    # --------------------------------------------------------
    # EMA TREND
    # --------------------------------------------------------

    if ema9 > ema21:

        bull += 20

    elif ema9 < ema21:

        bear += 20


    # --------------------------------------------------------
    # PRICE POSITION
    # --------------------------------------------------------

    if price > ema9:

        bull += 15

    elif price < ema9:

        bear += 15


    # --------------------------------------------------------
    # EMA MOMENTUM
    # --------------------------------------------------------

    if ema9 > previous_ema9:

        bull += 10

    elif ema9 < previous_ema9:

        bear += 10


    if ema21 > previous_ema21:

        bull += 5

    elif ema21 < previous_ema21:

        bear += 5


    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if 52 <= rsi <= 68:

        bull += 15

    elif 32 <= rsi <= 48:

        bear += 15

    elif 48 < rsi < 52:

        # Neutral RSI
        pass

    # Extreme RSI is not automatically a reversal
    # but reduces confidence.

    if rsi >= 75:

        bull -= 5

    if rsi <= 25:

        bear -= 5


    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------
    # ADX measures strength, not direction.
    #
    # ADX >= 25:
    # strong trend
    #
    # ADX 20-25:
    # moderate trend
    #
    # ADX < 20:
    # ranging market
    #
    # IMPORTANT:
    # ADX < 20 DOES NOT completely block the signal.
    # It only reduces confidence.
    # This prevents the bot from becoming silent.
    # --------------------------------------------------------

    if adx >= 25:

        if plus_di > minus_di:

            bull += 15

        elif minus_di > plus_di:

            bear += 15

    elif adx >= 20:

        if plus_di > minus_di:

            bull += 8

        elif minus_di > plus_di:

            bear += 8

    else:

        # Weak trend / range
        # Small directional contribution only

        if plus_di > minus_di:

            bull += 3

        elif minus_di > plus_di:

            bear += 3


    # --------------------------------------------------------
    # DI DIRECTION
    # --------------------------------------------------------

    if plus_di > minus_di:

        bull += 5

    elif minus_di > plus_di:

        bear += 5


    # --------------------------------------------------------
    # LATEST CANDLE
    # --------------------------------------------------------

    latest = candles[-1]

    direction = candle_direction(
        latest
    )

    if direction == "BULLISH":

        bull += 10

    elif direction == "BEARISH":

        bear += 10


    # --------------------------------------------------------
    # SECOND LAST CANDLE
    # --------------------------------------------------------

    previous = candles[-2]

    previous_direction = (
        candle_direction(previous)
    )

    if previous_direction == "BULLISH":

        bull += 5

    elif previous_direction == "BEARISH":

        bear += 5


    # --------------------------------------------------------
    # LIMIT SCORES
    # --------------------------------------------------------

    bull = max(
        0,
        min(100, bull)
    )

    bear = max(
        0,
        min(100, bear)
    )


    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if bull > bear:

        score = bull

        if score >= 80:

            trend = "صعودی قوی"

        elif score >= 60:

            trend = "صعودی"

        else:

            trend = "خنثی"

    elif bear > bull:

        score = bear

        if score >= 80:

            trend = "نزولی قوی"

        elif score >= 60:

            trend = "نزولی"

        else:

            trend = "خنثی"

    else:

        score = 0
        trend = "خنثی"


    return {

        "price": price,

        "ema9": ema9,

        "ema21": ema21,

        "rsi": rsi,

        "atr": atr,

        "adx": adx,

        "plus_di": plus_di,

        "minus_di": minus_di,

        "bull": bull,

        "bear": bear,

        "score": int(score),

        "trend": trend
    }


# ============================================================
# SIGNAL CONFIRMATION
# ============================================================

def confirm_signal(candles, analysis):

    latest = candles[-1]

    direction = candle_direction(
        latest
    )

    bull = analysis["bull"]
    bear = analysis["bear"]

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if (
        bull > bear
        and direction == "BULLISH"
    ):

        gap = bull - bear

        if gap >= MIN_DIRECTION_GAP:

            return "BUY"


    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if (
        bear > bull
        and direction == "BEARISH"
    ):

        gap = bear - bull

        if gap >= MIN_DIRECTION_GAP:

            return "SELL"


    return "HOLD"


# ============================================================
# EXTREME WICK FILTER
# ============================================================

def wick_rejection(signal, candle):

    data = candle_wick_data(candle)

    body = data["body"]

    lower = data["lower"]
    upper = data["upper"]

    # If body is tiny, do not aggressively reject.
    if body <= 0:

        return False


    # --------------------------------------------------------
    # BUY
    #
    # Extremely long upper wick can mean sellers rejected
    # higher prices.
    # --------------------------------------------------------

    if signal == "BUY":

        if upper > body * 2.5:

            return True


    # --------------------------------------------------------
    # SELL
    #
    # Extremely long lower wick can mean buyers rejected
    # lower prices.
    # --------------------------------------------------------

    if signal == "SELL":

        if lower > body * 2.5:

            return True


    return False


# ============================================================
# DYNAMIC TARGETS
# ============================================================

def calculate_dynamic_targets(
    entry_price,
    atr,
    direction
):

    if direction == "BUY":

        tp1 = (
            entry_price
            + atr * TP1_ATR
        )

        tp2 = (
            entry_price
            + atr * TP2_ATR
        )

        tp3 = (
            entry_price
            + atr * TP3_ATR
        )

        sl = (
            entry_price
            - atr * SL_ATR
        )

    else:

        tp1 = (
            entry_price
            - atr * TP1_ATR
        )

        tp2 = (
            entry_price
            - atr * TP2_ATR
        )

        tp3 = (
            entry_price
            - atr * TP3_ATR
        )

        sl = (
            entry_price
            + atr * SL_ATR
        )


    return {

        "entry": round(
            entry_price,
            2
        ),

        "tp1": round(
            tp1,
            2
        ),

        "tp2": round(
            tp2,
            2
        ),

        "tp3": round(
            tp3,
            2
        ),

        "sl": round(
            sl,
            2
        )
    }


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def calculate_support_resistance(
    candles,
    count=20
):

    recent = candles[-count:]

    support = min(
        c["low"]
        for c in recent
    )

    resistance = max(
        c["high"]
        for c in recent
    )

    return support, resistance


# ============================================================
# SIGNAL QUALITY
# ============================================================

def signal_quality(score, adx):

    if (
        score >= 80
        and adx >= 25
    ):

        return "بسیار قوی"

    if (
        score >= 70
        and adx >= 20
    ):

        return "قوی"

    if score >= 60:

        return "متوسط رو به قوی"

    return "ضعیف"


# ============================================================
# PERSIAN TELEGRAM MESSAGE
# ============================================================

def build_message(
    signal,
    analysis,
    levels,
    support,
    resistance
):

    if signal == "BUY":

        title = "🟢 سیگنال خرید طلا"

        direction_text = "خرید"

    else:

        title = "🔴 سیگنال فروش طلا"

        direction_text = "فروش"


    quality = signal_quality(
        analysis["score"],
        analysis["adx"]
    )


    adx_status = (
        "روند قوی"
        if analysis["adx"] >= 25
        else
        "روند متوسط"
        if analysis["adx"] >= 20
        else
        "بازار کم‌قدرت / رنج"
    )


    return f"""
{title}

━━━━━━━━━━━━━━━━

📌 نماد: {SYMBOL}
⏱ تایم‌فریم: {INTERVAL}

💰 قیمت ورود: {levels["entry"]:.2f}

📊 جهت: {direction_text}
📈 روند: {analysis["trend"]}

⭐ امتیاز سیگنال:
{analysis["score"]}/100

💪 قدرت روند ADX:
{analysis["adx"]:.2f}

📌 وضعیت روند:
{adx_status}

+DI: {analysis["plus_di"]:.2f}
-DI: {analysis["minus_di"]:.2f}

━━━━━━━━━━━━━━━━

📊 اندیکاتورها

EMA 9:
{analysis["ema9"]:.2f}

EMA 21:
{analysis["ema21"]:.2f}

RSI:
{analysis["rsi"]:.2f}

ATR:
{analysis["atr"]:.2f}

━━━━━━━━━━━━━━━━

🎯 اهداف پویا

TP1:
{levels["tp1"]:.2f}

TP2:
{levels["tp2"]:.2f}

TP3:
{levels["tp3"]:.2f}

🛑 حد ضرر:
{levels["sl"]:.2f}

━━━━━━━━━━━━━━━━

📉 حمایت:
{support:.2f}

📈 مقاومت:
{resistance:.2f}

⭐ کیفیت سیگنال:
{quality}

━━━━━━━━━━━━━━━━

⚠️ این پیام تحلیل تکنیکال است.
معامله خودکار فعال نیست.
"""


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze():

    global last_processed_candle
    global last_sent_candle
    global last_sent_signal


    # --------------------------------------------------------
    # GET MARKET DATA
    # --------------------------------------------------------

    candles = get_candles(
        outputsize=100
    )

    if len(candles) < 50:

        log.warning(
            "Not enough candles: %d",
            len(candles)
        )

        return


    # --------------------------------------------------------
    # CLOSED CANDLES
    # --------------------------------------------------------

    closed = get_closed_candles(
        candles
    )

    if len(closed) < 50:

        log.warning(
            "Not enough CLOSED candles: %d",
            len(closed)
        )

        return


    latest = closed[-1]

    candle_id = (
        latest["datetime"].isoformat()
    )


    # --------------------------------------------------------
    # DUPLICATE CANDLE
    # --------------------------------------------------------

    if candle_id == last_processed_candle:

        return


    age = candle_age_minutes(
        latest
    )

    log.info(
        "New closed 5m candle: %s | age=%.2f min",
        candle_id,
        age
    )


    # --------------------------------------------------------
    # STALE CANDLE
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

    support, resistance = (
        calculate_support_resistance(
            closed,
            20
        )
    )


    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    signal = confirm_signal(
        closed,
        analysis
    )


    log.info(
        "Price=%.2f | EMA9=%.2f | EMA21=%.2f | RSI=%.2f | ATR=%.2f",
        analysis["price"],
        analysis["ema9"],
        analysis["ema21"],
        analysis["rsi"],
        analysis["atr"]
    )


    log.info(
        "Bull=%d | Bear=%d | Trend=%s | Score=%d | ADX=%.2f | +DI=%.2f | -DI=%.2f | Signal=%s",
        analysis["bull"],
        analysis["bear"],
        analysis["trend"],
        analysis["score"],
        analysis["adx"],
        analysis["plus_di"],
        analysis["minus_di"],
        signal
    )


    # --------------------------------------------------------
    # HOLD
    # --------------------------------------------------------

    if signal == "HOLD":

        log.info(
            "No actionable signal on this candle."
        )

        last_processed_candle = candle_id

        return


    # --------------------------------------------------------
    # SCORE FILTER
    # --------------------------------------------------------

    if analysis["score"] < MIN_SIGNAL_SCORE:

        log.info(
            "Signal rejected by score: %d < %d",
            analysis["score"],
            MIN_SIGNAL_SCORE
        )

        last_processed_candle = candle_id

        return


    # --------------------------------------------------------
    # DIRECTION GAP
    # --------------------------------------------------------

    gap = abs(
        analysis["bull"]
        - analysis["bear"]
    )

    if gap < MIN_DIRECTION_GAP:

        log.info(
            "Signal rejected by direction gap: %d < %d",
            gap,
            MIN_DIRECTION_GAP
        )

        last_processed_candle = candle_id

        return


    # --------------------------------------------------------
    # EXTREME WICK
    # --------------------------------------------------------

    if wick_rejection(
        signal,
        latest
    ):

        log.info(
            "Signal rejected by extreme wick filter."
        )

        last_processed_candle = candle_id

        return


    # --------------------------------------------------------
    # DYNAMIC TARGETS
    # --------------------------------------------------------

    levels = calculate_dynamic_targets(
        analysis["price"],
        analysis["atr"],
        signal
    )


    # --------------------------------------------------------
    # BUILD MESSAGE
    # --------------------------------------------------------

    message = build_message(
        signal,
        analysis,
        levels,
        support,
        resistance
    )


    # --------------------------------------------------------
    # SEND TELEGRAM
    # --------------------------------------------------------

    success = send_telegram(
        message
    )


    if success:

        last_sent_signal = signal
        last_sent_candle = candle_id
        last_processed_candle = candle_id

        log.info(
            "SIGNAL SENT SUCCESSFULLY | %s | candle=%s",
            signal,
            candle_id
        )

    else:

        log.error(
            "SIGNAL WAS NOT SENT TO TELEGRAM."
        )

        # We do not mark the candle as processed.
        # It can retry on the next scan.


# ============================================================
# STARTUP
# ============================================================

def startup():

    log.info(
        "================================================"
    )

    log.info(
        "GOLD SIGNAL BOT v3 STARTED"
    )

    log.info(
        "Professional Analysis: ON"
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
        "Scan interval: %d seconds",
        CHECK_SECONDS
    )

    log.info(
        "Minimum Signal Score: %d/100",
        MIN_SIGNAL_SCORE
    )

    log.info(
        "Minimum Direction Gap: %d",
        MIN_DIRECTION_GAP
    )

    log.info(
        "ADX: ON"
    )

    log.info(
        "Dynamic ATR Targets: ON"
    )

    log.info(
        "Dynamic Stop Loss: ON"
    )

    log.info(
        "Closed Candle Protection: ON"
    )

    log.info(
        "Extreme Wick Protection: ON"
    )

    log.info(
        "Duplicate Candle Protection: ON"
    )

    log.info(
        "HOLD Messages: OFF"
    )

    log.info(
        "Automatic Trading: OFF"
    )

    log.info(
        "Telegram language: Persian"
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

        time.sleep(
            CHECK_SECONDS
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
