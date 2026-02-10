import os
import asyncio
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright
import requests

KST = timezone(timedelta(hours=9))

PRODUCT_URL = "https://store.sony.co.kr/product-view/131272260"
KEYWORD_IN_STOCK = "바로 구매하기"
KEYWORD_SOLD_OUT = "일시품절"


def send_telegram(message: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        url,
        data={"chat_id": chat_id, "text": message},
        timeout=20,
    )
    r.raise_for_status()


async def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(PRODUCT_URL, wait_until="networkidle", timeout=60000)

        text = await page.inner_text("body")
        await browser.close()

    if (KEYWORD_IN_STOCK in text) and (KEYWORD_SOLD_OUT not in text):
        send_telegram(
            f"📦 소니 재고 감지\n{now}\n{PRODUCT_URL}\n→ '바로 구매하기' 확인"
        )


if __name__ == "__main__":
    asyncio.run(main())
