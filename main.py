import os
import time
import requests

# =========================================================
# CONFIGURATION
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

# بررسی بازار هر 5 دقیقه
CHECK_SECONDS = 300

# حداقل امتیاز برای صدور سیگنال
MIN_TREND_SCORE = 60

# تعداد کندل بسته شده برای تأیید سیگنال
CONFIRMATION_CANDLES = 2

# اندیکاتورها
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14

# محدوده حمایت و مقاومت
SR_LOOKBACK = 30

# TP و SL بر اساس ATR
TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0
SL_ATR = 1.5

TIMEOUT = 20


# =========================================================
# STATE
# =========================================================

last_signal = None
pending_signal = None
pending_count = 0
last_candle_time = None
weakening_sent_for = None


# =========================================================
# ENVIRONMENT CHECK
# =========================================================

def require_env():

    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not CHAT_ID:
        missing.append("CHAT_ID")

    if not API_KEY:
        missing.append("API_KEY")

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )


# =========================================================
# GET MARKET DATA
# =========================================================

def get_candles():

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": 100,
        "order": "desc",
        "timezone": "UTC",
        "apikey": API_KEY
    }

    response = requests.get(
        url,
        params=params,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            data.get(
                "message",
                "Twelve Data API error"
            )
        )

    values = data.get("values", [])

    if len(values) < 40:
        raise RuntimeError(
            f"Not enough candles: {len(values)}"
        )

    candles = []

    # تبدیل از جدید به قدیم
    for row in reversed(values):

        candles.append({
            "datetime": row["datetime"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"])
        })

    # =====================================================
    # IMPORTANT:
    # آخرین کندل ممکن است هنوز در حال تشکیل باشد.
    # بنابراین برای تحلیل اصلی آن را حذف می‌کنیم.
    # =====================================================

    return candles[:-1]


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    value = sum(values[:period]) / period

    for price in values[period:]:

        value = (
            (price - value) * multiplier
            + value
        )

    return value


# =========================================================
# RSI
# =========================================================

def rsi(values, period=14):

    if len(values) <= period:
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

    return 100 - (100 / (1 + rs))


# =========================================================
# ATR
# =========================================================

def atr(candles, period=14):

    if len(candles) <= period:
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

    value = sum(
        true_ranges[:period]
    ) / period

    for tr in true_ranges[period:]:

        value = (
            (value * (period - 1))
            + tr
        ) / period

    return value


# =========================================================
# MARKET ANALYSIS
# =========================================================

def analyze(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

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

    current = candles[-1]
    previous = candles[-2]

    price = current["close"]

    # =====================================================
    # MOMENTUM
    # =====================================================

    momentum = (
        price - closes[-4]
    )

    # =====================================================
    # SUPPORT / RESISTANCE
    # =====================================================

    sr_candles = candles[
        -SR_LOOKBACK - 1:-1
    ]

    support = min(
        candle["low"]
        for candle in sr_candles
    )

    resistance = max(
        candle["high"]
        for candle in sr_candles
    )

    # =====================================================
    # SCORE
    # =====================================================

    bull_score = 0
    bear_score = 0

    # EMA TREND
    if ema9 > ema21:

        bull_score += 30

    elif ema9 < ema21:

        bear_score += 30

    # PRICE VS EMA9
    if price > ema9:

        bull_score += 15

    elif price < ema9:

        bear_score += 15

    # RSI
    if 52 <= current_rsi <= 68:

        bull_score += 20

    elif 32 <= current_rsi <= 48:

        bear_score += 20

    elif current_rsi > 70:

        bear_score += 5

    elif current_rsi < 30:

        bull_score += 5

    # MOMENTUM
    if momentum > 0:

        bull_score += 15

    elif momentum < 0:

        bear_score += 15

    # BREAKOUT
    if current["close"] > previous["high"]:

        bull_score += 20

    elif current["close"] < previous["low"]:

        bear_score += 20

    # =====================================================
    # FINAL SCORE
    # =====================================================

    score = max(
        bull_score,
        bear_score
    )

    if bull_score >= bear_score:

        if bull_score >= 75:

            trend = "Strong Bullish"

        else:

            trend = "Bullish"

    else:

        if bear_score >= 75:

            trend = "Strong Bearish"

        else:

            trend = "Bearish"

    # =====================================================
    # SIGNAL
    # =====================================================

    signal = None

    # BUY
    if (
        bull_score >= MIN_TREND_SCORE
        and price > ema9
        and ema9 > ema21
        and 50 < current_rsi < 70
    ):

        signal = "BUY"

    # SELL
    elif (
        bear_score >= MIN_TREND_SCORE
        and price < ema9
        and ema9 < ema21
        and 30 < current_rsi < 50
    ):

        signal = "SELL"

    return {

        "signal": signal,

        "trend": trend,

        "score": score,

        "price": price,

        "ema9": ema9,

        "ema21": ema21,

        "rsi": current_rsi,

        "atr": current_atr,

        "support": support,

        "resistance": resistance,

        "candle": current["datetime"]
    }


# =========================================================
# TP / SL
# =========================================================

def calculate_levels(result):

    price = result["price"]
    atr_value = result["atr"]

    if result["signal"] == "BUY":

        entry = price

        tp1 = price + (
            TP1_ATR * atr_value
        )

        tp2 = price + (
            TP2_ATR * atr_value
        )

        tp3 = price + (
            TP3_ATR * atr_value
        )

        stop_loss = price - (
            SL_ATR * atr_value
        )

    else:

        entry = price

        tp1 = price - (
            TP1_ATR * atr_value
        )

        tp2 = price - (
            TP2_ATR * atr_value
        )

        tp3 = price - (
            TP3_ATR * atr_value
        )

        stop_loss = price + (
            SL_ATR * atr_value
        )

    return (
        entry,
        tp1,
        tp2,
        tp3,
        stop_loss
    )


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }

    response = requests.post(
        url,
        data=payload,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):

        raise RuntimeError(
            data.get(
                "description",
                "Telegram error"
            )
        )


# =========================================================
# SIGNAL MESSAGE
# =========================================================

def format_signal(result):

    (
        entry,
        tp1,
        tp2,
        tp3,
        stop_loss
    ) = calculate_levels(result)

    if result["signal"] == "BUY":

        icon = "🟢"

    else:

        icon = "🔴"

    message = f"""
{icon} GOLD SIGNAL — {result["signal"]}

Symbol: {SYMBOL}
Timeframe: {INTERVAL}

Price: {result["price"]:.2f}

📊 Trend:
{result["trend"]}

Trend Score:
{result["score"]}/100

EMA 9: {result["ema9"]:.2f}
EMA 21: {result["ema21"]:.2f}

RSI: {result["rsi"]:.2f}
ATR: {result["atr"]:.2f}

━━━━━━━━━━━━━━━━

💰 Entry:
{entry:.2f}

🎯 TP1:
{tp1:.2f}

🎯 TP2:
{tp2:.2f}

🎯 TP3:
{tp3:.2f}

🛑 Stop Loss:
{stop_loss:.2f}

━━━━━━━━━━━━━━━━

📉 Support:
{result["support"]:.2f}

📈 Resistance:
{result["resistance"]:.2f}

✅ Signal confirmed by
{CONFIRMATION_CANDLES} closed candles

🕐 Candle:
{result["candle"]}

⚠️ Technical analysis only
Not automatic trading.
"""

    return message


# =========================================================
# WEAKENING ALERT
# =========================================================

def format_weakening(result):

    return f"""
⚠️ TREND WEAKENING

Previous Signal:
{last_signal}

Symbol:
{SYMBOL}

Timeframe:
{INTERVAL}

Price:
{result["price"]:.2f}

Trend:
{result["trend"]}

Trend Score:
{result["score"]}/100

RSI:
{result["rsi"]:.2f}

⚠️ No new BUY/SELL signal.

Monitor the position and risk.

Technical analysis only.
"""


# =========================================================
# PROCESS
# =========================================================

def process():

    global last_signal
    global pending_signal
    global pending_count
    global last_candle_time
    global weakening_sent_for

    candles = get_candles()

    result = analyze(candles)

    if not result:

        print(
            "Not enough data for analysis."
        )

        return

    candle_time = result["candle"]

    # =====================================================
    # از تحلیل دوباره یک کندل جلوگیری می‌کند
    # =====================================================

    if candle_time == last_candle_time:

        return

    last_candle_time = candle_time

    print("=" * 60)

    print(
        f'Price={result["price"]:.2f}'
    )

    print(
        f'EMA9={result["ema9"]:.2f}'
    )

    print(
        f'EMA21={result["ema21"]:.2f}'
    )

    print(
        f'RSI={result["rsi"]:.2f}'
    )

    print(
        f'ATR={result["atr"]:.2f}'
    )

    print(
        f'Signal={result["signal"]}'
    )

    print(
        f'Trend={result["trend"]}'
    )

    print(
        f'Score={result["score"]}'
    )

    # =====================================================
    # BUY / SELL
    # =====================================================

    signal = result["signal"]

    if signal in ("BUY", "SELL"):

        # اگر همان سیگنال قبلی باشد،
        # تعداد تأیید افزایش می‌یابد.
        if signal == pending_signal:

            pending_count += 1

        else:

            pending_signal = signal

            pending_count = 1

        print(
            f"Confirmation: "
            f"{pending_count}/"
            f"{CONFIRMATION_CANDLES}"
        )

        # =================================================
        # سیگنال فقط پس از تأیید صادر می‌شود
        # =================================================

        if (
            pending_count
            >= CONFIRMATION_CANDLES
            and signal != last_signal
        ):

            message = format_signal(
                result
            )

            send_telegram(message)

            print(
                f"Telegram: "
                f"{signal} signal sent."
            )

            last_signal = signal

            weakening_sent_for = None

    # =====================================================
    # NO SIGNAL
    # =====================================================

    else:

        pending_signal = None
        pending_count = 0

        # =================================================
        # HOLD دیگر ارسال نمی‌شود.
        # فقط در صورت ضعیف شدن روند هشدار می‌دهیم.
        # =================================================

        if (
            last_signal
            and result["score"] < 50
            and weakening_sent_for != last_signal
        ):

            send_telegram(
                format_weakening(result)
            )

            weakening_sent_for = last_signal

            print(
                f"Telegram: "
                f"{last_signal} weakening alert sent."
            )


# =========================================================
# MAIN
# =========================================================

def main():

    require_env()

    print("=" * 60)

    print(
        "GOLD SIGNAL BOT"
    )

    print(
        "PROFESSIONAL ANALYSIS MODE"
    )

    print("=" * 60)

    print(
        f"Symbol: {SYMBOL}"
    )

    print(
        f"Timeframe: {INTERVAL}"
    )

    print(
        f"Minimum Trend Score: "
        f"{MIN_TREND_SCORE}/100"
    )

    print(
        f"Confirmation Candles: "
        f"{CONFIRMATION_CANDLES}"
    )

    print(
        "HOLD messages: DISABLED"
    )

    print(
        "Automatic Trading: DISABLED"
    )

    print("=" * 60)

    while True:

        try:

            process()

        except Exception as error:

            print(
                f"ERROR: "
                f"{type(error).__name__}: "
                f"{error}"
            )

        time.sleep(
            CHECK_SECONDS
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()
