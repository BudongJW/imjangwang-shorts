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

# ── LLM (대본 생성) ─────────────────────────────────────
# Gemini 키는 콤마로 여러 개 넣으면 라운드로빈 로테이션.
GEMINI_API_KEYS = [
    k.strip() for k in os.getenv("GEMINI_API_KEYS", os.getenv("GEMINI_API_KEY", "")).split(",") if k.strip()
]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

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
    "부동산 집값",
    "아파트 전세",
    "부동산 정책 규제",
    "재건축 재개발",
    "청약 분양",
]
NEWS_MAX_CANDIDATES = 25
# 신뢰도 낮은/광고성 도메인 제외
NEWS_BLOCK_DOMAINS = ["blog.", "cafe.", "post.naver", "youtube.com"]
