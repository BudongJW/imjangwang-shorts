"""프로젝트 전역 설정."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── 경로 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
AUDIO_DIR = OUTPUT_DIR / "audio"
SRT_DIR = OUTPUT_DIR / "srt"
VIDEO_DIR = OUTPUT_DIR / "video"
FINAL_DIR = OUTPUT_DIR / "final"
ASSETS_DIR = PROJECT_ROOT / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
BGM_DIR = ASSETS_DIR / "bgm"
FACES_DIR = ASSETS_DIR / "faces"

# 정책 비판 대상(이재명 정부) 얼굴을 영상에 부각. 파일 없으면 자동 생략.
POLITICIAN_FACE = FACES_DIR / "leejaemyung.jpg"   # 하위호환(폴더가 비었을 때 폴백)
# 얼굴 사진은 파일 하나로 고정하지 않고 폴더 전체를 쓴다. assets/faces/ 에
# 파일을 더 넣기만 하면 날짜별로 돌아간다 — 코드 수정 불필요.
FACE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
POLITICIAN_FACE_ENABLED = os.getenv("POLITICIAN_FACE", "1") == "1"
# 대본에서 '정부'를 명시할 대상(정책 비판 편집 방향). 얼굴과 함께 적용.
GOV_NAME = os.getenv("GOV_NAME", "이재명 정부")

# 배경음: 나레이션 아래 '들리되 방해 안 되는' 수준 (0=무음)
BGM_VOLUME = float(os.getenv("BGM_VOLUME", "0.25"))

# ── LLM (대본 생성) ─────────────────────────────────────
# Gemini 키는 콤마로 여러 개 넣으면 라운드로빈 로테이션.
GEMINI_API_KEYS = [
    k.strip() for k in os.getenv("GEMINI_API_KEYS", os.getenv("GEMINI_API_KEY", "")).split(",") if k.strip()
]
# 빈 문자열(워크플로우가 미정의 vars를 넘길 때)도 기본값으로 처리 — `or` 사용
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
# 주 모델 실패(쿼터/모델명) 시 시도할 백업 모델
# (gemini-2.0-flash는 2026 서비스 종료 → 제거. flash-latest는 현행 flash 별칭)
GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash-lite"]

# ── 이미지 (선택) ───────────────────────────────────────
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# Gemini 이미지 생성(썸네일/타이틀카드 배경). 키는 'AQ.' 신형 포맷.
GEMINI_IMAGE_KEY = os.getenv("GEMINI_IMAGE_KEY", "")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
# AI 이미지 생성은 결제(billing) 필요 → 기본 OFF. 결제 켜면 AI_THUMBNAIL=1 로 재활성화.
AI_THUMBNAIL = os.getenv("AI_THUMBNAIL", "0") == "1"

# ── 영상 규격 ───────────────────────────────────────────
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
SHORTS_FPS = 30
SHORTS_MAX_DURATION = 60  # 초

# ── 개선안 반영 파라미터 ────────────────────────────────
# 도입부 타이틀 카드 최대 노출(초).
# 4.0 → 1.5. 타이틀카드는 썸네일로도 쓰는 바로 그 그림이라, 썸네일을 보고
# 들어온 시청자에게 같은 정지 화면을 4초 더 보여주고 있었다. 55초 영상에서
# 타이틀 4초 + 기사캡처 8초 = 12초(22%)가 정지 화면이었다.
# 지속률이 조회수를 만드는 구조(길이↔지속률 -0.52, 지속률↔조회수 +0.68)라
# 도입부 이탈을 줄이는 쪽에 건다. 7편 쌓이면 지속률 중앙값으로 검증한다.
TITLE_CARD_MAX_SEC = 1.5

# 도입 타이틀카드를 영상 '안'에도 넣을지. 썸네일로는 계속 쓴다.
#
# 쇼츠 이탈의 50~60%가 첫 3초에 일어나고, 그 이탈을 부르는 대표적인 형태로
# '인트로 카드'가 지목된다. 시청자는 피드에서 이미 썸네일을 봤는데 영상 첫
# 장면이 같은 카드면 1.5초 동안 새 정보가 없다. 그 1.5초가 스와이프 구간이다.
#
# 꺼도 맥락은 안 잃는다. 헤드라인 배너가 타이틀카드 이후 전 구간 상단에
# 떠 있는데, 카드를 빼면 그 배너가 0초부터 뜬다. 즉 헤드라인은 그대로 보이고
# 나레이션과 첫 수치 콜아웃이 곧바로 시작된다.
TITLE_CARD_IN_VIDEO = False

# 배경에 섞을 실사 b-roll 영상 개수(Pexels). 0이면 사진만 쓴다.
# 사진과 번갈아 배치된다. 2일 때는 사진 4장 + 영상 2개가 50초 동안
# 2~3바퀴 돌아 같은 화면이 반복되는 게 눈에 띄었다(09-25 점검).
BROLL_VIDEO_N = 3

# 목표 게시 시각(KST, HH:MM). 워크플로가 몇 시에 돌든 이 시각에 공개되도록
# YouTube 예약 공개(status.publishAt)로 올린다.
# 08~09시대를 노리는 이유는 리포트 수치다 — 게시 시각대별 하루당 조회수
# 중앙값이 08시대 25.4, 09시대 26.3인데 10시 이후는 4 안팎이다.
PUBLISH_TARGET_KST = os.getenv("PUBLISH_TARGET_KST", "08:40")
# 예약을 걸려면 목표까지 최소 이만큼 남아 있어야 한다(API 반영 여유).
PUBLISH_MIN_LEAD_MIN = int(os.getenv("PUBLISH_MIN_LEAD_MIN", "15"))

# ── 대본 길이 ───────────────────────────────────────────
# 실측 환산 약 6.4자/초.
#   normal : 310~350자(48~55초), 상한 380자(≈59초) — 현재 기본값.
#   short  : 200~240자(31~37초), 상한 270자(≈42초) — 2026-09-20 시작 A/B 실험.
# normal을 고른 근거는 지속률이 잡힌 영상 20개다(2026-09-13).
#   50~60초 9개 → 지속률 중앙 72.2% / 조회수 중앙 1,741
#   60초 초과 11개 → 지속률 중앙 61.6% / 조회수 중앙 1,201
#   상관: 길이↔지속률 -0.52, 지속률↔조회수 +0.68, 길이↔조회수 -0.45
# 다만 관측된 길이가 45~92초뿐이라 45초 미만 구간은 데이터가 없다. short는
# 그 빈 구간을 직접 재보려는 실험이며, 길이 외에는 아무것도 바꾸지 않는다.
# 전환은 리포지터리 변수 SCRIPT_LEN_MODE=short 하나로 끝난다(워크플로가 넘긴다).
CHARS_PER_SEC = 6.4
SCRIPT_LEN_MODE = os.getenv("SCRIPT_LEN_MODE", "normal").strip().lower()
_LEN_PRESETS = {"normal": (310, 350, 380), "short": (200, 240, 270)}
_len = _LEN_PRESETS.get(SCRIPT_LEN_MODE, _LEN_PRESETS["normal"])
# 프리셋 밖 미세조정이 필요할 때만 개별 환경변수로 덮어쓴다.
SCRIPT_CHARS_MIN = int(os.getenv("SCRIPT_CHARS_MIN", str(_len[0])))
SCRIPT_CHARS_MAX = int(os.getenv("SCRIPT_CHARS_MAX", str(_len[1])))
SCRIPT_CHARS_CAP = int(os.getenv("SCRIPT_CHARS_CAP", str(_len[2])))

IMAGE_MAX_SEC = 3.0           # 이미지 1컷 최대 노출(초) — 정지 이미지 12초 금지
# 숫자 콜아웃 1개 최대 노출(초). 구절이 길면 큰 숫자가 10초씩 박혀 있게 된다.
STAT_MAX_SEC = 3.5
KENBURNS = True               # 이미지 줌/팬 모션
ARTICLE_HIGHLIGHT = True      # 기사 캡처에 형광펜 하이라이트

# ── 채널 브랜딩 ─────────────────────────────────────────
CHANNEL_NAME = "공인중개사 임장왕"
CHANNEL_HANDLE = "@임장왕채널"
DEFAULT_HASHTAGS = ["부동산", "집값", "부동산뉴스", "임장왕", "shorts"]
FIXED_CTA = "부동산 소식 매일 정리 → 구독 @임장왕채널"

# ── 뉴스 수집 ───────────────────────────────────────────
# Google 뉴스 RSS(한국어) 검색 쿼리들. 순서대로 시도해 후보 기사 확보.
NEWS_QUERIES = [
    # 핵심(직결) + 정책 비판 각도를 함께 수집 → 좋은 소재 자동 선별
    "부동산 세금 부작용",
    "전세 대출 규제 논란",
    "월세 폭등 정책",
    "부동산 규제 부작용",
    "종부세 양도세 반발",
    "부동산 정책 실패",
    "부동산 집값",
    "아파트 전세",
    "재건축 규제 반발",
    "청약 분양",
]
NEWS_MAX_CANDIDATES = 30

# 언론사 RSS. 구글뉴스 RSS는 원문 URL을 주지 않는다 — 링크가 news.google.com
# 토큰이고, 그 페이지는 JS로만 원문으로 넘어간다(2026-09-15 확인: 응답 HTML에
# 외부 URL이 아예 없고, 토큰을 base64 디코딩해도 URL이 안 나온다). 그 결과
# 본문 확보에 통째로 실패하는 날이 생기고, 그러면 대본이 제목만 보고 쓰이고
# 기사 캡처도 리다이렉트 페이지에서 죽는다.
# 언론사 피드는 원문 URL과 본문을 그대로 주므로 후보 풀에 함께 넣는다.
# (실측 본문 674~1032자 + 대표 이미지 확보)
# 경제 전반 피드는 부동산 외 기사도 섞이지만 topic_score가 걸러 준다.
# 구글뉴스는 후보를 폭넓게 주지만 본문을 못 가져온다(과제 #14에서 확인:
# 링크가 CBMi... 토큰이고 원문 URL이 페이지에 없다). 2026-09-17 실측에서
# 상위 8개 중 7개가 구글뉴스 경유라 본문 0자로 탈락하고, 통과한 1개가
# 매일경제 직접 RSS였다. 즉 실질 후보는 이 목록이 전부다.
#
# 그런데 부동산 전용 피드가 매일경제 하나뿐이었다. 나머지는 경제 전반이라
# 부동산 기사가 다 들어오지 않는다. 그 결과 매일경제 부동산 피드를 채우는
# [MAI부동산] 시세 단신이 이틀 연속 영상 소재로 뽑혔다.
#
# 부동산 전용 3곳을 추가한다. 본문 추출까지 실측으로 확인했다.
#   한국경제 50건/본문 2,307자 · 아시아경제 76건/1,709자 · 국민일보 10건/2,104자
# 조선비즈 부동산은 본문이 JS로 렌더돼 0자라 넣지 않았다.
PUBLISHER_FEEDS = [
    ("매일경제", "https://www.mk.co.kr/rss/50300009/"),      # 부동산 전용
    ("한국경제", "https://www.hankyung.com/feed/realestate"),  # 부동산 전용
    ("아시아경제", "https://www.asiae.co.kr/rss/realestate.htm"),  # 부동산 전용
    ("국민일보", "https://www.kmib.co.kr/rss/data/kmibEcoRss.xml"),
    ("뉴시스", "https://newsis.com/RSS/economy.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ("한겨레", "https://www.hani.co.kr/rss/economy/"),
    ("경향신문", "https://www.khan.co.kr/rss/rssdata/economy_news.xml"),
]

# 신뢰도 낮은/광고성 도메인 제외
NEWS_BLOCK_DOMAINS = ["blog.", "cafe.", "post.naver", "youtube.com"]
