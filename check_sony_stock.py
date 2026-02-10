import os
import re
import json
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

TARGET_URL = "https://store.sony.co.kr/product-view/131272260"

# 키워드 (이 두 개로만 판정)
SOLD_OUT_KEYWORD = "일시품절"
BUY_NOW_KEYWORD = "바로 구매하기"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# 상태/부팅 플래그 파일 (GitHub Actions에서 캐시로 유지)
STATE_FILE = Path("last_status.json")
BOOT_FILE = Path("boot_notified.json")

# 알림 설정
BURST_COUNT = 10
BURST_INTERVAL = 1.0  # seconds


def telegram_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 비어있습니다.")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)

    # ✅ 실패 원인을 Actions 로그에서 바로 보이게
    print("telegram_status:", r.status_code)
    print("telegram_response:", r.text[:300])

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


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def read_last_status() -> str | None:
    data = read_json(STATE_FILE)
    return (data or {}).get("status")


def write_last_status(status: str) -> None:
    write_json(STATE_FILE, {"status": status})


def boot_notify_once(current_status: str) -> None:
    """
    ✅ '처음 정상 실행' 딱 1회만 텔레그램 전송
    (BOOT_FILE이 있으면 전송 안 함)
    """
    if BOOT_FILE.exists():
        print("boot_notify: already notified (BOOT_FILE exists)")
        return

    msg = (
        "✅ 소니스토어 재고체커가 정상적으로 실행되었습니다.\n"
        f"- 현재상태: {current_status}\n"
        f"- URL: {TARGET_URL}"
    )
    telegram_send(msg)
    write_json(BOOT_FILE, {"boot_notified": True, "ts": int(time.time())})
    print("boot_notify: sent and BOOT_FILE written")


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
    # 1) 스크래핑
    try:
        current_status = scrape_status(TARGET_URL)
        print("current_status =", current_status)
    except Exception as e:
        print("scrape_error:", repr(e))
        return 2

    # 2) 부팅 1회 알림 (실패해도 이유는 로그로 남김)
    try:
        boot_notify_once(current_status)
    except Exception as e:
        print("boot_notify_error:", repr(e))

    # 3) 전환 감지
    last_status = read_last_status()
    print("last_status =", last_status)

    if current_status == "BUY_NOW" and last_status != "BUY_NOW":
        try:
            notify_buy_now_burst()
        except Exception as e:
            print("buy_now_notify_error:", repr(e))

    # 4) 상태 저장
    try:
        write_last_status(current_status)
    except Exception as e:
        print("state_write_error:", repr(e))
        return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
