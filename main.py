import os
import time
import requests
from datetime import datetime, timezone

# =========================
# Environment Variables
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

# =========================
# Settings
# =========================

SYMBOL = "XAU/USD"

# بررسی قیمت هر 15 دقیقه
CHECK_INTERVAL = 900

# حداقل تغییر قیمت برای ارسال پیام
PRICE_CHANGE_THRESHOLD = 0.50


# =========================
# Get Gold Price
# =========================

def get_gold_price():

    url = (
        f"https://api.twelvedata.com/price"
        f"?symbol={SYMBOL}"
        f"&apikey={API_KEY}"
    )

    try:

        response = requests.get(
            url,
            timeout=15
        )

        data = response.json()

        if "price" in data:

            price = float(data["price"])

            print(f"Current gold price: {price}")

            return price

        print("API Error:", data)

        return None

    except Exception as e:

        print("Price Error:", e)

        return None


# =========================
# Send Telegram Message
# =========================

def send_message(text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:

        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=15
        )

        if response.ok:

            print("Telegram message sent.")

        else:

            print("Telegram Error:", response.text)

    except Exception as e:

        print("Telegram Connection Error:", e)


# =========================
# Main Bot
# =========================

print("=================================")
print("GoldSignalRezaBot Started")
print("Symbol:", SYMBOL)
print("Check Interval: 15 minutes")
print("Price Threshold:", PRICE_CHANGE_THRESHOLD)
print("=================================")


last_sent_price = None


while True:

    try:

        price = get_gold_price()

        if price is None:

            print("No price received.")

        else:

            # اولین قیمت بعد از شروع ربات
            if last_sent_price is None:

                message = f"""🟡 GOLD MARKET

Symbol: {SYMBOL}

Current Price:
{price:.2f}

Status:
Monitoring started.

Powered by GoldSignalRezaBot
"""

                send_message(message)

                last_sent_price = price

            else:

                change = price - last_sent_price

                absolute_change = abs(change)

                print(
                    f"Price change: {change:.2f} "
                    f"(threshold: {PRICE_CHANGE_THRESHOLD:.2f})"
                )

                # =========================
                # Significant Price Change
                # =========================

                if absolute_change >= PRICE_CHANGE_THRESHOLD:

                    if change > 0:

                        direction = "📈 UP"

                    else:

                        direction = "📉 DOWN"


                    current_time = datetime.now(
                        timezone.utc
                    ).strftime(
                        "%Y-%m-%d %H:%M UTC"
                    )


                    message = f"""🟡 GOLD PRICE UPDATE

Symbol: {SYMBOL}

Current Price:
{price:.2f}

Previous Price:
{last_sent_price:.2f}

Change:
{absolute_change:.2f} USD

Direction:
{direction}

Time:
{current_time}

Powered by GoldSignalRezaBot
"""

                    send_message(message)

                    last_sent_price = price

                else:

                    print(
                        "No significant price change. "
                        "Message not sent."
                    )


    except Exception as e:

        print("Main Loop Error:", e)


    # Wait 15 minutes

    print("Waiting 15 minutes...")
    time.sleep(CHECK_INTERVAL)
