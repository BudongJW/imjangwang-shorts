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
POLITICIAN_FACE = FACES_DIR / "leejaemyung.jpg"
POLITICIAN_FACE_ENABLED = os.getenv("POLITICIAN_FACE", "1") == "1"

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
TITLE_CARD_MAX_SEC = 4.0      # 도입부 타이틀 카드 최대 노출(초) — "도입 3~4초 룰"
IMAGE_MAX_SEC = 3.0           # 이미지 1컷 최대 노출(초) — 정지 이미지 12초 금지
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
# 신뢰도 낮은/광고성 도메인 제외
NEWS_BLOCK_DOMAINS = ["blog.", "cafe.", "post.naver", "youtube.com"]
