import os
import re
import json
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# =========================
# 대표님이 바꾸실 건 여기 1줄만
# =========================
TARGET_URL = "https://store.sony.co.kr/product-view/131272260"

# =========================
# 키워드 (이 두 개로만 판정)
# - 품절일 때:   "일시품절"
# - 구매 가능:   "바로 구매하기"
# =========================
SOLD_OUT_KEYWORD = "일시품절"
BUY_NOW_KEYWORD = "바로 구매하기"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# 상태/부팅 플래그 파일
STATE_FILE = Path("last_status.json")
BOOT_FILE = Path("boot_notified.json")

# 알림 설정
BURST_COUNT = 10
BURST_INTERVAL = 1.0  # seconds


def telegram_send(text: str) -> None:
    """
    텔레그램 전송 (실패 원인을 Actions 로그에 남김)
    """
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 비어있습니다.")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)

    print("telegram_status:", r.status_code)
    print("telegram_response:", r.text[:300])

    r.raise_for_status()


def compact(s: str) -> str:
    """
    공백/줄바꿈/탭을 모두 제거해서 비교 안정성 확보
    """
    return re.sub(r"\s+", "", s or "")


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
