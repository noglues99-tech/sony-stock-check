import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = os.getenv("SONY_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = "state.json"
SEND_COUNT_ON_AVAILABLE = 10

AVAILABLE_KEYWORDS = ["구매하기", "바로구매", "장바구니"]
SOLDOUT_KEYWORDS = ["일시품절", "품절"]


def read_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"last_status": "UNKNOWN", "last_checked_at": ""}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_status": "UNKNOWN", "last_checked_at": ""}


def write_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM env is missing. Skip sending.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}

    last_err = None
    for _ in range(3):
        try:
            r = requests.post(url, data=payload, timeout=15)
            if r.status_code == 200:
                return
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(2)

    print(f"Telegram send failed: {last_err}", file=sys.stderr)


def detect_status_from_text(page_text: str) -> str:
    for kw in AVAILABLE_KEYWORDS:
        if kw in page_text:
            return "AVAILABLE"
    for kw in SOLDOUT_KEYWORDS:
        if kw in page_text:
            return "SOLD_OUT"
    return "UNKNOWN"


def fetch_page_text_with_playwright() -> str:
    if not URL:
        raise ValueError("SONY_URL is empty")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1200)

            return page.inner_text("body")
        finally:
            context.close()
            browser.close()


def main():
    state = read_state()
    last_status = state.get("last_status", "UNKNOWN")

    try:
        page_text = fetch_page_text_with_playwright()
        current_status = detect_status_from_text(page_text)

        state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        state["last_status"] = current_status
        write_state(state)

        print(f"Last={last_status} / Now={current_status}")

        if current_status == "AVAILABLE" and last_status != "AVAILABLE":
            base = f"[소니스토어] 구매 가능 감지 ✅\nURL: {URL}\n(10회 발송)"
            for i in range(SEND_COUNT_ON_AVAILABLE):
                send_telegram(f"{base}\n({i+1}/{SEND_COUNT_ON_AVAILABLE})")
                time.sleep(0.4)

        elif current_status != "AVAILABLE" and last_status == "AVAILABLE":
            send_telegram(f"[소니스토어] 구매 불가로 전환됨 ⛔\n현재: {current_status}\nURL: {URL}")

        sys.exit(0)

    except Exception as e:
        msg = f"[소니스토어] 체크 실패 ⚠️\n에러: {type(e).__name__}: {e}\nURL: {URL}"
        print(msg, file=sys.stderr)
        send_telegram(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
