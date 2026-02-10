import os, re, json, time
from datetime import datetime, timezone, timedelta

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

KST = timezone(timedelta(hours=9))
URL = "https://store.sony.co.kr/product-view/131272260"

STATE_PATH = ".state/state.json"

# ----- 대표님 요구 조건 -----
UNKNOWN_OR_ERROR_STREAK_ALERT = 3          # 3번 연속 이상일 때만 점검 알림
STALL_ALERT_MINUTES = 45                   # 정상판정(품절/구매가능) 45분 이상 없으면 점검 알림
WATCHDOG_COOLDOWN_MINUTES = 60             # 점검 알림은 60분에 1번만

# 판정불가 즉시 재시도
UNKNOWN_RETRY_COUNT = 4
UNKNOWN_RETRY_DELAY_SEC = 5

# 구매 확정(2회 확인)
CONFIRM_DELAY_SEC = 4

# 구매 확정 시 1분에 1개씩 계속 알림 (한 실행 내에서)
ALERT_EVERY_SEC_WHEN_CONFIRMED = 60
ALERT_MODE_MAX_MINUTES = 90  # 너무 길면 Actions 시간/비용 이슈 → 90분까지만 (원하시면 조절)

BUY_PATTERNS = [r"바로\s*구매", r"구매\s*하기", r"구매"]
SOLDOUT_PATTERNS = [r"일시\s*품절", r"품절"]


def now_kst_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def send_telegram(msg: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(api, data={"chat_id": chat_id, "text": msg}, timeout=20)
    r.raise_for_status()


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "bad_streak": 0,
            "last_ok_epoch": 0,
            "last_watchdog_alert_epoch": 0,
        }


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _match_any(patterns, text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def detect_state(page) -> str:
    """
    return: "IN_STOCK" | "SOLD_OUT" | "UNKNOWN"
    - IN_STOCK: 구매 버튼(또는 링크)이 '보이고' 비활성화가 아님
    - SOLD_OUT: 품절 문구가 보이고 구매 버튼이 명확히 활성으로 보이지 않음
    - UNKNOWN: 로딩/구조변경/혼재
    """
    body = page.inner_text("body")
    soldout_seen = _match_any(SOLDOUT_PATTERNS, body)

    buy_enabled = False
    # 버튼/링크 둘 다 검사
    candidates = page.locator("button:visible, a:visible").all()

    for el in candidates:
        try:
            txt = (el.inner_text() or "").strip()
            if not txt:
                continue
            if _match_any(BUY_PATTERNS, txt):
                disabled_attr = el.get_attribute("disabled")
                aria_disabled = el.get_attribute("aria-disabled")
                is_disabled = (disabled_attr is not None) or (aria_disabled == "true")
                if not is_disabled:
                    buy_enabled = True
                    break
        except Exception:
            continue

    if buy_enabled and not soldout_seen:
        return "IN_STOCK"
    if soldout_seen and not buy_enabled:
        return "SOLD_OUT"
    return "UNKNOWN"


def goto_and_detect(page) -> str:
    page.goto(URL, wait_until="networkidle", timeout=60000)
    return detect_state(page)


def watchdog_if_needed(state, is_ok: bool):
    """
    대표님 요구:
    - 1회 실패로 메시지 보내면 안 됨
    - 3회 연속 이상/장시간 정상판정 없음일 때만
    """
    now_epoch = int(time.time())
    last_ok = int(state.get("last_ok_epoch", 0))
    last_alert = int(state.get("last_watchdog_alert_epoch", 0))
    bad_streak = int(state.get("bad_streak", 0))

    stalled = (last_ok > 0) and ((now_epoch - last_ok) >= STALL_ALERT_MINUTES * 60)

    should_alert = (bad_streak >= UNKNOWN_OR_ERROR_STREAK_ALERT) or stalled
    cooldown_ok = (now_epoch - last_alert) >= WATCHDOG_COOLDOWN_MINUTES * 60

    if should_alert and cooldown_ok:
        reason = []
        if bad_streak >= UNKNOWN_OR_ERROR_STREAK_ALERT:
            reason.append(f"연속 판정불가/오류 {bad_streak}회")
        if stalled:
            mins = (now_epoch - last_ok) // 60
            reason.append(f"정상 판정 없음 {mins}분")

        send_telegram(
            "⚠️ 소니 재고 감시 점검 필요\n"
            f"- 사유: {', '.join(reason)}\n"
            f"- URL: {URL}\n"
            f"- 시각: {now_kst_str()}\n"
            "→ GitHub Actions 로그 확인 권장"
        )
        state["last_watchdog_alert_epoch"] = now_epoch


def alert_mode(page):
    """
    구매가 '확실'해진 이후:
    - 1분에 1개씩 계속 알림
    - 매번 보내기 전에 아직 구매가능인지 확인
    - 품절되면 종료
    """
    end_epoch = time.time() + (ALERT_MODE_MAX_MINUTES * 60)

    while time.time() < end_epoch:
        try:
            st = goto_and_detect(page)
        except Exception:
            # alert 모드에서도 페이지가 깨질 수 있으니, 여기서는 1분 텀 유지하며 계속
            st = "UNKNOWN"

        if st != "IN_STOCK":
            # 구매 가능이 아니면 멈춤(대표님 요구: 확실할 때만 계속 울리기)
            return

        send_telegram(
            "🔥 구매 가능(유지 중)\n"
            f"- 시각: {now_kst_str()}\n"
            f"- 링크: {URL}\n"
            "→ 지금 바로 확인하세요"
        )
        time.sleep(ALERT_EVERY_SEC_WHEN_CONFIRMED)


def main():
    state = load_state()
    now_epoch = int(time.time())

    # 🧪 텔레그램 연결 테스트 (임시)
    send_telegram("🧪 테스트: GitHub Actions에서 텔레그램 연결 확인")

    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            result = None

            # 1) 판정불가 줄이기: UNKNOWN이면 즉시 재시도
            last_err = None
            for _ in range(UNKNOWN_RETRY_COUNT):
                try:
                    result = goto_and_detect(page)
                    if result != "UNKNOWN":
                        last_err = None
                        break
                except (PWTimeoutError, Exception) as e:
                    last_err = e
                time.sleep(UNKNOWN_RETRY_DELAY_SEC)

            # 2) 결과 처리
            if result == "IN_STOCK":
                # 구매 확정(2회)
                time.sleep(CONFIRM_DELAY_SEC)
                result2 = goto_and_detect(page)

                if result2 == "IN_STOCK":
                    # 정상 판정(구매가능) → ok 갱신/배드스택 리셋
                    state["last_ok_epoch"] = now_epoch
                    state["bad_streak"] = 0
                    save_state(state)

                    # 확정 알림 + 1분 연속 알림 모드
                    send_telegram(
                        "✅ 구매 가능 확정(2회 확인)\n"
                        f"- 시각: {now_kst_str()}\n"
                        f"- 링크: {URL}\n"
                        "→ 이후 1분마다 알림을 계속 보냅니다(구매 가능 유지 시)"
                    )
                    alert_mode(page)
                else:
                    # 1차만 뜬 경우는 오탐 가능 → '점검 알림' 대상 아님, 조용히 종료
                    state["last_ok_epoch"] = now_epoch
                    state["bad_streak"] = 0
                    save_state(state)

            elif result == "SOLD_OUT":
                # 정상 판정(품절) → ok 갱신/배드스택 리셋
                state["last_ok_epoch"] = now_epoch
                state["bad_streak"] = 0
                save_state(state)

            else:
                # UNKNOWN 또는 계속 오류
                state["bad_streak"] = int(state.get("bad_streak", 0)) + 1
                save_state(state)

                # 대표님 조건에 맞는 경우에만 점검 알림
                watchdog_if_needed(state, is_ok=False)

        finally:
            browser.close()


if __name__ == "__main__":
    main()
