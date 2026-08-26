"""타이틀 카드(썸네일 겸 도입 훅) 렌더.

분석한 상위 영상 스타일 재현:
  - 어둡게 깐 배경(아파트/그라디언트)
  - 빨강/검정 박스 위 흰색 헤드라인 + 노란색 강조어
  - 빨간 상승 화살표(상승 서사)
개선안 ①: 이 카드는 도입 3~4초만 노출하도록 composer가 길이를 제한한다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from config.settings import SHORTS_WIDTH, SHORTS_HEIGHT, VIDEO_DIR
from src.editor.fonts import font_bold

RED = (206, 32, 32)
YELLOW = (255, 214, 10)
WHITE = (245, 245, 245)
DARK = (18, 18, 20)

# 영상마다 액센트 색을 변주해 '찍어낸 템플릿' 느낌을 줄인다(YouTube inauthentic 정책 대응).
ACCENTS = [(206, 32, 32), (230, 150, 20), (30, 158, 148), (44, 96, 220), (168, 60, 190)]


def pick_accent() -> tuple:
    import random
    return random.choice(ACCENTS)


def _darken(im: Image.Image, factor: float = 0.5) -> Image.Image:
    im = im.convert("RGB").resize((SHORTS_WIDTH, SHORTS_HEIGHT), Image.LANCZOS)
    return ImageEnhance.Brightness(im).enhance(factor)


def _rising_arrow(draw: ImageDraw.ImageDraw, y0: int) -> None:
    """빨간 상승 지그재그 화살표를 화면 중하단에 그린다."""
    w = SHORTS_WIDTH
    pts = [
        (int(w * 0.12), y0 + 260),
        (int(w * 0.34), y0 + 120),
        (int(w * 0.52), y0 + 200),
        (int(w * 0.86), y0 - 40),
    ]
    draw.line(pts, fill=RED, width=22, joint="curve")
    # 화살촉
    tip = pts[-1]
    draw.polygon(
        [(tip[0] + 8, tip[1] - 8), (tip[0] - 70, tip[1] - 34), (tip[0] - 34, tip[1] + 62)],
        fill=RED,
    )


def _paste_face(base: Image.Image, face: Path) -> None:
    """정치인 얼굴을 우측 하단에 크게 부각(좌측 페이드로 배경에 블렌드)."""
    try:
        fim = Image.open(face).convert("RGB")
    except Exception:
        return
    fh = int(SHORTS_HEIGHT * 0.64)
    fw = int(fim.width * fh / fim.height)
    fim = fim.resize((fw, fh), Image.LANCZOS)
    fx = SHORTS_WIDTH - fw + int(fw * 0.04)     # 우측 정렬
    fy = SHORTS_HEIGHT - fh                       # 바닥 고정
    # 좌측 28% 페이드 마스크(직사각 경계 완화)
    mask = Image.new("L", (fw, fh), 0)
    md = ImageDraw.Draw(mask)
    fade = max(1, int(fw * 0.28))
    for x in range(fw):
        md.line([(x, 0), (x, fh)], fill=255 if x >= fade else int(255 * x / fade))
    base.paste(fim, (fx, fy), mask)


def render_title_card(headline: list[str], hook_word: str,
                      background: Path | None = None,
                      out_name: str = "title_card",
                      accent: tuple = RED,
                      face: Path | None = None) -> Path:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    if background and Path(background).exists():
        base = _darken(Image.open(background), 0.42)
    else:
        base = Image.new("RGB", (SHORTS_WIDTH, SHORTS_HEIGHT), (22, 30, 54))

    # 정치인 얼굴 부각(있으면 화살표 대신 얼굴을 초점으로)
    if face and Path(face).exists():
        _paste_face(base, face)

    draw = ImageDraw.Draw(base, "RGBA")

    # 얼굴이 없을 때만 상승 화살표
    if not (face and Path(face).exists()):
        _rising_arrow(draw, y0=int(SHORTS_HEIGHT * 0.52))

    # 헤드라인 박스
    font = ImageFont.truetype(font_bold(), 96)
    line_h = 120
    total_h = len(headline) * line_h
    top = int(SHORTS_HEIGHT * 0.16)

    # 반투명 검정 밴드
    pad = 40
    band_top = top - pad
    band_bottom = top + total_h + pad
    draw.rectangle([40, band_top, SHORTS_WIDTH - 40, band_bottom], fill=(10, 10, 12, 205))
    # 좌측 액센트 바(영상마다 색 변주)
    draw.rectangle([40, band_top, 64, band_bottom], fill=accent)

    y = top
    for line in headline:
        # 강조어는 노란색, 나머지는 흰색. 줄 단위 중앙정렬.
        parts = _split_hook(line, hook_word)
        widths = [draw.textlength(t, font=font) for t, _ in parts]
        x = (SHORTS_WIDTH - sum(widths)) // 2
        for (txt, is_hook), wdt in zip(parts, widths):
            # 가독성 위한 외곽선
            draw.text((x, y), txt, font=font, fill=(YELLOW if is_hook else WHITE),
                      stroke_width=6, stroke_fill=DARK)
            x += wdt
        y += line_h

    out = VIDEO_DIR / f"{out_name}.png"
    base.save(out)
    return out


def render_headline_banner(headline: list[str], hook_word: str,
                           out_name: str = "headline_banner",
                           accent: tuple = RED) -> Path:
    """영상 전체에 상시 오버레이할 상단 헤드라인 배너(투명 PNG).

    타이틀카드 이후 모든 프레임 상단에 헤드라인을 노출해, YouTube가 어떤 프레임을
    Shorts 그리드 썸네일로 자동 선택하든 '헤드라인이 박힌 썸네일'처럼 보이게 한다.
    """
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (SHORTS_WIDTH, SHORTS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    lines = headline[:2]  # 배너는 최대 2줄로 압축
    font = ImageFont.truetype(font_bold(), 74)
    line_h = 92
    top = 60
    band_h = len(lines) * line_h + 44
    # 반투명 검정 밴드 + 빨강 좌측 액센트
    draw.rectangle([0, top, SHORTS_WIDTH, top + band_h], fill=(10, 10, 12, 210))
    draw.rectangle([0, top, 20, top + band_h], fill=accent)

    y = top + 22
    for line in lines:
        parts = _split_hook(line, hook_word)
        widths = [draw.textlength(t, font=font) for t, _ in parts]
        x = (SHORTS_WIDTH - sum(widths)) // 2
        for (txt, is_hook), wdt in zip(parts, widths):
            draw.text((x, y), txt, font=font, fill=(YELLOW if is_hook else WHITE),
                      stroke_width=4, stroke_fill=DARK)
            x += wdt
        y += line_h

    out = VIDEO_DIR / f"{out_name}.png"
    img.save(out)
    return out


def _split_hook(line: str, hook_word: str) -> list[tuple[str, bool]]:
    """줄에서 hook_word 부분만 강조 플래그로 분리."""
    if hook_word and hook_word in line:
        i = line.index(hook_word)
        out = []
        if line[:i]:
            out.append((line[:i], False))
        out.append((hook_word, True))
        if line[i + len(hook_word):]:
            out.append((line[i + len(hook_word):], False))
        return out
    return [(line, False)]
