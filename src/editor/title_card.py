"""타이틀 카드(썸네일 겸 도입 훅) 렌더.

분석한 상위 영상 스타일 재현:
  - 어둡게 깐 배경(아파트/그라디언트)
  - 빨강/검정 박스 위 흰색 헤드라인 + 노란색 강조어
  - 빨간 상승 화살표(상승 서사)
개선안 ①: 이 카드는 도입 3~4초만 노출하도록 composer가 길이를 제한한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


@dataclass(frozen=True)
class _Layout:
    """타이틀카드(=썸네일) 구도 1종. 밴드 위치·정렬·얼굴 방향/크기를 함께 바꾼다."""
    name: str
    band_y: float       # 헤드라인 밴드 상단 (높이 비율)
    band_x0: float      # 밴드 좌/우 끝 (너비 비율)
    band_x1: float
    align: str          # center | left | right
    face_side: str      # right | left (left면 좌우 반전해 페이드가 안쪽을 향함)
    face_h: float       # 얼굴 높이 (높이 비율)
    band_style: str     # dark(반투명 검정) | accent(액센트 색 밴드)
    face_w: float       # 얼굴 폭 상한 (너비 비율) — 배경이 보이도록 제한
    face_crop: str      # full(원본 그대로) | head(상단 위주로 잘라 클로즈업)


# 매일 다른 그림이 나오도록 구도를 4종으로 나눠 회전시킨다.
# 밴드 폭은 전폭 고정(좁히면 오토핏이 폰트를 줄여 그리드에서 안 읽힘).
# 변주는 세로 위치 · 정렬 · 밴드 스타일 · 얼굴 좌우/크기로 준다.
# 얼굴 폭 상한(face_w)이 없던 때는 원본 비율(689x920)이 그대로 커져 프레임 폭의
# 77~98%를 얼굴이 덮었다. 배경이 보이지 않으니 배경을 바꿔도 썸네일이 똑같아
# 보였다. 폭을 42~58%로 묶고 크롭 방식(전신/클로즈업)까지 갈라 놓는다.
LAYOUTS = (
    _Layout("top-center",  0.14, 0.03, 0.97, "center", "right", 0.70, "dark",   0.62, "full"),
    _Layout("bottom-left", 0.58, 0.03, 0.97, "center", "left",  0.60, "accent", 0.54, "head"),
    _Layout("mid-left",    0.38, 0.03, 0.97, "left",   "right", 0.76, "none",   0.68, "full"),
    _Layout("top-right",   0.14, 0.03, 0.97, "right",  "left",  0.56, "accent", 0.50, "head"),
)


def _today_ord() -> int:
    """KST 기준 날짜 일련번호(구도·색 회전의 기준)."""
    return datetime.now(timezone(timedelta(hours=9))).date().toordinal()


def pick_layout(day_ord: int | None = None) -> _Layout:
    return LAYOUTS[(day_ord if day_ord is not None else _today_ord()) % len(LAYOUTS)]


def pick_face(day_ord: int | None = None):
    """assets/faces/ 안의 사진을 날짜별로 돌려 쓴다.

    사진이 한 장뿐이면 매일 같은 얼굴이 나온다 — 며칠째 썸네일이 똑같아
    보인 원인 중 하나다. 폴더에 파일을 더 넣으면 자동으로 회전한다.
    """
    from config.settings import FACES_DIR, FACE_EXTS, POLITICIAN_FACE
    if not FACES_DIR.exists():
        return POLITICIAN_FACE if POLITICIAN_FACE.exists() else None
    faces = sorted(p for p in FACES_DIR.iterdir()
                   if p.suffix.lower() in FACE_EXTS and p.is_file())
    if not faces:
        return POLITICIAN_FACE if POLITICIAN_FACE.exists() else None
    d = day_ord if day_ord is not None else _today_ord()
    return faces[d % len(faces)]


def pick_accent(day_ord: int | None = None) -> tuple:
    """무작위 대신 날짜 기반 회전 — 구도(4)×색(5)이 20일간 같은 조합 없이 돈다."""
    d = day_ord if day_ord is not None else _today_ord()
    return ACCENTS[(d // len(LAYOUTS)) % len(ACCENTS)]


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


def _paste_face(base: Image.Image, face: Path, side: str = "right",
                height_ratio: float = 0.62, width_ratio: float = 0.52,
                crop: str = "full") -> None:
    """정치인 얼굴을 좌/우 바닥에 부각(안쪽 경계는 페이드로 배경에 블렌드).

    side="left"면 이미지를 좌우 반전해 시선이 화면 안쪽을 향하게 하고,
    페이드도 오른쪽(안쪽) 경계에 준다.
    """
    try:
        fim = Image.open(face).convert("RGB")
    except Exception:
        return
    if crop == "head":
        # 상단 62%만 남겨 머리·얼굴 위주 클로즈업 — 같은 사진도 다른 그림이 된다
        fim = fim.crop((0, 0, fim.width, int(fim.height * 0.62)))
    fh = int(SHORTS_HEIGHT * height_ratio)
    fw = int(fim.width * fh / fim.height)
    # 폭 상한: 얼굴이 프레임을 덮어 배경을 가리지 않게 한다
    cap = int(SHORTS_WIDTH * width_ratio)
    if fw > cap:
        fh = int(fh * cap / fw)
        fw = cap
    fim = fim.resize((fw, fh), Image.LANCZOS)
    if side == "left":
        fim = fim.transpose(Image.FLIP_LEFT_RIGHT)
        fx = -int(fw * 0.04)
    else:
        fx = SHORTS_WIDTH - fw + int(fw * 0.04)
    fy = SHORTS_HEIGHT - fh                       # 바닥 고정

    # 안쪽 28% 페이드 마스크(직사각 경계 완화)
    mask = Image.new("L", (fw, fh), 0)
    md = ImageDraw.Draw(mask)
    fade = max(1, int(fw * 0.28))
    for x in range(fw):
        # right 배치는 좌측이, left 배치는 우측이 '안쪽'
        d = x if side == "right" else fw - 1 - x
        md.line([(x, 0), (x, fh)], fill=255 if d >= fade else int(255 * d / fade))
    base.paste(fim, (fx, fy), mask)


def _fit_font(draw: ImageDraw.ImageDraw, lines: list[str], max_w: int,
              start: int = 96, floor: int = 72) -> ImageFont.FreeTypeFont:
    """가장 긴 줄이 밴드 폭에 들어갈 때까지 폰트를 줄인다(좁은 구도에서 잘림 방지)."""
    size = start
    while size > floor:
        f = ImageFont.truetype(font_bold(), size)
        if max((draw.textlength(l, font=f) for l in lines), default=0) <= max_w:
            return f
        size -= 4
    return ImageFont.truetype(font_bold(), floor)


def render_title_card(headline: list[str], hook_word: str,
                      background: Path | None = None,
                      out_name: str = "title_card",
                      accent: tuple = RED,
                      face: Path | None = None,
                      layout: "_Layout | None" = None) -> Path:
    """썸네일 겸 도입 훅 카드. layout 미지정 시 날짜 기반으로 구도가 회전한다."""
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    lay = layout or pick_layout()
    has_face = bool(face and Path(face).exists())

    if background and Path(background).exists():
        base = _darken(Image.open(background), 0.42)
    else:
        base = Image.new("RGB", (SHORTS_WIDTH, SHORTS_HEIGHT), (22, 30, 54))

    # 정치인 얼굴 부각(있으면 화살표 대신 얼굴을 초점으로) — 구도별 좌/우·크기 변주
    if has_face:
        _paste_face(base, face, side=lay.face_side, height_ratio=lay.face_h,
                    width_ratio=lay.face_w, crop=lay.face_crop)

    draw = ImageDraw.Draw(base, "RGBA")

    # 얼굴이 없을 때만 상승 화살표
    if not has_face:
        _rising_arrow(draw, y0=int(SHORTS_HEIGHT * 0.52))

    # 헤드라인 밴드 — 구도별 위치·폭·정렬
    x0 = int(SHORTS_WIDTH * lay.band_x0)
    x1 = int(SHORTS_WIDTH * lay.band_x1)
    pad = 40
    font = _fit_font(draw, list(headline), max_w=(x1 - x0) - pad * 3)
    line_h = int(font.size * 1.25)
    total_h = len(headline) * line_h
    top = int(SHORTS_HEIGHT * lay.band_y)
    band_top, band_bottom = top - pad, top + total_h + pad

    if lay.band_style == "accent":
        draw.rectangle([x0, band_top, x1, band_bottom], fill=(*accent, 210))
        draw.rectangle([x0, band_bottom - 12, x1, band_bottom], fill=(10, 10, 12, 230))
    elif lay.band_style == "none":
        pass                      # 밴드 없이 두꺼운 외곽선 글자만으로 대비 확보
    else:
        draw.rectangle([x0, band_top, x1, band_bottom], fill=(10, 10, 12, 205))
        draw.rectangle([x0, band_top, x0 + 24, band_bottom], fill=accent)

    y = top
    for line in headline:
        # 강조어는 노란색, 나머지는 흰색.
        parts = _split_hook(line, hook_word)
        widths = [draw.textlength(t, font=font) for t, _ in parts]
        total_w = sum(widths)
        if lay.align == "left":
            x = x0 + pad * 2
        elif lay.align == "right":
            x = x1 - pad * 2 - total_w
        else:
            x = x0 + ((x1 - x0) - total_w) // 2
        for (txt, is_hook), wdt in zip(parts, widths):
            # 가독성 위한 외곽선
            draw.text((x, y), txt, font=font, fill=(YELLOW if is_hook else WHITE),
                      stroke_width=(14 if lay.band_style == "none" else 6), stroke_fill=DARK)
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
