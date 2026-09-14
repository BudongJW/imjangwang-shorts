"""숫자 스탯 콜아웃 — 대본의 핵심 수치를 화면 중앙에 큼직하게 띄운다.

영상 중앙 빈 공간을 채우고, 부동산 뉴스의 '숫자 임팩트'를 시각화한다.
방향(상승/하락)에 따라 색·화살표를 달리해 직관적으로 보이게 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import SHORTS_WIDTH, SHORTS_HEIGHT, VIDEO_DIR
from src.editor.fonts import font_bold

RED = (232, 50, 50)      # 상승
BLUE = (46, 130, 235)    # 하락
YELLOW = (255, 214, 10)  # 중립
DARK = (16, 16, 20)

# 강한 수치(단위 포함)만 콜아웃 대상. 단위를 넓힌다 — 예전 목록에는 천·명·
# 건·원·주·개월·제곱미터가 없어서 "200명", "3천 건", "84제곱미터"가 통째로
# 빠졌다.
_STAT_RE = re.compile(
    r"(\d[\d,\.]*)\s?"
    r"(%|퍼센트|억원|억|만원|만|천|원|배|채|가구|세대|호|명|건|위|조|평|"
    r"제곱미터|㎡|개월|주|년|일|개)")
# 연도는 수치가 아니라 날짜다. "2024년"을 화면 한복판에 210px로 띄우면
# 숫자 임팩트가 아니라 잡음이 된다(2026-09-14 실측에서 2회 잡혔다).
_YEAR_RE = re.compile(r"^(1[89]\d{2}|20\d{2})년$")
# 같은 구절에 여러 수치가 있으면 '센' 쪽을 띄운다. 기간(5년·2주·3개월)은
# 대개 기준일 뿐 임팩트가 아니다 — "지난 5년 평균 대비"의 5년을 화면
# 한복판에 띄워 봐야 시청자에게 남는 게 없다(2026-09-14 실측).
_WEAK_UNITS = ("년", "주", "개월", "일", "개")
_UP = ["오르", "상승", "폭등", "급등", "최고", "신고가", "뛰", "올라", "증가", "늘"]
_DOWN = ["하락", "급락", "폭락", "내리", "줄", "감소", "떨어", "최저", "급감"]


# "2만 3천 가구"처럼 수가 여러 토막으로 이어지는 경우. 첫 조각만 집으면
# "2만"이 화면에 뜨는데, 실제 값은 2만 3천이라 숫자를 틀리게 보여주는 셈이다
# (2026-09-14 검증 프레임 16초 지점).
_CONT_RE = re.compile(
    r"\s?(\d[\d,\.]*\s?(?:%|억원|억|만원|만|천|원|가구|세대|호|명|건|채|평|㎡))")


_TAIL_UNIT_RE = re.compile(r"\s?(?:가구|세대|명|건|채|호|원|평|㎡|개)")


def _extend(phrase: str, m: re.Match) -> str:
    """이어지는 수 조각을 흡수해 온전한 수치로 만든다."""
    out, pos = m.group(0), m.end()
    while True:
        nxt = _CONT_RE.match(phrase, pos)
        if not nxt:
            break
        out += nxt.group(0)
        pos = nxt.end()
    # "2만 3천"처럼 자릿수 단위로 끝났으면 뒤따르는 조수사를 마저 붙인다.
    # 자릿수로 끝날 때만 본다 — 그러지 않으면 "5년 평균"의 '평'까지 붙는다.
    if out.rstrip().endswith(("만", "천", "억", "조")):
        tail = _TAIL_UNIT_RE.match(phrase, pos)
        if tail:
            out += tail.group(0)
    return out.strip()


def is_weak(big: str) -> bool:
    """기간처럼 임팩트가 약한 수치인지."""
    return big.endswith(_WEAK_UNITS)


def pick_stat(phrase: str) -> tuple[str, str] | None:
    """구절에서 대표 수치 1개와 방향(up/down/flat)을 뽑는다. 없으면 None.

    연도는 제외하고, 강한 단위(%·억·가구·명 …)를 기간 단위보다 우선한다.
    """
    best, weak = None, None
    for m in _STAT_RE.finditer(phrase):
        cand = _extend(phrase, m)
        if _YEAR_RE.match(cand.replace(",", "")):
            continue
        if cand.endswith(_WEAK_UNITS):
            weak = weak or cand
            continue
        # %가 가장 날카롭다. 같은 구절에 %와 다른 단위가 같이 있으면 %를 쓴다.
        if best is None or (cand.endswith("%") and not best.endswith("%")):
            best = cand
    cand = best or weak
    if not cand:
        return None
    big = cand.replace("퍼센트", "%").replace("만원", "만").replace("제곱미터", "㎡")
    direction = "flat"
    if any(k in phrase for k in _UP):
        direction = "up"
    elif any(k in phrase for k in _DOWN):
        direction = "down"
    return big, direction


def _rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render_stat_card(big: str, direction: str, out: Path) -> Path:
    """화면 중앙에 수치 콜아웃을 렌더한 전체 투명 PNG."""
    img = Image.new("RGBA", (SHORTS_WIDTH, SHORTS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = {"up": RED, "down": BLUE}.get(direction, YELLOW)

    font = ImageFont.truetype(font_bold(), 210)
    bbox = draw.textbbox((0, 0), big, font=font, stroke_width=10)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    cx, cy = SHORTS_WIDTH // 2, int(SHORTS_HEIGHT * 0.46)
    pad_x, pad_y = 90, 70
    panel = [cx - tw // 2 - pad_x, cy - th // 2 - pad_y,
             cx + tw // 2 + pad_x, cy + th // 2 + pad_y]
    _rounded(draw, panel, 48, (10, 10, 14, 205))
    # 상단 컬러 액센트 바
    _rounded(draw, [panel[0], panel[1], panel[2], panel[1] + 16], 8, color + (255,))

    # 화살표(상승/하락)
    if direction in ("up", "down"):
        ax = panel[2] - 40
        ay = panel[1] - 30
        if direction == "up":
            draw.polygon([(ax, ay - 70), (ax - 55, ay + 20), (ax + 55, ay + 20)], fill=color)
        else:
            draw.polygon([(ax, ay + 90), (ax - 55, ay), (ax + 55, ay)], fill=color)

    # 큰 수치
    draw.text((cx, cy), big, font=font, fill=color + (255,),
              anchor="mm", stroke_width=10, stroke_fill=DARK + (255,))

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
