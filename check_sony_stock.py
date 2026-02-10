import os
import re
import sys
import json
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

TARGET_URL = "https://store.sony.co.kr/product-view/131272260"
CONTROL_URL = "https://store.sony.co.kr/product-view/123967519"

# 버튼 텍스트 판별 키워드
SOLD_OUT_KEYWORDS = ["일시품절"]
BUY_NOW_KEYWORDS = ["바로 구매하기"]

# 테스트 모드: 매 실행마다 메시지 보냄
ALWAYS_NOTIFY = True

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def telegram_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 비어 있습니다.")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    r.raise_for_status()

def normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s

def detect_status_from_texts(texts: list[str]) -> str:
    joined = " | ".join(texts)
    if any(k in joined for k in BUY_NOW_KEYWORDS):
        return "BUY_NOW"
    if any(k in joined for k in SOLD_OUT_KEYWORDS):
        return "SOLD_OUT"
    return "UNKNOWN"

def fetch_page_button_texts(url: str) -> tuple[str, list[str]]:
    """
    페이지 내에서 버튼/링크/입력 요소의 텍스트를 넓게 수집해서
    '일시품절' 또는 '바로 구매하기' 존재 여부로 상태를 판정합니다.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # SPA라서 렌더링 시간 조금 부여
        try:
            page.wait_for_timeout(3_000)
        except PWTimeoutError:
            pass

        texts: list[str] = []

        # 버튼/링크/인풋을 최대한 넓게 긁기
        locators = [
            page.locator("button"),
            page.locator("a[role='button']"),
            page.locator("a"),
            page.locator("input[type='button']"),
            page.locator("input[type='submit']"),
        ]

        for loc in locators:
            try:
                count = loc.count()
            except Exception:
                continue
            for i in range(min(count, 300)):  # 과도한 수집 방지
                try:
                    t = loc.nth(i).inner_text(timeout=2000)
                    t = normalize(t)
                    if t:
                        texts.append(t)
                except Exception:
                    # input은 value에 텍스트가 있을 수 있음
                    try:
                        v = loc.nth(i).get_attribute("value")
                        v = normalize(v or "")
                        if v:
                            texts.append(v)
                    except Exception:
                        continue

        browser.close()

    status = detect_status_from_texts(texts)
    return status, texts

def status_to_korean(status: str) -> str:
    if status == "BUY_NOW":
        return "구매 가능(바로 구매하기)"
    if status == "SOLD_OUT":
        return "품절(일시품절)"
    return "판단 불가(UNKNOWN)"

def main() -> int:
    try:
        target_status, target_texts = fetch_page_button_texts(TARGET_URL)
        control_status, _ = fetch_page_button_texts(CONTROL_URL)
    except Exception as e:
        telegram_send(f"[소니스토어 모니터링] 오류 발생\n{e}")
        return 2

    # 테스트 메시지(현재 상태를 매번 알림)
    msg_lines = [
        "[소니스토어 모니터링(테스트)]",
        f"- 대상: {status_to_korean(target_status)}",
        f"- 비교(참고): {status_to_korean(control_status)}",
        f"- 대상 URL: {TARGET_URL}",
    ]

    # 대상이 구매 가능이면 강한 메시지
    if target_status == "BUY_NOW":
        msg_lines.insert(1, "✅ 지금 구매하세요! (버튼이 '바로 구매하기'로 감지됨)")
    elif target_status == "SOLD_OUT":
        msg_lines.insert(1, "ℹ️ 현재 품절중입니다. (테스트 메시지)")
    else:
        msg_lines.insert(1, "⚠️ 버튼 문구를 확실히 못 찾았습니다. 페이지 구조가 바뀌었을 수 있어요.")

    message = "\n".join(msg_lines)

    # 테스트 모드: 항상 전송
    if ALWAYS_NOTIFY:
        telegram_send(message)
        return 0

    # (옵션) 변경시에만 전송하고 싶다면 아래처럼 state.json을 쓰는 방식으로 확장 가능
    return 0

if __name__ == "__main__":
    sys.exit(main())
