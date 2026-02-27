import os
import sys
import re
import json
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from dataclasses import dataclass
from datetime import datetime

# ── 기숙사 사이트 설정 ────────────────────────────────────────────────────────

@dataclass
class DormSite:
    name: str            # 기숙사 이름 (텔레그램 메시지 표시용)
    emoji: str           # 구분 이모지
    site_key: str        # seen_notices.json 내 키 (영문 고유값)
    base_url: str        # 기본 도메인 URL
    notice_url: str      # 공지사항 목록 페이지 URL


SITES = [
    DormSite(
        name="홍제 행복기숙사",
        emoji="🏠",
        site_key="hongje",
        base_url="https://hongje.happydorm.or.kr",
        notice_url="https://hongje.happydorm.or.kr/hongje/bbs/getBbsList.do?menu_id=010500",
    ),
    DormSite(
        name="동소문 행복기숙사",
        emoji="🏢",
        site_key="dongsomun",
        base_url="https://www.happydorm.or.kr",
        notice_url="https://www.happydorm.or.kr/dongsomun/ko/0601/board/board/",
    ),
]

# ── 전역 설정 ─────────────────────────────────────────────────────────────────

KEYWORDS = ["상시", "모집"]
SEEN_FILE = "seen_notices.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ── seen_notices 저장/로드 ────────────────────────────────────────────────────

def load_seen() -> dict[str, set]:
    """
    이전에 확인한 공고 ID를 사이트별로 불러옵니다.
    반환 형식: {"hongje": {"id1", "id2"}, "dongsomun": {"id1"}, ...}
    """
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # 이전 버전(flat list)과의 하위 호환
        if isinstance(raw, list):
            return {"hongje": set(raw)}
        return {k: set(v) for k, v in raw.items()}
    return {}


def save_seen(seen: dict[str, set]) -> None:
    """확인한 공고 ID를 사이트별로 파일에 저장합니다."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {k: sorted(v) for k, v in seen.items()},
            f,
            ensure_ascii=False,
            indent=2,
        )

# ── 크롤링 공통 유틸 ──────────────────────────────────────────────────────────

def make_session(site: DormSite) -> requests.Session:
    """
    사이트별 세션을 생성합니다.
    동소문처럼 jsessionid가 필요한 사이트는 메인 페이지 방문으로 쿠키를 선취득합니다.
    홍제는 SSL 인증서 호스트명 불일치로 verify=False 적용합니다.
    """
    session = requests.Session()
    session.headers.update({**BASE_HEADERS, "Referer": site.base_url})

    if site.site_key == "hongje":
        session.verify = False

    # 세션 쿠키 획득을 위해 메인 페이지 선방문 (실패해도 계속 진행)
    try:
        session.get(site.base_url, timeout=10)
    except requests.RequestException:
        pass

    return session


def _extract_idx_from_url(url: str) -> str | None:
    """URL에서 idx 또는 nttSn 파라미터 값을 추출합니다."""
    m = re.search(r"[?&](?:idx|nttSn)=(\d+)", url)
    return m.group(1) if m else None


def _extract_idx_from_onclick(onclick: str) -> str | None:
    """onclick 핸들러에서 숫자 ID를 추출합니다. (4자리 이상 숫자)"""
    m = re.search(r"['\"](\d{4,})['\"]", onclick)
    return m.group(1) if m else None


def _build_link(site: DormSite, uid: str, raw_href: str) -> str:
    """사이트 유형에 맞게 상세 페이지 URL을 구성합니다."""
    # 동소문: idx 파라미터 방식
    if site.site_key == "dongsomun":
        return f"{site.base_url}/dongsomun/ko/0601/board/board/?mode=V&idx={uid}"
    # 홍제: nttSn 파라미터 방식 (getBbsDetail.do)
    if site.site_key == "hongje":
        return f"{site.base_url}/hongje/bbs/getBbsDetail.do?menu_id=010500&nttSn={uid}"
    # 기타: raw_href 사용
    if raw_href.startswith("http"):
        return raw_href
    return site.base_url + raw_href if raw_href.startswith("/") else site.notice_url


# ── 공지사항 파싱 ─────────────────────────────────────────────────────────────

def _parse_notices_from_soup(soup: BeautifulSoup, site: DormSite) -> list[dict]:
    """
    BeautifulSoup 객체에서 공지사항 목록을 파싱합니다.

    지원하는 구조:
    1. <table> > <tbody> > <tr> > <td> (한국 공공기관 CMS 일반 패턴)
    2. <ul> > <li> (리스트형 CMS)

    각 항목에서 추출:
    - title: 공고 제목
    - uid: 고유 식별자 (idx, nttSn, 또는 제목)
    - link: 상세 페이지 URL
    """
    notices = []
    SKIP_TITLES = {"더보기", "목록", "[파일]", "첨부파일"}

    def try_extract_notice(tag) -> dict | None:
        """anchor 태그에서 공고 정보를 추출합니다."""
        title = tag.get_text(strip=True)
        if not title or title in SKIP_TITLES:
            return None

        raw_href = tag.get("href", "").strip()
        onclick = tag.get("onclick", "").strip()

        # ── UID 추출 우선순위 ──────────────────────────────────────────────────
        # 1) href에 idx 또는 nttSn 파라미터가 있으면 그 값을 사용
        uid = _extract_idx_from_url(raw_href)

        # 2) onclick에 숫자 ID가 있으면 사용
        if not uid:
            uid = _extract_idx_from_onclick(onclick)

        # 3) href 자체를 uid로 (상대/절대 경로)
        if not uid and raw_href and raw_href not in ("#", "javascript:void(0)", "javascript:;", ""):
            uid = raw_href

        # 4) 최후 수단: 제목을 uid로
        if not uid:
            uid = title

        # ── 링크 구성 ─────────────────────────────────────────────────────────
        # href가 완전한 URL이면 그대로 사용
        if raw_href.startswith("http"):
            link = raw_href
        # idx/nttSn 기반이면 사이트별 상세 URL 구성
        elif re.match(r"^\d{4,}$", uid):
            link = _build_link(site, uid, raw_href)
        # 상대 경로
        elif raw_href.startswith("/"):
            link = site.base_url + raw_href
        else:
            link = site.notice_url

        return {"uid": uid, "title": title, "link": link}

    # ── 전략 1: table > tbody > tr > td ──────────────────────────────────────
    for table in soup.find_all("table"):
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")
        for row in rows:
            for cell in row.find_all("td"):
                a = cell.find("a")
                if not a:
                    continue
                notice = try_extract_notice(a)
                if notice:
                    notices.append(notice)
                    break  # 행당 첫 번째 유효 항목만
        if notices:
            return notices

    # ── 전략 2: ul > li > a ───────────────────────────────────────────────────
    for li in soup.select("ul li"):
        a = li.find("a")
        if not a:
            continue
        notice = try_extract_notice(a)
        if notice:
            notices.append(notice)

    return notices


def fetch_notices(site: DormSite) -> list[dict]:
    """
    지정한 기숙사 사이트의 공지사항을 크롤링합니다.
    반환값: [{"uid": str, "title": str, "link": str}, ...]
    """
    session = make_session(site)
    try:
        resp = session.get(site.notice_url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"[{site.name}] 페이지 접근 실패: {e}") from e

    soup = BeautifulSoup(resp.text, "lxml")
    return _parse_notices_from_soup(soup, site)

# ── 텔레그램 ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> None:
    """텔레그램 봇을 통해 메시지를 전송합니다."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[경고] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 설정되지 않음 → 콘솔 출력")
        print(f"  ─ 메시지:\n{message}\n")
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
        print("    -> 텔레그램 전송 성공")
    except requests.RequestException as e:
        print(f"    -> 텔레그램 전송 실패: {e}")

# ── 메인 로직 ─────────────────────────────────────────────────────────────────

def has_keyword(title: str) -> bool:
    return any(kw in title for kw in KEYWORDS)


def monitor_site(site: DormSite, seen_for_site: set) -> tuple[set, list[dict]]:
    """
    단일 사이트를 모니터링합니다.
    반환값: (업데이트된 seen 집합, 알림 대상 공고 목록)
    """
    print(f"\n  [{site.name}] 크롤링 중... ({site.notice_url})")

    try:
        notices = fetch_notices(site)
    except RuntimeError as e:
        print(f"  [오류] {e}")
        return seen_for_site, []

    if not notices:
        print(f"  [경고] 공지사항을 찾지 못했습니다. 페이지 구조가 변경되었을 수 있습니다.")
        return seen_for_site, []

    print(f"  수집된 공고 수: {len(notices)}")

    new_alerts = []
    for notice in notices:
        uid = notice["uid"]
        if uid not in seen_for_site:
            seen_for_site.add(uid)
            if has_keyword(notice["title"]):
                new_alerts.append(notice)
        # 이미 본 공고도 seen에 계속 유지 (중복 방지)
        seen_for_site.add(uid)

    print(f"  키워드 매칭 새 공고: {len(new_alerts)}건")
    return seen_for_site, new_alerts


def main() -> None:
    if "--test" in sys.argv:
        print("[테스트] 텔레그램 테스트 알림 전송 중...")
        site_list = "\n".join(f"  {s.emoji} {s.name}" for s in SITES)
        send_telegram(
            "🧪 <b>테스트 알림</b>\n\n"
            "텔레그램 연동이 정상적으로 작동합니다.\n\n"
            f"<b>모니터링 대상:</b>\n{site_list}\n\n"
            f"<b>키워드:</b> {', '.join(KEYWORDS)}"
        )
        print("  완료. 텔레그램을 확인하세요.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 행복기숙사 공지사항 모니터링 시작")
    print(f"  모니터링 사이트: {', '.join(s.name for s in SITES)}")
    print(f"  키워드: {', '.join(KEYWORDS)}")

    # 1. 이전 공고 목록 로드 (사이트별)
    seen_all = load_seen()
    is_first_run = not bool(seen_all)

    # 2. 각 사이트 모니터링
    all_alerts: list[tuple[DormSite, dict]] = []

    for site in SITES:
        seen_for_site = seen_all.get(site.site_key, set())
        updated_seen, alerts = monitor_site(site, seen_for_site)
        seen_all[site.site_key] = updated_seen
        for notice in alerts:
            all_alerts.append((site, notice))

    # 3. 텔레그램 알림 전송
    if all_alerts:
        print(f"\n[알림] 총 {len(all_alerts)}건의 관련 공고 발견!")
        for site, notice in all_alerts:
            print(f"  {site.emoji} {site.name}: {notice['title']}")
            message = (
                f"{site.emoji} <b>{site.name} 새 공고 알림</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📢 <b>{notice['title']}</b>\n\n"
                f"🔗 <a href='{notice['link']}'>공고 바로가기</a>\n"
                f"📋 <a href='{site.notice_url}'>전체 공지사항 보기</a>"
            )
            send_telegram(message)
    else:
        print("\n  새로운 상시모집 공고 없음")

    # 4. 첫 실행 시 시작 알림
    if is_first_run:
        site_list = "\n".join(f"  {s.emoji} {s.name}" for s in SITES)
        send_telegram(
            "✅ <b>행복기숙사 모니터링 시작</b>\n\n"
            f"<b>모니터링 대상:</b>\n{site_list}\n\n"
            f"<b>키워드:</b> {', '.join(KEYWORDS)}"
        )

    # 5. 업데이트된 목록 저장
    save_seen(seen_all)
    total = sum(len(v) for v in seen_all.values())
    print(f"\n[완료] 저장된 총 공고 수: {total}")


if __name__ == "__main__":
    main()
