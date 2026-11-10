"""타이틀카드(썸네일): 화살표 방향이 헤드라인과 맞는지."""

import pytest
from PIL import Image

from src.editor import title_card as tc


@pytest.mark.parametrize("headline,hook,want", [
    (["서울 빌라 전월세", "집계 이래 최소"], "최소", "down"),          # 11-10: 상승 화살표가 붙었다
    (["서울 분양전망 91.4", "올해 들어 최저"], "91.4", "down"),        # 11-07
    (["강남 집값 하락률", "기관마다 4.8배 차이"], "4.8배", "down"),    # 11-08
    (["서울 외곽 전셋값 6.9%", "매매가보다 더 올랐다"], "6.9%", "up"),
    (["자산 상하위 집값 격차", "290.1배, 2017년 이래 최대"], "290.1배", "up"),
    (["청년 전세대출 보증", "만 39세까지 확대"], "만 39세", ""),       # 방향이 없는 소식
    (["거래 줄었는데 신고가", "입주권·분양권 껑충"], "", ""),          # 둘 다 있으면 안 그린다
    (["집값 폭락론 10년", "왜 다 빗나갔나"], "폭락론", ""),            # 전망을 가리키는 말
    (["안 팔린 아파트", "LH가 최대 92%에 산다"], "92%", ""),           # '최대'가 상한일 때
])
def test_headline_direction(headline, hook, want):
    assert tc.headline_direction(headline, hook) == want


def _arrow_colors(path):
    im = Image.open(path).convert("RGB")
    px = {color for _, color in im.resize((108, 192), Image.NEAREST).getcolors(108 * 192)}
    return tc.RED in px, tc.BLUE in px


@pytest.mark.parametrize("layout", tc.LAYOUTS, ids=lambda x: x.name)
def test_arrow_matches_direction_in_every_layout(layout, tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "VIDEO_DIR", tmp_path)
    accent = (30, 158, 148)          # 화살표 색과 겹치지 않는 액센트
    down = tc.render_title_card(["서울 빌라 전월세", "집계 이래 최소"], "최소",
                                out_name="down", accent=accent, layout=layout)
    up = tc.render_title_card(["경기 집값", "4년 11개월 만에 최대 상승"], "최대",
                              out_name="up", accent=accent, layout=layout)
    flat = tc.render_title_card(["청년 전세대출 보증", "만 39세까지 확대"], "만 39세",
                                out_name="flat", accent=accent, layout=layout)
    assert _arrow_colors(down) == (False, True)
    assert _arrow_colors(up) == (True, False)
    assert _arrow_colors(flat) == (False, False)
    assert Image.open(down).size == (1080, 1920)


def test_banner_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "VIDEO_DIR", tmp_path)
    out = tc.render_headline_banner(["서울 빌라 전월세", "집계 이래 최소"], "최소")
    assert Image.open(out).size == (1080, 1920)


def test_layout_and_accent_rotate_by_day():
    assert {tc.pick_layout(d).name for d in range(4)} == {x.name for x in tc.LAYOUTS}
    assert tc.layout_by_name("full-right").name == "full-right"
    assert tc.layout_by_name("nope") is None
    assert len({(tc.pick_layout(d).name, tc.pick_accent(d)) for d in range(20)}) == 20
