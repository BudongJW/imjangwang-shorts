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

# 목표 게시 시각(KST, HH:MM). 워크플로가 몇 시에 돌든 이 시각에 공개되도록
# YouTube 예약 공개(status.publishAt)로 올린다.
# 08~09시대를 노리는 이유는 리포트 수치다 — 게시 시각대별 하루당 조회수
# 중앙값이 08시대 25.4, 09시대 26.3인데 10시 이후는 4 안팎이다.
PUBLISH_TARGET_KST = os.getenv("PUBLISH_TARGET_KST", "08:40")
# 예약을 걸려면 목표까지 최소 이만큼 남아 있어야 한다(API 반영 여유).
PUBLISH_MIN_LEAD_MIN = int(os.getenv("PUBLISH_MIN_LEAD_MIN", "15"))

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
PUBLISHER_FEEDS = [
    ("매일경제", "https://www.mk.co.kr/rss/50300009/"),      # 부동산 전용
    ("뉴시스", "https://newsis.com/RSS/economy.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ("한겨레", "https://www.hani.co.kr/rss/economy/"),
    ("경향신문", "https://www.khan.co.kr/rss/rssdata/economy_news.xml"),
]

# 신뢰도 낮은/광고성 도메인 제외
NEWS_BLOCK_DOMAINS = ["blog.", "cafe.", "post.naver", "youtube.com"]
