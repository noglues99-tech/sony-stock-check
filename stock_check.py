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

# 대표님 요구: "구매 가능일 때만" 10번 전송, 품절로 돌아가면 리셋
SEND_COUNT_ON_AVAILABLE = 10

# 페이지 상태 판정 키워드(필요하면 여기만 바꾸면 됨)
AVAILABLE_KEYWORDS = ["구매하기", "바로구매", "장바구니"]  # 사이트 문구가 바뀔 가능성 대비
SOLDOUT_KEYWORDS = ["일시품절", "품절"]


def now_iso_kst() -> str:
    # GitHub Actions는 UTC라서, 로그 읽기 편하게 KST로 변환해서 저장
    # KST = UTC+9
    kst = timezone.utc
    dt = datetime.now(timezone.utc).astimezone(timezone.utc)
    # 저장은 ISO로 하되, 실제 표기는 메시지에서 KST로 표기
    return dt.isoformat()


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

    # 네트워크 일시 오류 대비 재시도
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
    # “그 순간 새로고침했을 때 키워드 존재 여부”만으로 단순 판정
    # 구매가능 > 품절 > 알수없음 순서로 판단
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
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
        )
        page = context.new_page()

        # “진짜 새로고침” 느낌: 캐시 영향 최소화
        page.route("**/*", lambda route: route.continue_())

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            # JS 렌더링 기다리기: 네트워크 idle + 약간의 안정화 대기
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1200)

            text = page.inner_text("body")
            return text
        finally:
            context.close()
            browser.close()


def main():
    state = read_state()
    last_status = state.get("last_status", "UNKNOWN")

    try:
        page_text = fetch_page_text_with_playwright()
        current_status = detect_status_from_text(page_text)

        checked_at = datetime.now(timezone.utc).isoformat()
        state["last_checked_at"] = checked_at
        state["last_status"] = current_status
        write_state(state)

        print(f"Last={last_status} / Now={current_status}")

        # 트리거 로직
        if current_status == "AVAILABLE" and last_status != "AVAILABLE":
            msg = f"[소니스토어] 구매 가능 감지 ✅\nURL: {URL}\n(10회 발송)"
            for i in range(SEND_COUNT_ON_AVAILABLE):
                send_telegram(f"{msg}\n({i+1}/{SEND_COUNT_ON_AVAILABLE})")
                time.sleep(0.4)

        elif current_status != "AVAILABLE" and last_status == "AVAILABLE":
            # 구매가능 -> 품절/unknown 으로 돌아가면 “다음번 구매가능” 때 다시 10회 보내기 위한 리셋
            send_telegram(f"[소니스토어] 구매 불가로 전환됨 ⛔\n현재: {current_status}\nURL: {URL}")

        # UNKNOWN은 원칙적으로 조용히 넘어감 (원하시면 여기서 UNKNOWN 알림도 가능)
        sys.exit(0)

    except Exception as e:
        # 오류는 알려야 함 (대표님이 “주기 실행이 안되는지” 판단할 수 있도록)
        err = f"[소니스토어] 체크 실패 ⚠️\n에러: {type(e).__name__}: {e}\nURL: {URL}"
        print(err, file=sys.stderr)
        send_telegram(err)
        sys.exit(1)


if __name__ == "__main__":
    main()
