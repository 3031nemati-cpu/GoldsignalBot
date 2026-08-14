import os
import time
import logging
from datetime import datetime, timezone

import requests

# ============================================================
# GOLD SIGNAL BOT - STRONG SIGNAL MODE
# ============================================================
# هدف این نسخه:
# - سیگنال کمتر، اما قوی‌تر و کنترل‌شده‌تر
# - فقط تحلیل کندل 5 دقیقه‌ای کاملاً بسته‌شده
# - حذف زمان UTC/تهران از پیام تلگرام
# - حذف پیام‌های HOLD
# - جلوگیری از سیگنال‌های تکراری و ضعیف
# - معاملات خودکار فعلاً خاموش است
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

# فقط یک بار برای هر کندل 5 دقیقه‌ای از Twelve Data داده می‌گیریم.
# این کار مصرف API را از حدود 1440 درخواست روزانه به حدود 288 می‌رساند.
CANDLE_SECONDS = 300
FETCH_DELAY_AFTER_CLOSE_SECONDS = 75
REQUEST_TIMEOUT_SECONDS = 20
MAX_CONSECUTIVE_API_FAILURES = 3

# فیلتر اصلی کیفیت
MIN_SIGNAL_SCORE = 70
MIN_SCORE_MARGIN = 10

# حداکثر سن مجاز آخرین کندل بسته‌شده
MAX_CANDLE_AGE_MINUTES = 7

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14

# اهداف و حد ضرر بر اساس ATR
TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0
SL_ATR = 1.5

# جلوگیری از ورود در ATR غیرعادی
MIN_ATR = 0.10
MAX_ATR = 20.0

# فعلاً خاموش؛ بعد از تست دمو می‌توان فعال کرد.
AUTOMATIC_TRADING = False
SEND_HOLD_MESSAGES = False


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(levelname)s | %(message)s",
)

log = logging.getLogger("GoldSignalBot")


# ============================================================
# ENVIRONMENT CHECK
# ============================================================

for name, value in (
    ("BOT_TOKEN", BOT_TOKEN),
    ("CHAT_ID", CHAT_ID),
    ("API_KEY", API_KEY),
):
    if not value:
        raise RuntimeError(f"{name} is missing")


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()
session.headers.update(
    {"User-Agent": "GoldSignalBot/StrongSignals/2.0"}
)


# ============================================================
# MEMORY
# ============================================================

last_processed_candle = None

last_sent_signal = None
last_sent_candle = None
last_sent_price = None
last_sent_score = None

# زمان درخواست بعدی داده از Twelve Data
next_fetch_time = 0.0
consecutive_api_failures = 0


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
                "text": message,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code == 429:
            log.error(
                "Twelve Data HTTP 429: API limit reached. "
                "No rapid retry will be performed."
            )
            return []

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            log.error("Telegram error: %s", data)
            return False

        log.info("Telegram signal sent.")
        return True

    except Exception as exc:
        log.error("Telegram error: %s", exc)
        return False


# ============================================================
# TIME PARSER
# ============================================================

def parse_utc(value):
    value = str(value).strip()

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


# ============================================================
# TWELVE DATA
# ============================================================

def get_candles(outputsize=150):
    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": outputsize,
        "apikey": API_KEY,
        "timezone": "UTC",
        "format": "JSON",
    }

    try:
        response = session.get(
            url,
            params=params,
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        if data.get("status") == "error":
            safe_data = dict(data) if isinstance(data, dict) else data
            if isinstance(safe_data, dict):
                safe_data.pop("apikey", None)
            log.error("Twelve Data error: %s", safe_data)
            return []

        if "values" not in data:
            log.error("Twelve Data returned no values: %s", data)
            return []

        candles = []

        for item in data["values"]:
            try:
                candles.append(
                    {
                        "datetime": parse_utc(item["datetime"]),
                        "open": float(item["open"]),
                        "high": float(item["high"]),
                        "low": float(item["low"]),
                        "close": float(item["close"]),
                    }
                )

            except (KeyError, TypeError, ValueError):
                continue

        candles.sort(key=lambda item: item["datetime"])

        return candles

    except requests.RequestException as exc:
        log.error("Market data request error: %s", exc)
        return []

    except Exception as exc:
        log.error("Market data error: %s", exc)
        return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(candles):
    now = datetime.now(timezone.utc)

    closed = []

    for candle in candles:

        # XAU/USD 5-minute candle
        # datetime = opening time of candle
        close_time = candle["datetime"].timestamp() + 300

        if now.timestamp() >= close_time:
            closed.append(candle)

    return closed


def candle_age_minutes(candle):
    now = datetime.now(timezone.utc)

    close_time = candle["datetime"].timestamp() + 300

    return max(
        0.0,
        (now.timestamp() - close_time) / 60.0,
    )


# ============================================================
# EMA
# ============================================================

def ema_series(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)

    current = sum(values[:period]) / period

    result = [None] * (period - 1)

    result.append(current)

    for price in values[period:]:

        current = (
            (price - current) * multiplier
        ) + current

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

    for index in range(1, len(values)):

        change = values[index] - values[index - 1]

        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for index in range(period, len(gains)):

        average_gain = (
            (average_gain * (period - 1))
            + gains[index]
        ) / period

        average_loss = (
            (average_loss * (period - 1))
            + losses[index]
        ) / period

    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss

    return 100.0 - (
        100.0 / (1.0 + relative_strength)
    )


# ============================================================
# ATR
# ============================================================

def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    true_ranges = []

    for index in range(1, len(candles)):

        high = candles[index]["high"]
        low = candles[index]["low"]
        previous_close = candles[index - 1]["close"]

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        true_ranges.append(true_range)

    value = sum(
        true_ranges[:period]
    ) / period

    for true_range in true_ranges[period:]:

        value = (
            (value * (period - 1))
            + true_range
        ) / period

    return value


# ============================================================
# CANDLE QUALITY
# ============================================================

def candle_body_ratio(candle):

    candle_range = candle["high"] - candle["low"]

    if candle_range <= 0:
        return 0.0

    body = abs(
        candle["close"] - candle["open"]
    )

    return body / candle_range


# ============================================================
# MARKET STRUCTURE
# ============================================================

def calculate_market_structure(candles):

    if len(candles) < 12:
        return "NEUTRAL", 0

    recent = candles[-6:]
    previous = candles[-12:-6]

    recent_high = max(
        c["high"] for c in recent
    )

    recent_low = min(
        c["low"] for c in recent
    )

    previous_high = max(
        c["high"] for c in previous
    )

    previous_low = min(
        c["low"] for c in previous
    )

    bullish = (
        recent_high > previous_high
        and recent_low > previous_low
    )

    bearish = (
        recent_high < previous_high
        and recent_low < previous_low
    )

    if bullish:
        return "BULLISH", 15

    if bearish:
        return "BEARISH", 15

    return "NEUTRAL", 0


# ============================================================
# MAIN ANALYSIS
# ============================================================

def calculate_analysis(candles):

    if len(candles) < 60:
        return None

    closes = [
        c["close"]
        for c in candles
    ]

    fast_series = ema_series(
        closes,
        EMA_FAST,
    )

    slow_series = ema_series(
        closes,
        EMA_SLOW,
    )

    if (
        len(fast_series) < 4
        or len(slow_series) < 4
    ):
        return None

    ema_fast = fast_series[-1]
    ema_slow = slow_series[-1]

    previous_fast = fast_series[-2]
    previous_slow = slow_series[-2]

    older_fast = fast_series[-4]
    older_slow = slow_series[-4]

    price = closes[-1]

    current_rsi = rsi(
        closes,
        RSI_PERIOD,
    )

    current_atr = atr(
        candles,
        ATR_PERIOD,
    )

    if (
        current_rsi is None
        or current_atr is None
    ):
        return None

    if not (
        MIN_ATR
        <= current_atr
        <= MAX_ATR
    ):

        return {
            "price": price,
            "ema9": ema_fast,
            "ema21": ema_slow,
            "rsi": current_rsi,
            "atr": current_atr,
            "bull": 0,
            "bear": 0,
            "trend": "خنثی",
            "score": 0,
            "structure": "NEUTRAL",
            "atr_valid": False,
        }

    bull = 0
    bear = 0

    # --------------------------------------------------------
    # 1. EMA ALIGNMENT = 25
    # --------------------------------------------------------

    if ema_fast > ema_slow:
        bull += 25

    elif ema_fast < ema_slow:
        bear += 25

    # --------------------------------------------------------
    # 2. PRICE VS EMA9 = 15
    # --------------------------------------------------------

    if price > ema_fast:
        bull += 15

    elif price < ema_fast:
        bear += 15

    # --------------------------------------------------------
    # 3. EMA9 SLOPE = 10
    # --------------------------------------------------------

    if ema_fast > previous_fast:
        bull += 10

    elif ema_fast < previous_fast:
        bear += 10

    # --------------------------------------------------------
    # 4. EMA21 SLOPE = 10
    # --------------------------------------------------------

    if ema_slow > previous_slow:
        bull += 10

    elif ema_slow < previous_slow:
        bear += 10

    # --------------------------------------------------------
    # 5. RSI = 15
    # --------------------------------------------------------

    if 52 <= current_rsi <= 68:
        bull += 15

    elif 32 <= current_rsi <= 48:
        bear += 15

    elif 68 < current_rsi <= 72:
        if ema_fast > ema_slow:
            bull += 8

    elif 28 <= current_rsi < 32:
        if ema_fast < ema_slow:
            bear += 8

    # جلوگیری از ورود خیلی دیرهنگام
    if current_rsi > 75:
        bull = max(
            0,
            bull - 15,
        )

    if current_rsi < 25:
        bear = max(
            0,
            bear - 15,
        )

    # --------------------------------------------------------
    # 6. MARKET STRUCTURE = 15
    # --------------------------------------------------------

    structure, structure_score = (
        calculate_market_structure(candles)
    )

    if structure == "BULLISH":
        bull += structure_score

    elif structure == "BEARISH":
        bear += structure_score

    # --------------------------------------------------------
    # 7. CANDLE QUALITY = 10
    # --------------------------------------------------------

    latest = candles[-1]

    body_ratio = candle_body_ratio(
        latest
    )

    if body_ratio >= 0.55:

        if latest["close"] > latest["open"]:
            bull += 10

        elif latest["close"] < latest["open"]:
            bear += 10

    # مهم:
    # یک کندل اصلاحی کوچک، روند قوی را به تنهایی حذف نمی‌کند.

    # --------------------------------------------------------
    # 8. EMA MOMENTUM = 5
    # --------------------------------------------------------

    fast_rising = (
        ema_fast > older_fast
    )

    fast_falling = (
        ema_fast < older_fast
    )

    slow_rising = (
        ema_slow > older_slow
    )

    slow_falling = (
        ema_slow < older_slow
    )

    if fast_rising and slow_rising:
        bull += 5

    if fast_falling and slow_falling:
        bear += 5

    bull = min(
        100,
        bull,
    )

    bear = min(
        100,
        bear,
    )

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if bull > bear:

        score = bull

        if score >= 85:
            trend = "صعودی بسیار قوی"

        elif score >= 70:
            trend = "صعودی قوی"

        elif score >= 60:
            trend = "صعودی"

        else:
            trend = "خنثی"

    elif bear > bull:

        score = bear

        if score >= 85:
            trend = "نزولی بسیار قوی"

        elif score >= 70:
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
        "ema9": ema_fast,
        "ema21": ema_slow,
        "rsi": current_rsi,
        "atr": current_atr,
        "bull": bull,
        "bear": bear,
        "trend": trend,
        "score": int(score),
        "structure": structure,
        "atr_valid": True,
    }


# ============================================================
# SIGNAL CONFIRMATION
# ============================================================

def confirm_signal(
    candles,
    analysis,
):

    if not analysis["atr_valid"]:
        return "HOLD", 0

    bull = analysis["bull"]
    bear = analysis["bear"]
    score = analysis["score"]

    if score < MIN_SIGNAL_SCORE:
        return "HOLD", 0

    if abs(bull - bear) < MIN_SCORE_MARGIN:
        return "HOLD", 0

    price = analysis["price"]
    ema9 = analysis["ema9"]
    ema21 = analysis["ema21"]
    current_rsi = analysis["rsi"]
    structure = analysis["structure"]

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if (
        bull > bear
        and bull >= MIN_SIGNAL_SCORE
        and ema9 > ema21
        and price > ema9
        and structure == "BULLISH"
        and 50 <= current_rsi <= 72
    ):
        return "BUY", 3

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if (
        bear > bull
        and bear >= MIN_SIGNAL_SCORE
        and ema9 < ema21
        and price < ema9
        and structure == "BEARISH"
        and 28 <= current_rsi <= 50
    ):
        return "SELL", 3

    return "HOLD", 0


# ============================================================
# TP / SL
# ============================================================

def calculate_levels(
    signal,
    price,
    current_atr,
    support,
    resistance,
):

    if signal == "BUY":

        return {
            "entry": price,

            "tp1": price
            + current_atr * TP1_ATR,

            "tp2": price
            + current_atr * TP2_ATR,

            "tp3": price
            + current_atr * TP3_ATR,

            "sl": price
            - current_atr * SL_ATR,

            "support": support,
            "resistance": resistance,
        }

    if signal == "SELL":

        return {
            "entry": price,

            "tp1": price
            - current_atr * TP1_ATR,

            "tp2": price
            - current_atr * TP2_ATR,

            "tp3": price
            - current_atr * TP3_ATR,

            "sl": price
            + current_atr * SL_ATR,

            "support": support,
            "resistance": resistance,
        }

    return None


# ============================================================
# REPEATED SIGNAL FILTER
# ============================================================

def repeated_signal_is_allowed(
    signal,
    price,
    current_atr,
    score,
):

    if last_sent_signal is None:
        return True

    # تغییر جهت همیشه اجازه دارد
    if signal != last_sent_signal:
        return True

    if (
        last_sent_price is None
        or last_sent_score is None
    ):
        return True

    price_move = abs(
        price - last_sent_price
    )

    # اگر قیمت حداقل 0.6 ATR حرکت کرده باشد
    if price_move >= current_atr * 0.60:
        return True

    # یا قدرت سیگنال حداقل 8 امتیاز بهتر شده باشد
    if score >= last_sent_score + 8:
        return True

    return False


# ============================================================
# TELEGRAM MESSAGE - PERSIAN
# ============================================================

def build_message(
    signal,
    analysis,
    levels,
    confirmation_count,
):

    if signal == "BUY":
        title = "🟢 سیگنال طلا — خرید"

    else:
        title = "🔴 سیگنال طلا — فروش"

    if analysis["score"] >= 85:
        quality = "بسیار قوی"

    elif analysis["score"] >= 75:
        quality = "قوی"

    else:
        quality = "معتبر"

    return f"""
{title}

🔹 نماد: XAU/USD
⏱ تایم‌فریم: ۵ دقیقه

💰 قیمت ورود: {levels["entry"]:.2f}

📊 روند: {analysis["trend"]}
⭐ امتیاز سیگنال: {analysis["score"]}/100
🏆 کیفیت: {quality}

📈 EMA 9: {analysis["ema9"]:.2f}
📉 EMA 21: {analysis["ema21"]:.2f}
📊 RSI: {analysis["rsi"]:.2f}
📏 ATR: {analysis["atr"]:.2f}

━━━━━━━━━━━━━━━━

🎯 هدف اول: {levels["tp1"]:.2f}
🎯 هدف دوم: {levels["tp2"]:.2f}
🎯 هدف سوم: {levels["tp3"]:.2f}

🛑 حد ضرر: {levels["sl"]:.2f}

━━━━━━━━━━━━━━━━

📉 حمایت: {levels["support"]:.2f}
📈 مقاومت: {levels["resistance"]:.2f}

✅ تأیید: {confirmation_count}/3
🔒 فقط بر اساس کندل بسته‌شده

⚠️ تحلیل تکنیکال است و فعلاً معامله خودکار فعال نیست.
""".strip()


# ============================================================
# ANALYZE
# ============================================================

def analyze(candles):
    """
    تحلیل فقط روی داده‌ای که در زمان‌بندی کنترل‌شده از Twelve Data گرفته شده.
    """

    global last_processed_candle
    global last_sent_signal
    global last_sent_candle
    global last_sent_price
    global last_sent_score

    if len(candles) < 60:
        log.warning(
            "کندل کافی دریافت نشد: %d",
            len(candles),
        )
        return

    closed = get_closed_candles(candles)

    if len(closed) < 60:
        log.warning(
            "کندل بسته‌شده کافی نیست: %d",
            len(closed),
        )
        return

    latest = closed[-1]

    candle_id = latest["datetime"].isoformat()

    # یک تحلیل برای هر کندل بسته‌شده
    if candle_id == last_processed_candle:
        log.info("این کندل قبلاً تحلیل شده است.")
        return

    age = candle_age_minutes(latest)

    log.info(
        "New closed 5m candle | age=%.2f min | candle=%s",
        age,
        candle_id,
    )

    if age > MAX_CANDLE_AGE_MINUTES:
        log.warning("کندل قدیمی رد شد.")
        last_processed_candle = candle_id
        return

    analysis = calculate_analysis(closed)

    if not analysis:
        log.warning("محاسبه اندیکاتورها ناموفق بود.")
        last_processed_candle = candle_id
        return

    recent = closed[-30:]

    support = min(c["low"] for c in recent)
    resistance = max(c["high"] for c in recent)

    signal, confirmation_count = confirm_signal(
        closed,
        analysis,
    )

    log.info(
        "Price=%.2f | EMA9=%.2f | EMA21=%.2f | RSI=%.2f | ATR=%.2f",
        analysis["price"],
        analysis["ema9"],
        analysis["ema21"],
        analysis["rsi"],
        analysis["atr"],
    )

    log.info(
        "Bull=%d | Bear=%d | Score=%d | Trend=%s | Structure=%s | Signal=%s",
        analysis["bull"],
        analysis["bear"],
        analysis["score"],
        analysis["trend"],
        analysis["structure"],
        signal,
    )

    # این کندل بررسی شد؛ از تحلیل مجدد آن جلوگیری می‌کنیم.
    last_processed_candle = candle_id

    if signal == "HOLD":
        log.info("شرایط سیگنال قوی وجود ندارد؛ پیام ارسال نشد.")
        return

    if not repeated_signal_is_allowed(
        signal,
        analysis["price"],
        analysis["atr"],
        analysis["score"],
    ):
        log.info("سیگنال تکراری و نزدیک به سیگنال قبلی حذف شد.")
        return

    levels = calculate_levels(
        signal,
        analysis["price"],
        analysis["atr"],
        support,
        resistance,
    )

    if not levels:
        return

    message = build_message(
        signal,
        analysis,
        levels,
        confirmation_count,
    )

    if send_telegram(message):
        last_sent_signal = signal
        last_sent_candle = candle_id
        last_sent_price = analysis["price"]
        last_sent_score = analysis["score"]

        log.info(
            "Signal delivered | %s | price=%.2f | score=%d",
            signal,
            analysis["price"],
            analysis["score"],
        )


def seconds_until_next_fetch():
    """
    فقط یک درخواست برای هر کندل 5 دقیقه‌ای.
    درخواست 75 ثانیه بعد از بسته‌شدن کندل انجام می‌شود تا
    تأخیر پردازش داده Twelve Data را در نظر بگیریم.
    """
    now = time.time()

    # زمان فعلی را روی بازه‌های 5 دقیقه‌ای می‌بریم.
    current_bucket = int(now // CANDLE_SECONDS)

    next_close = (current_bucket + 1) * CANDLE_SECONDS
    target = next_close + FETCH_DELAY_AFTER_CLOSE_SECONDS

    wait = target - now

    if wait <= 0:
        wait = CANDLE_SECONDS

    return wait


def fetch_and_analyze_once():
    """
    یک درخواست داده -> یک تحلیل.
    در صورت 429 یا خطای API، تا نوبت بعدی درخواست صبر می‌کنیم
    تا مصرف API بیشتر نشود.
    """
    global consecutive_api_failures

    candles = get_candles()

    if not candles:
        consecutive_api_failures += 1

        log.warning(
            "دریافت داده ناموفق بود | failure=%d/%d",
            consecutive_api_failures,
            MAX_CONSECUTIVE_API_FAILURES,
        )

        if consecutive_api_failures >= MAX_CONSECUTIVE_API_FAILURES:
            log.warning(
                "چند درخواست متوالی ناموفق بود. "
                "ربات تا نوبت بعدی API صبر می‌کند."
            )

        return False

    consecutive_api_failures = 0

    analyze(candles)

    return True


# ============================================================
# STARTUP
# ============================================================

def startup():

    log.info(
        "================================================"
    )

    log.info(
        "GOLD SIGNAL BOT - STRONG SIGNAL MODE"
    )

    log.info(
        "Professional Analysis: ON"
    )

    log.info(
        "Symbol: %s",
        SYMBOL,
    )

    log.info(
        "Timeframe: %s",
        INTERVAL,
    )

    log.info(
        "Minimum Signal Score: %d/100",
        MIN_SIGNAL_SCORE,
    )

    log.info(
        "Minimum Score Margin: %d",
        MIN_SCORE_MARGIN,
    )

    log.info(
        "API Schedule: 1 request per 5-minute candle",
    )

    log.info(
        "Fetch Delay After Candle Close: %d seconds",
        FETCH_DELAY_AFTER_CLOSE_SECONDS,
    )

    log.info(
        "Closed Candle Protection: ON"
    )

    log.info(
        "Strong Structure Confirmation: ON"
    )

    log.info(
        "Repeated Signal Filter: ON"
    )

    log.info(
        "Twelve Data API Protection: ON"
    )
    log.info(
        "One API request per 5-minute candle: ON"
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
        "================================================"
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    global next_fetch_time

    startup()

    # یک بار در شروع اجرا داده می‌گیریم.
    log.info("Initial market-data request...")
    fetch_and_analyze_once()

    # درخواست بعدی دقیقاً طبق چرخه 5 دقیقه‌ای برنامه‌ریزی می‌شود.
    next_fetch_time = time.time() + seconds_until_next_fetch()

    while True:
        try:
            now = time.time()

            if now >= next_fetch_time:
                log.info("Scheduled 5-minute market-data request...")
                fetch_and_analyze_once()

                # بعد از هر درخواست، نوبت بعدی دوباره محاسبه می‌شود.
                next_fetch_time = (
                    time.time() + seconds_until_next_fetch()
                )

            else:
                # خواب کوتاه فقط برای مدیریت زمان‌بندی محلی است؛
                # هیچ درخواست API در این فاصله ارسال نمی‌شود.
                sleep_for = min(
                    5.0,
                    max(0.5, next_fetch_time - now),
                )
                time.sleep(sleep_for)

        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break

        except Exception as exc:
            log.exception(
                "Unexpected error: %s",
                exc,
            )

            # در صورت خطای غیرمنتظره نیز از درخواست‌های پشت‌سرهم جلوگیری می‌کنیم.
            next_fetch_time = (
                time.time()
                + seconds_until_next_fetch()
            )


if __name__ == "__main__":
    main()
