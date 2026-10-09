"""정보 화면 배경 — 숫자·비교 카드를 사진 위가 아니라 전체 화면으로 띄울 때 쓴다.

스톡 사진은 말하는 내용과 상관이 없고, 서울·한국 사진만 쓰게 막자 같은
사진이 반복됐다(10-09). 숫자가 나오는 구간만큼은 그 숫자를 보여 주는 화면이
배경이 되게 한다. 실험 bg_mode의 graphic 팔에서만 쓴다(src/experiments.py).

색은 남색 바탕에 노란 강조 하나로 둔다. 숫자 카드의 색 규칙(오름 빨강,
내림 파랑, 그 밖 노랑)은 그대로다.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw

from config.settings import SHORTS_WIDTH, SHORTS_HEIGHT

NAVY_TOP = (14, 26, 43)
NAVY_BOTTOM = (24, 44, 72)
GRID = (255, 255, 255, 14)
GRID_STEP = 90


@lru_cache(maxsize=1)
def _backdrop() -> Image.Image:
    W, H = SHORTS_WIDTH, SHORTS_HEIGHT
    img = Image.new("RGBA", (W, H), NAVY_TOP + (255,))
    draw = ImageDraw.Draw(img)
    # 세로 그라데이션
    for y in range(H):
        t = y / (H - 1)
        c = tuple(int(a + (b - a) * t) for a, b in zip(NAVY_TOP, NAVY_BOTTOM))
        draw.line([(0, y), (W, y)], fill=c + (255,))
    # 그래프 종이 같은 옅은 격자. 정보 화면이라는 신호다.
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, W, GRID_STEP):
        gd.line([(x, 0), (x, H)], fill=GRID, width=2)
    for y in range(0, H, GRID_STEP):
        gd.line([(0, y), (W, y)], fill=GRID, width=2)
    return Image.alpha_composite(img, grid)


def backdrop() -> Image.Image:
    """전체 화면 불투명 배경 한 장(복사본)."""
    return _backdrop().copy()
