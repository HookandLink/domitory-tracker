# 행복기숙사 상시모집 모니터링 봇 (홍제 + 동소문)

홍제·동소문 행복기숙사 공지사항을 자동으로 감시하여 **상시모집** 관련 공고가 올라오면 **텔레그램**으로 즉시 알림을 보내는 자동화 시스템입니다.

---

## 작동 원리

```
GitHub Actions (30분마다)
    │
    ▼
main.py 실행
    │
    ├─ 홍제·동소문 행복기숙사 공지사항 페이지 크롤링
    │
    ├─ seen_notices.json 과 비교 → 새 공고 추출
    │
    ├─ 새 공고 제목에 '상시' 또는 '모집' 포함 여부 확인
    │
    ├─ 조건 만족 시 텔레그램 메시지 전송
    │
    └─ seen_notices.json 업데이트 후 저장소에 커밋
```

---

## 파일 구조

```
domitory-tracker/
├── main.py                        # 메인 크롤링 + 알림 스크립트
├── requirements.txt               # Python 의존성
├── seen_notices.json              # 이전 공고 목록 (자동 생성)
└── .github/
    └── workflows/
        └── monitor.yml            # GitHub Actions 워크플로우
```

---

## 설정 방법 (단계별 가이드)

### STEP 1 — 텔레그램 봇 만들기

1. 텔레그램 앱에서 **[@BotFather](https://t.me/BotFather)** 를 검색하여 대화를 시작합니다.
2. `/newbot` 명령어를 입력합니다.
3. 봇 이름을 입력합니다. (예: `홍제기숙사 알림봇`)
4. 봇 아이디(username)를 입력합니다. (예: `hongje_dorm_bot`, 반드시 `bot`으로 끝나야 함)
5. BotFather가 **API Token**을 발급해줍니다.
   ```
   예시: 7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   이 토큰을 복사해두세요.

### STEP 2 — 내 채팅 ID 확인하기

1. 위에서 만든 봇을 텔레그램에서 검색하여 `/start` 메시지를 보냅니다.
2. 브라우저에서 아래 URL에 접속합니다. (`YOUR_TOKEN` 자리에 발급받은 토큰 입력)
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
3. 응답 JSON에서 `"chat"` → `"id"` 값을 찾습니다.
   ```json
   {"ok":true,"result":[{"message":{"chat":{"id":123456789,...}}}]}
   ```
   이 숫자가 **TELEGRAM_CHAT_ID**입니다.

> **그룹 채팅에 알림을 받고 싶다면**: 봇을 그룹에 추가하고 그룹 채팅 ID를 사용하세요. 그룹 ID는 보통 `-` 로 시작합니다(예: `-1001234567890`).

### STEP 3 — GitHub 저장소에 Secrets 등록

1. GitHub 저장소 페이지 → **Settings** 탭 클릭
2. 좌측 메뉴 **Secrets and variables** → **Actions** 클릭
3. **New repository secret** 버튼으로 아래 두 값을 등록합니다.

   | Secret 이름          | 값                                      |
   |---------------------|-----------------------------------------|
   | `TELEGRAM_BOT_TOKEN` | BotFather에서 발급받은 API 토큰          |
   | `TELEGRAM_CHAT_ID`   | STEP 2에서 확인한 채팅 ID (숫자)         |

### STEP 4 — GitHub Actions 활성화 확인

1. 저장소 → **Actions** 탭 클릭
2. 워크플로우가 비활성화 상태라면 **"I understand my workflows, go ahead and enable them"** 버튼 클릭
3. `행복기숙사 공지 모니터링 (홍제 + 동소문)` 워크플로우가 목록에 보이면 완료입니다.

### STEP 5 — 첫 실행 테스트

1. **Actions** 탭 → `행복기숙사 공지 모니터링 (홍제 + 동소문)` 워크플로우 선택
2. **Run workflow** → **Run workflow** 클릭
3. 실행 후 로그를 확인하고, 텔레그램으로 시작 알림이 오는지 확인합니다.

---

## 모니터링 주기 변경

`.github/workflows/monitor.yml` 파일의 `cron` 값을 수정합니다.

```yaml
schedule:
  - cron: "*/30 * * * *"   # 30분마다 (기본값)
  - cron: "*/10 * * * *"   # 10분마다
  - cron: "0 * * * *"      # 1시간마다
```

> GitHub Actions의 무료 플랜은 월 2,000분을 제공합니다.
> 30분 주기 실행 시 월 약 1,440분 소요 (공개 저장소는 무제한 무료).

---

## 키워드 변경

`main.py` 상단의 `KEYWORDS` 리스트를 수정합니다.

```python
KEYWORDS = ["상시", "모집"]   # 기본값
KEYWORDS = ["상시모집", "입사신청"]  # 더 구체적인 키워드
```

---

## 로컬에서 직접 실행하기 (테스트용)

```bash
# 저장소 클론
git clone https://github.com/<your-username>/domitory-tracker.git
cd domitory-tracker

# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정 (텔레그램 없이 테스트할 경우 생략 가능)
export TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
export TELEGRAM_CHAT_ID="YOUR_CHAT_ID"

# 실행
python main.py
```

텔레그램 환경변수 없이 실행하면 알림 대신 터미널에 메시지를 출력합니다.

---

## 트러블슈팅

| 증상 | 해결 방법 |
|------|-----------|
| Actions가 실행되지 않음 | Actions 탭에서 워크플로우가 활성화되어 있는지 확인 |
| 텔레그램 알림이 오지 않음 | Secrets 이름이 정확한지 확인 (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) |
| `공지사항을 찾지 못했습니다` 출력 | 페이지 구조가 변경된 것일 수 있음. Actions 로그를 확인하고 Issue 등록 |
| 봇에게 `/start` 를 보내야 메시지 수신 | 봇을 만든 후 반드시 봇에게 `/start` 메시지를 먼저 보내야 함 |

---

## 참고 링크

- [홍제 행복기숙사 공지사항](https://hongje.happydorm.or.kr/hongje/bbs/getBbsList.do?menu_id=010500)
- [동소문 행복기숙사 공지사항](https://www.happydorm.or.kr/dongsomun/ko/0601/board/board/)
- [Telegram BotFather](https://t.me/BotFather)
- [GitHub Actions 공식 문서](https://docs.github.com/ko/actions)
