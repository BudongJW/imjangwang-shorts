"""Gemini 이미지 생성 — 썸네일/타이틀카드 배경용 AI 이미지.

무료티어는 분당·일일 한도가 낮으므로 하루 1~2장만 생성하고, 실패(429 등) 시
None을 반환해 기존 배경 로직(뉴스 이미지/Pexels/그라디언트)으로 폴백한다.
키는 GEMINI_IMAGE_KEY(신형 'AQ.' 포맷) 환경변수에서 읽는다.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import requests

from config.settings import VIDEO_DIR, SHORTS_WIDTH, SHORTS_HEIGHT
from src.utils.logger import setup_logger

log = setup_logger("ai_image")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")


def _keys() -> list[str]:
    """GEMINI_IMAGE_KEY(콤마 다중)을 리스트로. 여러 키를 로테이션해 일일 한도 확장."""
    return [k.strip() for k in os.getenv("GEMINI_IMAGE_KEY", "").split(",") if k.strip()]


# 장면을 고정하면 매일 같은 그림이 나온다. 실제로 "골든아워 고층 아파트"만
# 반복돼 며칠째 썸네일이 똑같아 보였다. 구도(4종)·액센트(5종)와 같은 방식으로
# 날짜 기반 회전시킨다. 서로 시간대·날씨·피사체가 겹치지 않게 골랐다.
SCENES = (
    "golden-hour skyline of dense high-rise apartment complexes, warm amber light, "
    "long shadows, shot from a rooftop",
    "rainy night street below apartment towers, wet asphalt reflecting window lights, "
    "cold blue and neon tones, shallow depth of field",
    "foggy grey dawn over an older low-rise apartment estate, muted desaturated palette, "
    "bare trees, quiet empty playground",
    "construction site at dusk — tower cranes and concrete frames of unfinished "
    "apartments, silhouettes against a deep orange sky",
    "winter snowfall over a Seoul apartment district, overcast white sky, "
    "snow on rooftops and parked cars, cold desaturated tones",
    "aerial drone view of the Han river at blue hour with apartment blocks lining "
    "both banks, city lights just switching on",
    "narrow old alley of low-rise villas and semi-basement windows, harsh midday sun, "
    "cramped and worn textures",
    "empty ground-floor commercial strip under an apartment building, shutters down, "
    "flat overcast afternoon light, deserted",
)


def _today_ord() -> int:
    """KST 기준 날짜 일련번호 — 장면 회전의 기준."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9))).date().toordinal()


def pick_scene(day_ord: int | None = None) -> str:
    d = day_ord if day_ord is not None else _today_ord()
    return SCENES[d % len(SCENES)]


def _prompt(headline: str, day_ord: int | None = None) -> str:
    topic = (headline or "한국 부동산").replace("\n", " ")
    return (
        "Create a photorealistic, cinematic vertical (9:16) background image for a "
        f"Korean real-estate news short about: '{topic}'. "
        f"Scene: {pick_scene(day_ord)}. "
        "Korean urban setting. Ultra-detailed, professional news thumbnail look. "
        "ABSOLUTELY NO text, letters, numbers, logos or watermarks."
    )


def _try_key(key: str, prompt: str, out: Path, model: str) -> Path | None:
    try:
        r = requests.post(
            API.format(model=model) + f"?key={key}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=120,
        )
        if r.status_code == 429:
            return None  # 한도 소진 → 다음 키로 로테이션
        if r.status_code != 200:
            log.info(f"  AI 이미지 HTTP {r.status_code} (…{key[-4:]})")
            return None
        for p in r.json()["candidates"][0]["content"]["parts"]:
            data = p.get("inlineData") or p.get("inline_data")
            if data and data.get("data"):
                VIDEO_DIR.mkdir(parents=True, exist_ok=True)
                out.write_bytes(base64.b64decode(data["data"]))
                _to_portrait(out)
                return out
        return None
    except Exception as e:
        log.info(f"  AI 이미지 예외(…{key[-4:]}): {e}")
        return None


def generate_background(headline: str, out_name: str = "ai_bg",
                        model: str = DEFAULT_MODEL) -> Path | None:
    """헤드라인 기반 배경 이미지를 1장 생성.

    여러 키를 순차 로테이션하여 한도 남은 키로 생성한다.
    모두 실패(429/오류)하면 None을 반환해 기존 배경 로직으로 폴백한다.
    """
    keys = _keys()
    if not keys:
        return None
    prompt = _prompt(headline)
    out = VIDEO_DIR / f"{out_name}.png"
    for key in keys:
        got = _try_key(key, prompt, out, model)
        if got:
            log.info(f"  AI 썸네일 이미지 생성 성공 (…{key[-4:]})")
            return got
    log.info("  AI 이미지: 모든 키 한도 소진 → 폴백")
    return None


def _to_portrait(path: Path) -> None:
    """생성 이미지가 정확히 1080x1920가 아니면 커버 크롭."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if im.size == (SHORTS_WIDTH, SHORTS_HEIGHT):
            return
        ratio = max(SHORTS_WIDTH / im.width, SHORTS_HEIGHT / im.height)
        nw, nh = int(im.width * ratio), int(im.height * ratio)
        im = im.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - SHORTS_WIDTH) // 2, (nh - SHORTS_HEIGHT) // 2
        im.crop((left, top, left + SHORTS_WIDTH, top + SHORTS_HEIGHT)).save(path)
    except Exception:
        pass
