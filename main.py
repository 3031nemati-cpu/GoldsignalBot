import os
import time
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOL = "XAU/USD"

def get_gold_price():
    url = "https://api.twelvedata.com/price?symbol=XAU/USD&apikey=demo"

    try:
        r = requests.get(url)
        data = r.json()

        if "price" in data:
            return float(data["price"])

        return None

    except:
        return None


def send_message(text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text
        }
    )


print("Gold Bot Started...")

while True:

    price = get_gold_price()

    if price:

        message = f"""
🟡 GOLD PRICE

Symbol : {SYMBOL}

Current Price :
{price}

Powered by GoldSignalBot
"""

        send_message(message)

    time.sleep(3600)
