import os
import sys
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────────────────────────────
BASE_URL = "https://hongje.happydorm.or.kr"
NOTICE_URL = f"{BASE_URL}/hongje/bbs/getBbsList.do?menu_id=010500"
SEEN_FILE = "seen_notices.json"
KEYWORDS = ["상시", "모집"]

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE_URL,
}

# ── 저장/로드 ─────────────────────────────────────────────────────────────────

def load_seen() -> set:
    """이전에 확인한 공고 ID 목록을 파일에서 불러옵니다."""
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set) -> None:
    """확인한 공고 ID 목록을 파일에 저장합니다."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)

# ── 크롤링 ────────────────────────────────────────────────────────────────────

def fetch_notices() -> list[dict]:
    """
    홍제 행복기숙사 공지사항 페이지를 크롤링하여 공고 목록을 반환합니다.

    반환값: [{"id": str, "title": str, "link": str}, ...]
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        resp = session.get(NOTICE_URL, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"페이지 접근 실패: {e}") from e

    soup = BeautifulSoup(resp.text, "lxml")
    notices = []

    # ── 파싱 전략 1: <table> 기반 공지 목록 ─────────────────────────────────
    # 홍제 행복기숙사 CMS는 표준 한국 공공기관 CMS 패턴을 따릅니다.
    # tbody > tr 구조에서 제목 셀(a 태그 또는 onclick)을 찾습니다.
    tables = soup.find_all("table")
    for table in tables:
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            notice = _extract_from_row(cells)
            if notice:
                notices.append(notice)

        if notices:
            break  # 첫 번째 유효한 테이블에서 멈춤

    # ── 파싱 전략 2: <ul>/<li> 기반 공지 목록 (테이블 없을 때) ──────────────
    if not notices:
        for li in soup.select("ul li"):
            a = li.find("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            link, uid = _resolve_link(a, li)
            notices.append({"id": uid, "title": title, "link": link})

    return notices


def _extract_from_row(cells: list) -> dict | None:
    """
    테이블 행(tr)의 td 목록에서 공고 ID, 제목, 링크를 추출합니다.
    한국 CMS의 일반적인 패턴:
      - 직접 <a href="..."> 링크
      - onclick="fn_view('nttSn')" 또는 getBbsDetail.do 형태
    """
    for cell in cells:
        a = cell.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title or title in ("더보기", "목록", "[파일]"):
            continue

        link, uid = _resolve_link(a, cell)
        return {"id": uid, "title": title, "link": link}
    return None


def _resolve_link(tag, parent) -> tuple[str, str]:
    """
    <a> 태그 또는 상위 요소에서 실제 URL과 고유 ID를 추출합니다.
    1. href가 실제 URL이면 그대로 사용
    2. onclick에서 파라미터(nttSn 등) 추출 후 상세 URL 구성
    3. 링크를 찾을 수 없으면 목록 URL 사용
    """
    href = tag.get("href", "").strip()
    onclick = tag.get("onclick", "") or (
        parent.get("onclick", "") if hasattr(parent, "get") else ""
    )

    # href가 실제 URL인 경우
    if href and href not in ("#", "javascript:void(0)", "javascript:;"):
        if href.startswith("http"):
            link = href
        else:
            link = BASE_URL + href
        uid = href
        return link, uid

    # onclick에서 파라미터 추출 (예: fn_view('12345'), getBbsDetail('12345'))
    import re
    match = re.search(r"['\"](\d{4,})['\"]", onclick)
    if match:
        ntt_id = match.group(1)
        link = f"{BASE_URL}/hongje/bbs/getBbsDetail.do?menu_id=010500&nttSn={ntt_id}"
        return link, ntt_id

    # 폼 파라미터 방식 (input hidden 등)
    form = parent.find_parent("form") if hasattr(parent, "find_parent") else None
    if form:
        ntt_input = form.find("input", {"name": "nttSn"})
        if ntt_input:
            ntt_id = ntt_input.get("value", "")
            link = f"{BASE_URL}/hongje/bbs/getBbsDetail.do?menu_id=010500&nttSn={ntt_id}"
            return link, ntt_id

    title_text = tag.get_text(strip=True)
    return NOTICE_URL, title_text  # fallback

# ── 텔레그램 ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> None:
    """텔레그램 봇을 통해 메시지를 전송합니다."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[경고] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 설정되지 않았습니다.")
        print(f"  전송할 메시지:\n{message}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        print("  -> 텔레그램 전송 성공")
    except requests.RequestException as e:
        print(f"  -> 텔레그램 전송 실패: {e}")

# ── 메인 로직 ─────────────────────────────────────────────────────────────────

def has_keyword(title: str) -> bool:
    """공고 제목에 모니터링 키워드가 포함되어 있는지 확인합니다."""
    return any(kw in title for kw in KEYWORDS)


def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 홍제 행복기숙사 공지사항 모니터링 시작")

    # 1. 이전에 확인한 공고 목록 로드
    seen = load_seen()
    print(f"  이전 저장 공고 수: {len(seen)}")

    # 2. 현재 공지사항 크롤링
    try:
        notices = fetch_notices()
    except RuntimeError as e:
        print(f"[오류] {e}")
        sys.exit(1)

    if not notices:
        print("[경고] 공지사항을 찾지 못했습니다. 페이지 구조가 변경되었을 수 있습니다.")
        print(f"  직접 확인: {NOTICE_URL}")
        sys.exit(0)

    print(f"  현재 공지사항 수: {len(notices)}")

    # 3. 새 공고 중 키워드 포함 항목 필터링
    new_alerts = []
    all_new = []
    for notice in notices:
        uid = notice["id"]
        if uid not in seen:
            all_new.append(notice)
            if has_keyword(notice["title"]):
                new_alerts.append(notice)
        seen.add(uid)

    print(f"  새 공고 수: {len(all_new)}")
    print(f"  키워드 매칭 공고 수: {len(new_alerts)}")

    # 4. 알림 전송
    if new_alerts:
        print("\n[알림] 관련 공고 발견!")
        for notice in new_alerts:
            print(f"  제목: {notice['title']}")
            print(f"  링크: {notice['link']}")
            message = (
                "🏠 <b>홍제 행복기숙사 새 공고 알림</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📢 <b>{notice['title']}</b>\n\n"
                f"🔗 <a href='{notice['link']}'>공고 바로가기</a>\n"
                f"📋 <a href='{NOTICE_URL}'>전체 공지사항 보기</a>"
            )
            send_telegram(message)
    else:
        print("\n  새로운 상시모집 공고 없음")

    # 5. 처음 실행 시 (seen이 비어있었고 공고가 있는 경우) 시작 알림
    if not load_seen() and notices:
        send_telegram(
            "✅ <b>홍제 행복기숙사 모니터링 시작</b>\n\n"
            f"키워드: {', '.join(KEYWORDS)}\n"
            f"🔗 <a href='{NOTICE_URL}'>공지사항 페이지</a>"
        )

    # 6. 업데이트된 목록 저장
    save_seen(seen)
    print(f"\n[완료] 저장된 공고 수: {len(seen)}")


if __name__ == "__main__":
    main()
