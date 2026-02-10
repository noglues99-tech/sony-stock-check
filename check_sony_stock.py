import os
import re
import json
import sys
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright

TARGET_URL = "https://store.sony.co.kr/product-view/123967519"

# 키워드(이 2개로만 판정)
SOLD_OUT_KEYWORD = "일시품절"
BUY_NOW_KEYWORD = "바로 구매하기"

# 1이면 매번 메시지(테스트), 0이면 상태 변경시에만 메시지
TEST_MODE = os.getenv("TEST_MODE", "0").strip() == "1"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = Path("last_status.json")


def telegram_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 비어 있습니다.")
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


def scrape_button_texts(url: str) -> tuple[str, list[str]]:
    """
    JS로 렌더링되는 페이지에서 버튼/링크 텍스트를 넓게 긁어서
    '일시품절' 또는 '바로 구매하기' 포함 여부만으로 판정.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # 렌더링 시간 여유
        page.wait_for_timeout(3000)

        texts: list[str] = []

        # 텍스트가 있을 법한 요소를 넓게 수집
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
                    # input은 value에 있는 경우가 많음
                    try:
                        v = normalize(loc.nth(i).get_attribute("value"))
                        if v:
                            texts.append(v)
                    except Exception:
                        pass

        browser.close()

    status = detect_status(texts)
    return status, texts


def status_ko(status: str) -> str:
    if status == "BUY_NOW":
        return "구매 가능(바로 구매하기)"
    if status == "SOLD_OUT":
        return "품절(일시품절)"
    return "판단 불가(키워드 미검출)"


def read_last_status() -> str | None:
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data.get("status")
    except Exception:
        return None


def write_last_status(status: str) -> None:
    STATE_FILE.write_text(json.dumps({"status": status}, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    try:
        current_status, _texts = scrape_button_texts(TARGET_URL)
    except Exception as e:
        telegram_send(f"[소니스토어 모니터링] 오류 발생\n- URL: {TARGET_URL}\n- 내용: {e}")
        return 2

    last_status = read_last_status()

    # 메시지 구성(키워드 기반 판정 결과만 전달)
    if current_status == "BUY_NOW":
        msg = (
            "✅ 지금 구매하세요!\n"
            f"- 상태: {status_ko(current_status)}\n"
            f"- URL: {TARGET_URL}"
        )
    elif current_status == "SOLD_OUT":
        msg = (
            "ℹ️ 현재 품절중입니다.\n"
            f"- 상태: {status_ko(current_status)}\n"
            f"- URL: {TARGET_URL}"
        )
    else:
        msg = (
            "⚠️ 키워드(일시품절/바로 구매하기)를 찾지 못했습니다.\n"
            f"- 상태: {status_ko(current_status)}\n"
            f"- URL: {TARGET_URL}\n"
            "페이지 구조 변경/로딩 문제일 수 있습니다."
        )

    # 전송 조건
    should_send = TEST_MODE or (last_status != current_status)

    # 상태 저장(다음 실행 비교용)
    write_last_status(current_status)

    if should_send:
        telegram_send(msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
