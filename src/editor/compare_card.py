"""전후 비교 막대 카드 — 대본의 "A에서 B로"를 막대 두 개로 보여 준다.

숫자 콜아웃은 값 하나만 띄운다. "2024년 말 8807가구에서 올해 상반기 말
4383가구로"처럼 바뀐 폭이 이야기의 전부인 문장도 화면에는 "4383가구"
하나만 남았다. 말하는 숫자를 화면이 증명해야 끝까지 본다는 게 쇼츠
레퍼런스들의 공통 의견이고(2026-10-01 조사), 말과 상관없는 스톡 사진이
대부분이던 화면에 내용을 직접 보여 주는 컷이 생긴다.

값과 시점 이름은 모두 대본에서 그대로 가져온다. 대본은 이미 기사와
대조를 마친 문장이므로, 여기서 새로 계산해 띄우는 숫자는 없다(차이도
계산하지 않는다). 막대 높이만 두 값의 비율로 그린다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import SHORTS_WIDTH, SHORTS_HEIGHT
from src.editor.fonts import font_bold, fix_glyphs
from src.editor.stat_callout import _STAT_RE, _YEAR_RE, _extend, RED, BLUE, DARK

GRAY = (150, 154, 166)

# 앞 값 뒤에 와야 하는 말("~에서", "~이던"), 뒤 값 뒤에 와야 하는 말("~로", "~에").
_BOUND_RE = r"(?:\s*(?:이하|이상|미만|초과))?"
_FROM_TAIL_RE = re.compile(_BOUND_RE + r"\s*(?:원\s*)?(?:에서|이던|였던|이었던)")
_TO_TAIL_RE = re.compile(_BOUND_RE + r"\s*(?:원\s*)?(?:으로|로|에|까지)")
# 값 바로 앞의 시점 이름. 없으면 '전'·'후'로 쓴다.
_TIME_RE = re.compile(
    r"((?:\d{4}년|올해|지난해|작년)?\s?(?:상반기|하반기|[1-4]분기)?\s?(?:말|초)?"
    r"|\d{1,2}월|(?:\d{4}년\s?)?\d{1,2}월)\s*(?:기준\s*)?(?:약\s*)?$")


def _unit_class(v: str) -> str | None:
    v = v.replace(" ", "")
    if v.endswith("%"):
        return "pct"
    if re.search(r"(가구|세대|채|호|실|건|명|곳)$", v):
        return "count"
    if re.search(r"(조|억|만|천|원)$", v):
        return "money"
    if v.endswith("세"):
        return "age"
    if re.search(r"(㎡|평)$", v):
        return "area"
    return None


_NUM_TOK_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|천만|만|천)?")
_MULT = {"조": 1e12, "억": 1e8, "천만": 1e7, "만": 1e4, "천": 1e3, None: 1.0}


def to_number(v: str) -> float | None:
    """'12억 3000만 원' → 1.23e9, '2만 4000가구' → 24000, '0.26%' → 0.26."""
    toks = _NUM_TOK_RE.findall(v)
    if not toks:
        return None
    total = 0.0
    big_seen = None
    for num, unit in toks:
        n = float(num.replace(",", ""))
        u = unit or None
        # "5억 6천"은 5억 6천만이다. 억 뒤의 천은 천만으로 읽는다.
        if u == "천" and big_seen == "억":
            u = "천만"
        total += n * _MULT[u]
        if u in ("조", "억"):
            big_seen = u
    return total


@dataclass
class Pair:
    v1: str
    v2: str
    label1: str
    label2: str
    start_pos: int    # 대본 안에서 첫 값이 시작하는 위치(타이밍 계산용)
    end_pos: int      # 대본 안에서 두 번째 값이 끝나는 위치
    up: bool


def _values(sent: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _STAT_RE.finditer(sent):
        cand = _extend(sent, m)
        if _YEAR_RE.match(cand.replace(",", "")):
            continue
        start = sent.find(cand, m.start())
        if start < 0:
            continue
        out.append((start, start + len(cand), cand))
    # _extend가 이어 붙인 뒤 조각이 따로 또 잡힌다("12억 3000만"의 "3000만").
    # 앞 값 안에 들어가는 것은 버린다.
    kept: list[tuple[int, int, str]] = []
    for v in out:
        if kept and v[0] < kept[-1][1]:
            continue
        kept.append(v)
    return kept


def _label(text: str) -> str:
    m = _TIME_RE.search(text.rstrip())
    lab = (m.group(1) if m else "").strip()
    return re.sub(r"\s+", " ", lab)


def find_pairs(script: str) -> list[Pair]:
    """대본에서 같은 단위의 'A에서/이던 … B로/에' 쌍을 찾는다."""
    pairs: list[Pair] = []
    offset = 0
    for sent in re.split(r"(?<=[다요죠])[.!?]+(?!\d)", script or ""):
        base = script.find(sent, offset) if sent else offset
        offset = base + len(sent)
        vals = _values(sent)
        for i, (s1, e1, t1) in enumerate(vals):
            if not _FROM_TAIL_RE.match(sent, e1):
                continue
            c1 = _unit_class(t1)
            if not c1:
                continue
            for s2, e2, t2 in vals[i + 1:]:
                if _unit_class(t2) != c1:
                    continue
                if not _TO_TAIL_RE.match(sent, e2):
                    break
                n1, n2 = to_number(t1), to_number(t2)
                if not n1 or not n2 or n1 == n2:
                    break
                l1 = _label(sent[:s1]) or "전"
                l2 = _label(sent[e1:s2]) or "후"
                pairs.append(Pair(t1, t2, l1, l2, base + s1, base + e2, n2 > n1))
                break
    return pairs


def _short(v: str) -> str:
    """막대 위 숫자. '12억 3000만 원'의 꼬리 '원'은 뗀다(콜아웃과 같은 표기)."""
    v = re.sub(r"\s*원$", "", v.strip())
    return fix_glyphs(v)


def _fit(draw, text: str, size: int, max_w: int):
    while size > 28:
        font = ImageFont.truetype(font_bold(), size)
        bb = draw.textbbox((0, 0), text, font=font, stroke_width=6)
        if bb[2] - bb[0] <= max_w:
            return font
        size -= 4
    return ImageFont.truetype(font_bold(), size)


def render_compare_card(pair: Pair, out: Path, full: bool = False) -> Path:
    """화면 중앙에 막대 두 개짜리 비교 카드를 그린 전체 PNG(full이면 정보 화면 배경)."""
    W, H = SHORTS_WIDTH, SHORTS_HEIGHT
    if full:
        from src.editor.slide_bg import backdrop
        img = backdrop()
    else:
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = RED if pair.up else BLUE

    pw, ph = 860, 820
    cx, cy = W // 2, int(H * 0.46)
    panel = [cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2]
    # 정보 화면이면 바탕이 이미 남색이라 상자는 한 톤 밝은 남색으로 둔다.
    # 반투명으로 칠하면 그 자리만 투명해져 아래 사진이 비친다(불투명 색만 쓴다).
    draw.rounded_rectangle(panel, radius=48,
                           fill=(30, 52, 84, 255) if full else (10, 10, 14, 230))
    draw.rounded_rectangle([panel[0], panel[1], panel[2], panel[1] + 16], radius=8,
                           fill=color + (255,))

    n1, n2 = to_number(pair.v1) or 0, to_number(pair.v2) or 0
    top = max(n1, n2) or 1
    bar_max = 430
    base_y = panel[3] - 150
    bar_w = 230
    xs = (cx - 190, cx + 190)
    for x, n, v, lab, fill in ((xs[0], n1, pair.v1, pair.label1, GRAY),
                               (xs[1], n2, pair.v2, pair.label2, color)):
        h = max(24, int(bar_max * n / top))
        draw.rounded_rectangle([x - bar_w // 2, base_y - h, x + bar_w // 2, base_y],
                               radius=18, fill=fill + (255,))
        val = _short(v)
        vf = _fit(draw, val, 72, 330)
        draw.text((x, base_y - h - 24), val, font=vf, fill=(255, 255, 255, 255),
                  anchor="mb", stroke_width=6, stroke_fill=DARK + (255,))
        lf = _fit(draw, lab, 54, 380)
        draw.text((x, base_y + 30), lab, font=lf, fill=(220, 224, 232, 255), anchor="mt")

    img.save(out)
    return out
