import os
import time
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")

SYMBOL = "XAU/USD"

def get_gold_price():
    url = f"https://api.twelvedata.com/price?symbol={SYMBOL}&apikey={API_KEY}"

    try:
        r = requests.get(url, timeout=15)
        data = r.json()

        if "price" in data:
            return float(data["price"])

        print("API Error:", data)
        return None

    except Exception as e:
        print("Request Error:", e)
        return None


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=15
        )
        print("Telegram message sent.")
    except Exception as e:
        print("Telegram Error:", e)


print("Gold Bot Started...")

while True:

    price = get_gold_price()

    if price is not None:

        message = f"""🟡 GOLD PRICE

Symbol: {SYMBOL}

Current Price:
{price}

Powered by GoldSignalBot
"""

        send_message(message)

    else:
        print("No price received.")

    time.sleep(3600)
