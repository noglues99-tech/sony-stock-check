def status_to_korean(result: str) -> str:
    if result == "IN_STOCK":
        return "🔥 구매 가능"
    if result == "SOLD_OUT":
        return "❌ 일시품절/품절"
    return "⚠️ 판정불가(UNKNOWN)"


def main():
    state = load_state()
    now_epoch = int(time.time())

    # ❌ 테스트 메시지는 이제 제거/주석 권장 (계속 오면 헷갈림)
    # send_telegram("🧪 테스트: GitHub Actions에서 텔레그램 연결 확인")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            result = None

            # 1) UNKNOWN이면 즉시 재시도
            for _ in range(UNKNOWN_RETRY_COUNT):
                try:
                    result = goto_and_detect(page)
                    if result != "UNKNOWN":
                        break
                except (PWTimeoutError, Exception):
                    result = "UNKNOWN"
                time.sleep(UNKNOWN_RETRY_DELAY_SEC)

            # ✅ (추가) 매 실행마다 현재 상태를 무조건 텔레그램으로 보냄
            send_telegram(
                "📡 소니 재고 감시 상태 보고(5분 주기)\n"
                f"- 상태: {status_to_korean(result)}\n"
                f"- 시각: {now_kst_str()}\n"
                f"- URL: {URL}"
            )

            # 2) 기존 로직 유지 (재고 확정 시 알림 폭격 모드)
            if result == "IN_STOCK":
                time.sleep(CONFIRM_DELAY_SEC)
                result2 = goto_and_detect(page)

                if result2 == "IN_STOCK":
                    state["last_ok_epoch"] = now_epoch
                    state["bad_streak"] = 0
                    save_state(state)

                    send_telegram(
                        "✅ 구매 가능 확정(2회 확인)\n"
                        f"- 시각: {now_kst_str()}\n"
                        f"- 링크: {URL}\n"
                        "→ 이후 1분마다 알림을 계속 보냅니다(구매 가능 유지 시)"
                    )
                    alert_mode(page)
                else:
                    state["last_ok_epoch"] = now_epoch
                    state["bad_streak"] = 0
                    save_state(state)

            elif result == "SOLD_OUT":
                state["last_ok_epoch"] = now_epoch
                state["bad_streak"] = 0
                save_state(state)

            else:
                state["bad_streak"] = int(state.get("bad_streak", 0)) + 1
                save_state(state)
                watchdog_if_needed(state, is_ok=False)

        finally:
            browser.close()
