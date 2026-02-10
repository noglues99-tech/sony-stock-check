import os
import re
import json
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

TARGET_URL = "https://store.sony.co.kr/product-view/131272260"

# 화면 상태 키워드 (대표님 말 기준으로 고정)
SOLD_OUT_KEYWORD = "일시품절"
BUY_NOW_KEYWORD = "바로 구매하기"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = Path("last_status.json")
BOOT_FILE = Path("boot_notified.json")

BURST_COUNT = 10
BURST_INTERVAL = 1.0  # seconds


def telegram_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 비어있습니다.")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)

    # 실패 원인을 로그에서 바로 보이게
    print("telegram_status:", r.status_code)
    print("telegram_response:", r.text[:300])

    r.raise_for_status()


def compact(s: str) -> str:
    # 공백/줄바꿈/탭 제거 (띄어쓰기 변형 대비)
    return re.sub(r"\s+", "", s or "")


def read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def read_last_status():
    data = read_json(STATE_FILE)
    return (data or {}).get("status")


def write_last_status(status: str) -> None:
    write_json(STATE_FILE, {"status": status, "ts": int(time.time())})


def boot_notify_once(current_status: str) -> None:
    # 처음 정상 실행 1회만 알림
    if BOOT_FILE.exists():
        print("boot_notify: already notified")
        return

    msg = (
        "✅ 소니스토어 재고체커가 정상적으로 실행되었습니다.\n"
        f"- 현재상태: {current_status}\n"
        f"- URL: {TARGET_URL}"
    )
    telegram_send(msg)
    write_json(BOOT_FILE, {"boot_notified": True, "ts": int(time.time())})
    print("boot_notify: sent")


def notify_buy_now_burst() -> None:
    base_text = (
        "🔥 소니스토어 구매 가능 감지!\n"
        "👉 지금 바로 구매하세요\n"
        f"- URL: {TARGET_URL}"
    )

    for i in range(BURST_COUNT):
        telegram_send(f"[{i+1}/{BURST_COUNT}]\n{base_text}")
        time.sleep(BURST_INTERVAL)


def scrape_status(url: str) -> str:
    # ✅ 오탐 방지: 렌더링 후 body 텍스트에서만 2키워드 체크
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(2000)

            body_text = page.inner_text("body", timeout=10_000)
        finally:
            browser.close()

    body_compact = compact(body_text)

    # ✅ 품절 우선 (둘 다 있으면 무조건 SOLD_OUT)
    if compact(SOLD_OUT_KEYWORD) in body_compact:
        return "SOLD_OUT"
    if compact(BUY_NOW_KEYWORD) in body_compact:
        return "BUY_NOW"
    return "UNKNOWN"


def main() -> int:
    # 1) 스크래핑
    try:
        current_status = scrape_status(TARGET_URL)
        print("current_status =", current_status)
    except Exception as e:
        print("scrape_error:", repr(e))
        # 실패 알림(원치 않으면 아래 3줄 주석 처리)
        try:
            telegram_send(f"⚠️ 소니스토어 체크 실패\n- 에러: {repr(e)}\n- URL: {TARGET_URL}")
        except Exception:
            pass
        return 2

    # 2) 부팅 1회 알림
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
