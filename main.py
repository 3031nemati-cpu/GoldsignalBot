import os
import time
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"

CHECK_INTERVAL = 300       # 5 minutes
PRICE_CHANGE_THRESHOLD = 0.50


def get_gold_price():
    url = f"https://api.twelvedata.com/price?symbol={SYMBOL}&apikey={API_KEY}"

    try:
        response = requests.get(url, timeout=15)
        data = response.json()

        if "price" in data:
            return float(data["price"])

        print("API Error:", data)
        return None

    except Exception as e:
        print("Price Error:", e)
        return None


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


print("Gold Bot Started...")
print("Checking gold price every 5 minutes.")
print(f"Price change threshold: {PRICE_CHANGE_THRESHOLD}")

last_sent_price = None


while True:

    price = get_gold_price()

    if price is None:
        print("No price received.")

    else:
        print(f"Current gold price: {price}")

        if last_sent_price is None:

            message = f"""🟡 GOLD PRICE

Symbol: {SYMBOL}

Current Price:
{price:.2f}

Powered by GoldSignalRezaBot
"""

            send_message(message)
            last_sent_price = price

        else:

            price_change = abs(price - last_sent_price)

            print(
                f"Price change: {price_change:.2f} "
                f"(threshold: {PRICE_CHANGE_THRESHOLD:.2f})"
            )

            if price_change >= PRICE_CHANGE_THRESHOLD:

                direction = "📈 UP" if price > last_sent_price else "📉 DOWN"

                message = f"""🟡 GOLD PRICE UPDATE

Symbol: {SYMBOL}

Current Price:
{price:.2f}

Previous Sent Price:
{last_sent_price:.2f}

Change:
{price_change:.2f} USD {direction}

Powered by GoldSignalRezaBot
"""

                send_message(message)

                last_sent_price = price

            else:
                print("No significant price change. Message not sent.")

    time.sleep(CHECK_INTERVAL)
