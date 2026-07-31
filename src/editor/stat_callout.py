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

# 강한 수치(단위 포함)만 콜아웃 대상
_STAT_RE = re.compile(r"(\d[\d,\.]*)\s?(%|퍼센트|억|만원|만|배|채|년|가구|위|조|평)")
_UP = ["오르", "상승", "폭등", "급등", "최고", "신고가", "뛰", "올라", "증가", "늘"]
_DOWN = ["하락", "급락", "폭락", "내리", "줄", "감소", "떨어", "최저", "급감"]


def pick_stat(phrase: str) -> tuple[str, str] | None:
    """구절에서 대표 수치 1개와 방향(up/down/flat)을 뽑는다. 없으면 None."""
    m = _STAT_RE.search(phrase)
    if not m:
        return None
    big = (m.group(1) + m.group(2)).replace("퍼센트", "%").replace("만원", "만")
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
