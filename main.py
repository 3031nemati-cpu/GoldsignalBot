import os
import time
import logging
from datetime import datetime, timezone

import requests


# ============================================================
# GOLD SIGNAL BOT
# PROFESSIONAL ANALYSIS + TUX EMA SCALPER FILTER
# ============================================================
#
# Symbol: XAU/USD
# Timeframe: 5min
#
# Main filters:
#   EMA 9 / EMA 21
#   RSI 14
#   ADX 14
#   TUX EMA Scalper style filter
#
# TUX FILTER:
#   Length = 12
#   Source = HLC3
#
# Dynamic targets:
#   TP1 = 1 ATR
#   TP2 = 2 ATR
#   TP3 = 3 ATR
#   SL  = 1.5 ATR
#
# Automatic trading: OFF
# HOLD messages: OFF
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")


SYMBOL = "XAU/USD"
INTERVAL = "5min"

# Check every 30 seconds, but analyze only once per closed candle
CHECK_SECONDS = 30

# Minimum final trend score
MIN_TREND_SCORE = 55

# Reject very old candles
MAX_CANDLE_AGE_MINUTES = 8


# ============================================================
# INDICATOR SETTINGS
# ============================================================

EMA_FAST = 9
EMA_SLOW = 21

RSI_PERIOD = 14
ADX_PERIOD = 14
ATR_PERIOD = 14


# ============================================================
# TUX EMA SCALPER FILTER
# ============================================================

TUX_LENGTH = 12
TUX_SOURCE = "HLC3"

# Previous close lookback used as support/resistance reference
TUX_LOOKBACK = 8


# ============================================================
# ATR TARGET SETTINGS
# ============================================================

TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0

SL_ATR = 1.5


# ============================================================
# TRADING MODE
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
# ENVIRONMENT VALIDATION
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
    "User-Agent": "GoldSignalBot/Professional-TUX"
})


# ============================================================
# STATE
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
# DATETIME
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

        raise ValueError(f"Invalid datetime: {value}")


# ============================================================
# MARKET DATA
# ============================================================

def get_candles(outputsize=150):

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

        if data.get("status") == "error":

            log.error(
                "Twelve Data error: %s",
                data
            )

            return []

        values = data.get("values", [])

        result = []

        for item in values:

            try:

                result.append({

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

        # 5-minute candle
        # datetime = candle OPEN time
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
    ) / 60.0

    return max(0.0, age)


# ============================================================
# EMA
# ============================================================

def ema_series(values, period):

    if len(values) < period:

        return []

    multiplier = 2 / (period + 1)

    ema_value = sum(
        values[:period]
    ) / period

    result = (
        [None] * (period - 1)
        + [ema_value]
    )

    for value in values[period:]:

        ema_value = (
            (value - ema_value)
            * multiplier
            + ema_value
        )

        result.append(
            ema_value
        )

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

    rs = avg_gain / avg_loss

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


# ============================================================
# HLC3
# ============================================================

def hlc3(candle):

    return (
        candle["high"]
        + candle["low"]
        + candle["close"]
    ) / 3.0


def hlc3_series(candles):

    return [
        hlc3(c)
        for c in candles
    ]


# ============================================================
# TUX EMA SCALPER
# ============================================================

def tux_ema_series(candles):

    source = hlc3_series(candles)

    return ema_series(
        source,
        TUX_LENGTH
    )


def tux_filter(candles):

    if len(candles) < TUX_LENGTH + 3:

        return {
            "signal": "NONE",
            "ema": None,
            "source": None,
            "previous_source": None,
            "previous_ema": None,
            "cross_up": False,
            "cross_down": False,
            "uptrend": False,
            "downtrend": False
        }

    tux_ema = tux_ema_series(candles)

    if len(tux_ema) < 3:

        return {
            "signal": "NONE",
            "ema": None,
            "source": None,
            "previous_source": None,
            "previous_ema": None,
            "cross_up": False,
            "cross_down": False,
            "uptrend": False,
            "downtrend": False
        }

    source_now = hlc3(candles[-1])
    source_prev = hlc3(candles[-2])

    ema_now = tux_ema[-1]
    ema_prev = tux_ema[-2]

    if ema_now is None or ema_prev is None:

        return {
            "signal": "NONE",
            "ema": None,
            "source": source_now,
            "previous_source": source_prev,
            "previous_ema": ema_prev,
            "cross_up": False,
            "cross_down": False,
            "uptrend": False,
            "downtrend": False
        }

    cross_up = (
        source_prev <= ema_prev
        and source_now > ema_now
    )

    cross_down = (
        source_prev >= ema_prev
        and source_now < ema_now
    )

    uptrend = source_now > ema_now
    downtrend = source_now < ema_now

    if cross_up and uptrend:

        signal = "BUY"

    elif cross_down and downtrend:

        signal = "SELL"

    elif uptrend:

        signal = "BUY_BIAS"

    elif downtrend:

        signal = "SELL_BIAS"

    else:

        signal = "NEUTRAL"

    return {

        "signal": signal,

        "ema": ema_now,

        "source": source_now,

        "previous_source": source_prev,

        "previous_ema": ema_prev,

        "cross_up": cross_up,

        "cross_down": cross_down,

        "uptrend": uptrend,

        "downtrend": downtrend
    }


# ============================================================
# ADX
# ============================================================

def adx(candles, period=14):

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

        high_diff = (
            current["high"]
            - previous["high"]
        )

        low_diff = (
            previous["low"]
            - current["low"]
        )

        if (
            high_diff > low_diff
            and high_diff > 0
        ):

            pdm = high_diff

        else:

            pdm = 0.0

        if (
            low_diff > high_diff
            and low_diff > 0
        ):

            mdm = low_diff

        else:

            mdm = 0.0

        tr = max(

            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            )
        )

        tr_values.append(tr)
        plus_dm.append(pdm)
        minus_dm.append(mdm)

    if len(tr_values) < period:

        return None

    atr_value = sum(
        tr_values[:period]
    )

    plus_value = sum(
        plus_dm[:period]
    )

    minus_value = sum(
        minus_dm[:period]
    )

    dx_values = []

    for i in range(
        period,
        len(tr_values)
    ):

        atr_value = (
            atr_value
            - (atr_value / period)
            + tr_values[i]
        )

        plus_value = (
            plus_value
            - (plus_value / period)
            + plus_dm[i]
        )

        minus_value = (
            minus_value
            - (minus_value / period)
            + minus_dm[i]
        )

        if atr_value == 0:

            continue

        plus_di = (
            100
            * plus_value
            / atr_value
        )

        minus_di = (
            100
            * minus_value
            / atr_value
        )

        denominator = (
            plus_di
            + minus_di
        )

        if denominator == 0:

            dx = 0.0

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

    adx_value = sum(
        x[0]
        for x in dx_values[:period]
    ) / period

    last_plus_di = None
    last_minus_di = None

    for dx, pdi, mdi in dx_values[period:]:

        adx_value = (
            (
                adx_value
                * (period - 1)
            )
            + dx
        ) / period

        last_plus_di = pdi
        last_minus_di = mdi

    if last_plus_di is None:

        last_plus_di = dx_values[-1][1]
        last_minus_di = dx_values[-1][2]

    return {
        "adx": adx_value,
        "plus_di": last_plus_di,
        "minus_di": last_minus_di
    }


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
# CANDLE SHADOW FILTER
# ============================================================

def candle_has_bad_shadow(
    candle,
    direction
):

    body = abs(
        candle["close"]
        - candle["open"]
    )

    if body <= 0:

        return False

    upper_shadow = (
        candle["high"]
        - max(
            candle["open"],
            candle["close"]
        )
    )

    lower_shadow = (
        min(
            candle["open"],
            candle["close"]
        )
        - candle["low"]
    )

    # For BUY:
    # Long upper shadow can indicate
    # rejection at the top.

    if direction == "BUY":

        if upper_shadow > body * 1.5:

            return True

    # For SELL:
    # Long lower shadow can indicate
    # buyers entering at the bottom.

    if direction == "SELL":

        if lower_shadow > body * 1.5:

            return True

    return False


# ============================================================
# ANALYSIS
# ============================================================

def calculate_analysis(candles):

    closes = [
        c["close"]
        for c in candles
    ]

    ema9_series = ema_series(
        closes,
        EMA_FAST
    )

    ema21_series = ema_series(
        closes,
        EMA_SLOW
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

    rsi_value = rsi(
        closes,
        RSI_PERIOD
    )

    atr_value = atr(
        candles,
        ATR_PERIOD
    )

    adx_data = adx(
        candles,
        ADX_PERIOD
    )

    tux_data = tux_filter(
        candles
    )

    if (
        rsi_value is None
        or atr_value is None
        or adx_data is None
        or tux_data["ema"] is None
    ):

        return None


    # ========================================================
    # SCORE
    # ========================================================

    bull = 0
    bear = 0


    # EMA 9 / EMA 21
    if ema9 > ema21:

        bull += 20

    elif ema9 < ema21:

        bear += 20


    # Price vs EMA9
    if price > ema9:

        bull += 10

    elif price < ema9:

        bear += 10


    # EMA9 momentum
    if ema9 > previous_ema9:

        bull += 10

    elif ema9 < previous_ema9:

        bear += 10


    # EMA21 momentum
    if ema21 > previous_ema21:

        bull += 10

    elif ema21 < previous_ema21:

        bear += 10


    # RSI
    if 50 <= rsi_value <= 68:

        bull += 15

    elif 32 <= rsi_value < 50:

        bear += 15


    # Avoid extreme RSI
    if rsi_value > 72:

        bull -= 10

    if rsi_value < 28:

        bear -= 10


    # ADX strength
    adx_value = adx_data["adx"]

    plus_di = adx_data["plus_di"]
    minus_di = adx_data["minus_di"]


    if adx_value >= 25:

        if plus_di > minus_di:

            bull += 15

        elif minus_di > plus_di:

            bear += 15

    elif adx_value >= 20:

        if plus_di > minus_di:

            bull += 8

        elif minus_di > plus_di:

            bear += 8


    # TUX FILTER
    #
    # Cross = stronger confirmation
    # Bias = weaker confirmation

    if tux_data["cross_up"]:

        bull += 15

    elif tux_data["cross_down"]:

        bear += 15

    elif tux_data["uptrend"]:

        bull += 7

    elif tux_data["downtrend"]:

        bear += 7


    # Latest candle
    latest_direction = candle_direction(
        candles[-1]
    )

    if latest_direction == "BULLISH":

        bull += 5

    elif latest_direction == "BEARISH":

        bear += 5


    bull = max(
        0,
        min(100, bull)
    )

    bear = max(
        0,
        min(100, bear)
    )


    if bull > bear:

        score = bull

        if score >= 80:

            trend = "Strong Bullish"

        elif score >= 60:

            trend = "Bullish"

        else:

            trend = "Neutral"


    elif bear > bull:

        score = bear

        if score >= 80:

            trend = "Strong Bearish"

        elif score >= 60:

            trend = "Bearish"

        else:

            trend = "Neutral"


    else:

        score = 0

        trend = "Neutral"


    return {

        "price": price,

        "ema9": ema9,

        "ema21": ema21,

        "rsi": rsi_value,

        "atr": atr_value,

        "adx": adx_value,

        "plus_di": plus_di,

        "minus_di": minus_di,

        "bull": bull,

        "bear": bear,

        "trend": trend,

        "score": int(score),

        "tux_ema": tux_data["ema"],

        "tux_source": tux_data["source"],

        "tux_signal": tux_data["signal"],

        "tux_cross_up": tux_data["cross_up"],

        "tux_cross_down": tux_data["cross_down"]
    }


# ============================================================
# SIGNAL CONFIRMATION
# ============================================================

def confirm_signal(
    candles,
    analysis
):

    latest = candles[-1]

    bullish_candle = (
        latest["close"]
        > latest["open"]
    )

    bearish_candle = (
        latest["close"]
        < latest["open"]
    )


    # ========================================================
    # BUY
    # ========================================================

    if (
        analysis["bull"]
        > analysis["bear"]
        and bullish_candle
    ):

        # TUX must at least have bullish bias
        if (
            analysis["tux_signal"]
            in ["BUY", "BUY_BIAS"]
        ):

            if not candle_has_bad_shadow(
                latest,
                "BUY"
            ):

                return "BUY", 1


    # ========================================================
    # SELL
    # ========================================================

    if (
        analysis["bear"]
        > analysis["bull"]
        and bearish_candle
    ):

        # TUX must at least have bearish bias
        if (
            analysis["tux_signal"]
            in ["SELL", "SELL_BIAS"]
        ):

            if not candle_has_bad_shadow(
                latest,
                "SELL"
            ):

                return "SELL", 1


    return "HOLD", 0


# ============================================================
# DYNAMIC LEVELS
# ============================================================

def calculate_dynamic_targets(
    entry_price,
    atr_value,
    direction
):

    if direction == "BUY":

        tp1 = (
            entry_price
            + atr_value * TP1_ATR
        )

        tp2 = (
            entry_price
            + atr_value * TP2_ATR
        )

        tp3 = (
            entry_price
            + atr_value * TP3_ATR
        )

        sl = (
            entry_price
            - atr_value * SL_ATR
        )

    else:

        tp1 = (
            entry_price
            - atr_value * TP1_ATR
        )

        tp2 = (
            entry_price
            - atr_value * TP2_ATR
        )

        tp3 = (
            entry_price
            - atr_value * TP3_ATR
        )

        sl = (
            entry_price
            + atr_value * SL_ATR
        )

    return {

        "entry": entry_price,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3,

        "sl": sl
    }


# ============================================================
# MESSAGE
# ============================================================

def build_message(
    signal,
    analysis,
    levels,
    confirmation
):

    if signal == "BUY":

        title = (
            "🟢 سیگنال طلا — خرید"
        )

    else:

        title = (
            "🔴 سیگنال طلا — فروش"
        )


    if analysis["score"] >= 75:

        quality = "قوی"

    elif analysis["score"] >= 65:

        quality = "خوب"

    else:

        quality = "متوسط"


    tux_direction = (
        "صعودی"
        if analysis["tux_signal"]
        in ["BUY", "BUY_BIAS"]
        else "نزولی"
        if analysis["tux_signal"]
        in ["SELL", "SELL_BIAS"]
        else "خنثی"
    )


    return f"""
{title}

نماد: XAU/USD
تایم‌فریم: 5 دقیقه

💰 قیمت: {analysis["price"]:.2f}

📊 روند: {analysis["trend"]}
امتیاز روند: {analysis["score"]}/100

EMA 9: {analysis["ema9"]:.2f}
EMA 21: {analysis["ema21"]:.2f}

RSI: {analysis["rsi"]:.2f}

ADX: {analysis["adx"]:.2f}
+DI: {analysis["plus_di"]:.2f}
-DI: {analysis["minus_di"]:.2f}

━━━━━━━━━━━━━━━━

🔹 فیلتر TUX EMA Scalper

Length: 12
Source: HLC3
TUX EMA: {analysis["tux_ema"]:.2f}
وضعیت TUX: {tux_direction}

━━━━━━━━━━━━━━━━

💵 ورود: {levels["entry"]:.2f}

🎯 هدف اول: {levels["tp1"]:.2f}
🎯 هدف دوم: {levels["tp2"]:.2f}
🎯 هدف سوم: {levels["tp3"]:.2f}

🛑 حد ضرر: {levels["sl"]:.2f}

━━━━━━━━━━━━━━━━

✅ تأیید کندل: {confirmation}/1
⭐ کیفیت سیگنال: {quality}

📌 اهداف بر اساس ATR محاسبه شده‌اند.

⚠️ تحلیل تکنیکال است و معاملات خودکار فعال نیست.
""".strip()


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze():

    global last_processed_candle
    global last_sent_signal
    global last_sent_candle


    candles = get_candles(
        outputsize=150
    )


    if len(candles) < 60:

        log.warning(
            "Not enough market candles: %d",
            len(candles)
        )

        return


    closed = get_closed_candles(
        candles
    )


    if len(closed) < 60:

        log.warning(
            "Not enough CLOSED candles: %d",
            len(closed)
        )

        return


    latest = closed[-1]


    candle_id = (
        latest["datetime"]
        .isoformat()
    )


    # ========================================================
    # DUPLICATE PROTECTION
    # ========================================================

    if candle_id == last_processed_candle:

        return


    age = candle_age_minutes(
        latest
    )


    log.info(
        "New closed 5m candle | %s | age=%.2f min",
        candle_id,
        age
    )


    if age > MAX_CANDLE_AGE_MINUTES:

        log.warning(
            "Candle rejected as stale."
        )

        last_processed_candle = candle_id

        return


    # ========================================================
    # CALCULATE
    # ========================================================

    analysis = calculate_analysis(
        closed
    )


    if not analysis:

        log.warning(
            "Indicator calculation failed."
        )

        last_processed_candle = candle_id

        return


    # ========================================================
    # SUPPORT / RESISTANCE
    # ========================================================

    recent = closed[-20:]


    support = min(
        c["low"]
        for c in recent
    )


    resistance = max(
        c["high"]
        for c in recent
    )


    # ========================================================
    # SIGNAL
    # ========================================================

    signal, confirmation = (
        confirm_signal(
            closed,
            analysis
        )
    )


    # ========================================================
    # LOG
    # ========================================================

    log.info(
        "Price=%.2f | EMA9=%.2f | EMA21=%.2f | RSI=%.2f | ATR=%.2f",
        analysis["price"],
        analysis["ema9"],
        analysis["ema21"],
        analysis["rsi"],
        analysis["atr"]
    )


    log.info(
        "Bull=%d | Bear=%d | Trend=%s | Score=%d | ADX=%.2f | +DI=%.2f | -DI=%.2f",
        analysis["bull"],
        analysis["bear"],
        analysis["trend"],
        analysis["score"],
        analysis["adx"],
        analysis["plus_di"],
        analysis["minus_di"]
    )


    log.info(
        "TUX | Length=%d | Source=%s | EMA=%.2f | Signal=%s | CrossUp=%s | CrossDown=%s",
        TUX_LENGTH,
        TUX_SOURCE,
        analysis["tux_ema"],
        analysis["tux_signal"],
        analysis["tux_cross_up"],
        analysis["tux_cross_down"]
    )


    log.info(
        "Final Signal=%s | Confirmation=%d/1",
        signal,
        confirmation
    )


    # ========================================================
    # HOLD
    # ========================================================

    if signal == "HOLD":

        log.info(
            "No actionable signal on this closed candle."
        )

        last_processed_candle = candle_id

        return


    # ========================================================
    # MINIMUM SCORE
    # ========================================================

    if analysis["score"] < MIN_TREND_SCORE:

        log.info(
            "Signal rejected: score=%d < minimum=%d",
            analysis["score"],
            MIN_TREND_SCORE
        )

        last_processed_candle = candle_id

        return


    # ========================================================
    # DIRECTION VALIDATION
    # ========================================================

    if (
        signal == "BUY"
        and analysis["bull"]
        <= analysis["bear"]
    ):

        last_processed_candle = candle_id

        return


    if (
        signal == "SELL"
        and analysis["bear"]
        <= analysis["bull"]
    ):

        last_processed_candle = candle_id

        return


    # ========================================================
    # DYNAMIC TARGETS
    # ========================================================

    levels = calculate_dynamic_targets(

        analysis["price"],

        analysis["atr"],

        signal
    )


    # ========================================================
    # MESSAGE
    # ========================================================

    message = build_message(

        signal,

        analysis,

        levels,

        confirmation
    )


    # ========================================================
    # SEND
    # ========================================================

    if send_telegram(message):

        last_sent_signal = signal

        last_sent_candle = candle_id

        last_processed_candle = candle_id

        log.info(
            "Signal delivered successfully | %s | candle=%s",
            signal,
            candle_id
        )

    else:

        log.warning(
            "Telegram failed. Candle will be retried."
        )


# ============================================================
# STARTUP
# ============================================================

def startup():

    log.info(
        "================================================"
    )

    log.info(
        "GOLD SIGNAL BOT - PROFESSIONAL"
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
        "EMA: %d / %d",
        EMA_FAST,
        EMA_SLOW
    )

    log.info(
        "RSI Period: %d",
        RSI_PERIOD
    )

    log.info(
        "ADX Period: %d",
        ADX_PERIOD
    )

    log.info(
        "TUX EMA Scalper: ON"
    )

    log.info(
        "TUX Length: %d",
        TUX_LENGTH
    )

    log.info(
        "TUX Source: %s",
        TUX_SOURCE
    )

    log.info(
        "TUX Lookback: %d",
        TUX_LOOKBACK
    )

    log.info(
        "ATR TP: %.1f / %.1f / %.1f",
        TP1_ATR,
        TP2_ATR,
        TP3_ATR
    )

    log.info(
        "ATR SL: %.1f",
        SL_ATR
    )

    log.info(
        "Minimum Trend Score: %d",
        MIN_TREND_SCORE
    )

    log.info(
        "Closed Candle Protection: ON"
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
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
