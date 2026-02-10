import os
import re
import json
import sys
import time
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright

TARGET_URL = "https://store.sony.co.kr/product-view/131272260"

# 키워드 (이 두 개로만 판정)
SOLD_OUT_KEYWORD = "일시품절"
BUY_NOW_KEYWORD = "바로 구매하기"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = Path("last_status.json")

# 알림 설정
BURST_COUNT = 10        # 총 메시지 수
BURST_INTERVAL = 1.0   # 초 단위 (1초마다 1개)


def telegram_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("텔레그램 환경변수가 설정되지 않았습니다.")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    r.raise_for_status()


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def detect_status(texts: list[str]) -> str:
    joined = " | ".join(texts)
    if BUY_NOW_KEYWORD in joined:
        return "BUY_NOW"
    if SOLD_OUT_KEYWORD in joined:
        return "SOLD_OUT"
    return "UNKNOWN"


def scrape_status(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3000)

        texts: list[str] = []
        selectors = [
            "button",
            "a[role='button']",
            "a",
            "input[type='button']",
            "input[type='submit']",
        ]

        for sel in selectors:
            loc = page.locator(sel)
            try:
                count = loc.count()
            except Exception:
                continue

            for i in range(min(count, 300)):
                try:
                    t = normalize(loc.nth(i).inner_text(timeout=1500))
                    if t:
                        texts.append(t)
                except Exception:
                    try:
                        v = normalize(loc.nth(i).get_attribute("value"))
                        if v:
                            texts.append(v)
                    except Exception:
                        pass

        browser.close()

    return detect_status(texts)


def read_last_status() -> str | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get("status")
    except Exception:
        return None


def write_last_status(status: str) -> None:
    STATE_FILE.write_text(
        json.dumps({"status": status}, ensure_ascii=False),
        encoding="utf-8"
    )


def notify_buy_now_burst() -> None:
    base_text = (
        "🔥 소니스토어 구매 가능 감지!\n"
        "👉 지금 바로 구매하세요\n"
        f"- URL: {TARGET_URL}"
    )

    for i in range(BURST_COUNT):
        telegram_send(f"[{i+1}/{BURST_COUNT}]\n{base_text}")
        time.sleep(BURST_INTERVAL)


def main() -> int:
    try:
        current_status = scrape_status(TARGET_URL)
    except Exception:
        # 필요하면 오류 알림 추가 가능
        return 2

    last_status = read_last_status()

    # BUY_NOW로 '전환'되는 순간만 10초 분산 알림
    if current_status == "BUY_NOW" and last_status != "BUY_NOW":
        notify_buy_now_burst()

    # 상태 저장
    write_last_status(current_status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
