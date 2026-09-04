"""영상용 배경 이미지 수집.

개선안 ②·③에 맞춰 '실제 뉴스 캡처 중심'을 보조할 b-roll 이미지를 모은다.
소스 우선순위: 기사 대표이미지(og:image) → Pexels(키 있을 때) → 그라디언트 생성.
반환 이미지는 모두 세로(1080x1920)로 크롭·리사이즈된 PNG.
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter

from config.settings import (
    SHORTS_WIDTH,
    SHORTS_HEIGHT,
    VIDEO_DIR,
    PEXELS_API_KEY,
)
from src.utils.logger import setup_logger

log = setup_logger("images")

UA = {"User-Agent": "Mozilla/5.0"}
# 부동산 톤 그라디언트 팔레트(어두운 남색~차분한 톤)
GRADIENTS = [
    ((16, 24, 48), (40, 58, 96)),
    ((28, 20, 20), (70, 42, 42)),
    ((18, 34, 40), (34, 70, 78)),
    ((30, 28, 44), (66, 58, 96)),
]


def _to_portrait(im: Image.Image) -> Image.Image:
    """이미지를 1080x1920로 커버 크롭."""
    im = im.convert("RGB")
    tw, th = SHORTS_WIDTH, SHORTS_HEIGHT
    ratio = max(tw / im.width, th / im.height)
    nw, nh = int(im.width * ratio), int(im.height * ratio)
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def _download(url: str) -> Image.Image | None:
    try:
        r = requests.get(url, headers=UA, timeout=12)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content))
    except Exception as e:
        log.info(f"  이미지 다운로드 실패: {e}")
        return None


def _draw_skyline(base: Image.Image, idx: int) -> None:
    """하단에 야경 도시 스카이라인(실루엣 건물 + 창문 불빛)을 그린다."""
    rnd = random.Random(idx * 7 + 13)
    draw = ImageDraw.Draw(base, "RGBA")
    W, H = base.size
    x = -20
    while x < W + 20:
        bw = rnd.randint(70, 150)
        bh = rnd.randint(int(H * 0.14), int(H * 0.38))
        top = H - bh
        shade = rnd.randint(6, 20)
        draw.rectangle([x, top, x + bw, H], fill=(shade, shade, shade + 10, 240))
        # 창문 불빛
        for wy in range(top + 20, H - 24, 36):
            for wx in range(x + 14, x + bw - 12, 28):
                if rnd.random() < 0.26:
                    c = rnd.choice([(255, 214, 120), (255, 236, 180), (170, 195, 255)])
                    a = rnd.randint(120, 210)
                    draw.rectangle([wx, wy, wx + 9, wy + 13], fill=c + (a,))
        x += bw + rnd.randint(-6, 12)


def _gradient(idx: int) -> Image.Image:
    c1, c2 = GRADIENTS[idx % len(GRADIENTS)]
    base = Image.new("RGB", (SHORTS_WIDTH, SHORTS_HEIGHT))
    top = Image.new("RGB", (1, SHORTS_HEIGHT))
    for y in range(SHORTS_HEIGHT):
        t = y / SHORTS_HEIGHT
        top.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    base = top.resize((SHORTS_WIDTH, SHORTS_HEIGHT))
    _draw_skyline(base, idx)   # 도시 스카이라인 실루엣 + 창문 불빛
    # 은은한 비네트
    v = Image.new("L", (SHORTS_WIDTH, SHORTS_HEIGHT), 0)
    dv = ImageDraw.Draw(v)
    dv.ellipse([-200, -300, SHORTS_WIDTH + 200, SHORTS_HEIGHT + 300], fill=60)
    v = v.filter(ImageFilter.GaussianBlur(180))
    base = Image.composite(base, Image.new("RGB", base.size, (0, 0, 0)), v.point(lambda x: 255 - x))
    return base


def _pexels(query: str, n: int) -> list[Image.Image]:
    if not PEXELS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": n, "orientation": "portrait", "locale": "ko-KR"},
            timeout=12,
        )
        r.raise_for_status()
        imgs = []
        for photo in r.json().get("photos", []):
            im = _download(photo["src"]["large2x"])
            if im:
                imgs.append(im)
        return imgs
    except Exception as e:
        log.info(f"  Pexels 실패: {e}")
        return []


# 검색어를 고정하면 Pexels가 매번 같은 사진군을 준다. 날짜별로 돌려
# 배경이 겹치지 않게 한다(썸네일 구도·얼굴 크롭 회전과 같은 방식).
PEXELS_QUERIES = (
    "seoul apartment building",
    "korean city skyline night",
    "apartment construction site",
    "seoul street rain",
    "high rise apartment window",
    "moving boxes empty room",
    "real estate agency window",
    "han river apartment aerial",
)


def _today_query() -> str:
    from datetime import datetime, timezone, timedelta
    d = datetime.now(timezone(timedelta(hours=9))).date().toordinal()
    return PEXELS_QUERIES[d % len(PEXELS_QUERIES)]


def collect_backgrounds(article_image_url: str = "", need: int = 3,
                        query: str = "") -> list[Path]:
    """b-roll 배경 이미지 need개를 확보해 파일로 저장하고 경로 리스트 반환."""
    query = query or _today_query()
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    pool: list[Image.Image] = []

    # 뉴스 대표이미지가 로고/파비콘이면(구글뉴스 등) 배경으로 부적합 → 스킵
    logo_like = any(k in article_image_url.lower() for k in ("google", "gstatic", "favicon", "logo"))
    if article_image_url and not logo_like:
        im = _download(article_image_url)
        # 너무 작은 이미지(아이콘)는 배제
        if im and min(im.size) >= 400:
            pool.append(im)

    if len(pool) < need:
        pool += _pexels(query, need - len(pool))

    # 폴백 그라디언트도 날짜를 시작점으로 — 소스가 전부 실패한 날에도
    # 최소한 어제와 같은 색은 피한다.
    from datetime import datetime, timezone, timedelta
    idx = datetime.now(timezone(timedelta(hours=9))).date().toordinal()
    while len(pool) < need:
        pool.append(_gradient(idx))
        idx += 1

    paths = []
    for i, im in enumerate(pool[:need]):
        p = VIDEO_DIR / f"bg_{i:02d}.png"
        _to_portrait(im).save(p)
        paths.append(p)
    log.info(f"  배경 이미지 {len(paths)}개 확보")
    return paths
