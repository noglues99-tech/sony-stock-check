import os
import requests
import asyncio
from playwright.async_api import async_playwright

# 환경 변수 설정
URL = os.getenv("SONY_URL", "https://store.sony.co.kr/product-view/131272260")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_msg(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 설정이 없습니다.")
        return
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(api_url, json=payload)
    except Exception as e:
        print(f"메시지 전송 실패: {e}")

async def check_stock():
    async with async_playwright() as p:
        # 브라우저 실행
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            print(f"접속 시도: {URL}")
            await page.goto(URL, wait_until="networkidle")
            
            # 구매 버튼 영역 확인 (소니 스토어의 버튼 클래스나 텍스트 기준)
            # '일시품절' 문구가 있는지 확인
            content = await page.content()
            
            if "일시품절" in content:
                status_msg = "❌ 현재 상태: [일시품절] - 아직 구매할 수 없습니다."
            elif "바로 구매하기" in content or "장바구니" in content:
                status_msg = f"✅ <b>[재고 발생!]</b> 지금 바로 구매 가능합니다!\n링크: {URL}"
            else:
                status_msg = "⚠️ 상태를 확인할 수 없습니다. 사이트 구조가 변경되었는지 확인이 필요합니다."

            print(status_msg)
            send_telegram_msg(status_msg)

        except Exception as e:
            error_msg = f"에러 발생: {str(e)}"
            print(error_msg)
            send_telegram_msg(error_msg)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(check_stock())
