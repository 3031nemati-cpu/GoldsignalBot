import os
import time
import logging
from datetime import datetime, timezone

import requests

# ============================================================
# GOLD SIGNAL BOT - PROFESSIONAL ANALYSIS
# ============================================================
# Telegram output intentionally contains NO UTC/Tehran/analysis
# timestamps. Time is used internally only for candle validation.
# HOLD messages are OFF. Automatic trading is OFF.
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"
INTERVAL = "5min"

CHECK_SECONDS = 60
MIN_TREND_SCORE = 60
MAX_CANDLE_AGE_MINUTES = 8
CONFIRMATION_REQUIRED = 2

TP1_ATR = 1.0
TP2_ATR = 2.0
TP3_ATR = 3.0
SL_ATR = 1.5

AUTOMATIC_TRADING = False
SEND_HOLD_MESSAGES = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(levelname)s | %(message)s"
)
log = logging.getLogger("GoldSignalBot")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not CHAT_ID:
    raise RuntimeError("CHAT_ID is missing")
if not API_KEY:
    raise RuntimeError("API_KEY is missing")

session = requests.Session()
session.headers.update({"User-Agent": "GoldSignalBot/Professional"})

last_processed_candle = None
last_sent_signal = None


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = session.post(
            url,
            data={"chat_id": CHAT_ID, "text": message},
            timeout=20
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            log.error("Telegram error: %s", data)
            return False
        log.info("Telegram message sent.")
        return True
    except Exception as e:
        log.error("Telegram error: %s", e)
        return False


def parse_utc(value):
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_candles(outputsize=120):
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
        r = session.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        if data.get("status") == "error":
            log.error("Twelve Data: %s", data)
            return []

        result = []
        for x in data.get("values", []):
            try:
                result.append({
                    "datetime": parse_utc(x["datetime"]),
                    "open": float(x["open"]),
                    "high": float(x["high"]),
                    "low": float(x["low"]),
                    "close": float(x["close"]),
                })
            except (KeyError, ValueError, TypeError):
                continue

        result.sort(key=lambda x: x["datetime"])
        return result

    except Exception as e:
        log.error("Market data error: %s", e)
        return []


def get_closed_candles(candles):
    now = datetime.now(timezone.utc)
    closed = []

    for c in candles:
        # 5-minute candle: datetime is candle OPEN time.
        close_time = c["datetime"].timestamp() + 300
        if now.timestamp() >= close_time:
            closed.append(c)

    return closed


def candle_age_minutes(candle):
    now = datetime.now(timezone.utc)
    close_time = candle["datetime"].timestamp() + 300
    return max(0.0, (now.timestamp() - close_time) / 60.0)


def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    value = sum(values[:period]) / period
    for price in values[period:]:
        value = (price - value) * k + value
    return value


def ema_series(values, period):
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    value = sum(values[:period]) / period
    out = [None] * (period - 1) + [value]
    for price in values[period:]:
        value = (price - value) * k + value
        out.append(value)
    return out


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = ((value * (period - 1)) + tr) / period
    return value


def candle_direction(c):
    if c["close"] > c["open"]:
        return "BULLISH"
    if c["close"] < c["open"]:
        return "BEARISH"
    return "NEUTRAL"


def calculate_analysis(candles):
    closes = [c["close"] for c in candles]

    e9 = ema_series(closes, 9)
    e21 = ema_series(closes, 21)
    if len(e9) < 2 or len(e21) < 2:
        return None

    ema9 = e9[-1]
    ema21 = e21[-1]
    prev_ema9 = e9[-2]
    prev_ema21 = e21[-2]

    price = closes[-1]
    r = rsi(closes)
    a = atr(candles)

    if r is None or a is None:
        return None

    bull = 0
    bear = 0

    if ema9 > ema21:
        bull += 25
    elif ema9 < ema21:
        bear += 25

    if price > ema9:
        bull += 15
    elif price < ema9:
        bear += 15

    if ema9 > prev_ema9:
        bull += 10
    elif ema9 < prev_ema9:
        bear += 10

    if ema21 > prev_ema21:
        bull += 10
    elif ema21 < prev_ema21:
        bear += 10

    if 52 <= r <= 68:
        bull += 20
    elif 32 <= r <= 48:
        bear += 20

    # Do not chase extreme RSI conditions.
    if r > 72:
        bull -= 10
    if r < 28:
        bear -= 10

    # Two closed candle directions.
    if candles[-1]["close"] > candles[-1]["open"]:
        bull += 10
    elif candles[-1]["close"] < candles[-1]["open"]:
        bear += 10

    if candles[-2]["close"] > candles[-2]["open"]:
        bull += 10
    elif candles[-2]["close"] < candles[-2]["open"]:
        bear += 10

    bull = max(0, min(100, bull))
    bear = max(0, min(100, bear))

    if bull >= bear:
        score = bull
        trend = "Strong Bullish" if score >= 80 else (
            "Bullish" if score >= 60 else "Neutral"
        )
    else:
        score = bear
        trend = "Strong Bearish" if score >= 80 else (
            "Bearish" if score >= 60 else "Neutral"
        )

    return {
        "price": price,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": r,
        "atr": a,
        "bull": bull,
        "bear": bear,
        "trend": trend,
        "score": int(score),
    }


def confirm_signal(candles, analysis):
    c1, c2 = candles[-2], candles[-1]

    bull_count = sum([
        c1["close"] > c1["open"],
        c2["close"] > c2["open"]
    ])
    bear_count = sum([
        c1["close"] < c1["open"],
        c2["close"] < c2["open"]
    ])

    if (
        analysis["bull"] > analysis["bear"]
        and bull_count >= CONFIRMATION_REQUIRED
    ):
        return "BUY", bull_count

    if (
        analysis["bear"] > analysis["bull"]
        and bear_count >= CONFIRMATION_REQUIRED
    ):
        return "SELL", bear_count

    return "HOLD", max(bull_count, bear_count)


def levels(signal, price, a, support, resistance):
    if signal == "BUY":
        return {
            "entry": price,
            "tp1": price + a * TP1_ATR,
            "tp2": price + a * TP2_ATR,
            "tp3": price + a * TP3_ATR,
            "sl": price - a * SL_ATR,
            "support": support,
            "resistance": resistance,
        }

    if signal == "SELL":
        return {
            "entry": price,
            "tp1": price - a * TP1_ATR,
            "tp2": price - a * TP2_ATR,
            "tp3": price - a * TP3_ATR,
            "sl": price + a * SL_ATR,
            "support": support,
            "resistance": resistance,
        }

    return None


def build_message(signal, x, lv, confirmation_count):
    title = "🟢 GOLD SIGNAL — BUY" if signal == "BUY" else "🔴 GOLD SIGNAL — SELL"
    quality = "HIGH" if x["score"] >= 75 else "MEDIUM"

    return f"""{title}

Symbol: {SYMBOL}
Timeframe: {INTERVAL}

💰 Price: {x["price"]:.2f}

📊 Trend: {x["trend"]}
Trend Score: {x["score"]}/100

EMA 9: {x["ema9"]:.2f}
EMA 21: {x["ema21"]:.2f}
RSI: {x["rsi"]:.2f}
ATR: {x["atr"]:.2f}

━━━━━━━━━━━━━━━━

💰 Entry: {lv["entry"]:.2f}

🎯 TP1: {lv["tp1"]:.2f}
🎯 TP2: {lv["tp2"]:.2f}
🎯 TP3: {lv["tp3"]:.2f}

🛑 Stop Loss: {lv["sl"]:.2f}

━━━━━━━━━━━━━━━━

📉 Support: {lv["support"]:.2f}
📈 Resistance: {lv["resistance"]:.2f}

✅ Confirmation: {confirmation_count}/2
⭐ Signal Quality: {quality}

⚠️ Technical analysis only
Not automatic trading."""


def analyze():
    global last_processed_candle, last_sent_signal

    candles = get_candles()
    if len(candles) < 30:
        log.warning("Not enough candles.")
        return

    closed = get_closed_candles(candles)
    if len(closed) < 30:
        log.warning("Not enough CLOSED candles.")
        return

    latest = closed[-1]
    candle_id = latest["datetime"].isoformat()

    if candle_id == last_processed_candle:
        log.info("Same closed candle already processed.")
        return

    age = candle_age_minutes(latest)
    log.info("Closed candle age: %.2f minutes", age)

    if age > MAX_CANDLE_AGE_MINUTES:
        log.warning("Closed candle is too old.")
        return

    last_processed_candle = candle_id

    x = calculate_analysis(closed)
    if not x:
        log.warning("Indicator calculation failed.")
        return

    recent = closed[-20:]
    support = min(c["low"] for c in recent)
    resistance = max(c["high"] for c in recent)

    signal, confirmation_count = confirm_signal(closed, x)

    log.info(
        "Price=%.2f | EMA9=%.2f | EMA21=%.2f | RSI=%.2f | ATR=%.2f",
        x["price"], x["ema9"], x["ema21"], x["rsi"], x["atr"]
    )
    log.info(
        "Signal=%s | Trend=%s | Score=%d | Confirmation=%d/2",
        signal, x["trend"], x["score"], confirmation_count
    )

    if signal == "HOLD":
        log.info("HOLD suppressed. No Telegram HOLD message.")
        return

    if x["score"] < MIN_TREND_SCORE:
        log.info("Signal rejected: score below minimum.")
        return

    if signal == "BUY" and x["bull"] <= x["bear"]:
        return

    if signal == "SELL" and x["bear"] <= x["bull"]:
        return

    # Prevent repeated BUY/BUY or SELL/SELL Telegram messages.
    if signal == last_sent_signal:
        log.info("Same signal direction already sent: %s", signal)
        return

    lv = levels(signal, x["price"], x["atr"], support, resistance)
    if not lv:
        return

    message = build_message(signal, x, lv, confirmation_count)

    if send_telegram(message):
        last_sent_signal = signal


def startup():
    log.info("================================================")
    log.info("GOLD SIGNAL BOT")
    log.info("Professional Analysis Mode: ON")
    log.info("Symbol: %s", SYMBOL)
    log.info("Timeframe: %s", INTERVAL)
    log.info("Minimum Trend Score: %d/100", MIN_TREND_SCORE)
    log.info("Maximum Candle Age: %d minutes", MAX_CANDLE_AGE_MINUTES)
    log.info("Closed Candle Protection: ON")
    log.info("Duplicate Candle Protection: ON")
    log.info("HOLD Messages: OFF")
    log.info("Automatic Trading: DISABLED")
    log.info("Telegram timestamps: OFF")
    log.info("================================================")


def main():
    startup()

    while True:
        try:
            analyze()
        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break
        except Exception as e:
            log.exception("Unexpected error: %s", e)

        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    main()
