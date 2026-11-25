"""움직임 묶음: 자막 어절 강조, 컷 움직임, 기사 화면 전환, 카드 등장."""

import re
from pathlib import Path

import pytest
from PIL import Image

from src.editor import composer as c
from src.editor import motion as mo

TITLE = Path("title_card.png")
ARTICLE = Path("article.png")
BGS = [Path(f"bg_{i}.png") for i in range(6)]


def plain(text: str) -> str:
    """ASS 태그를 떼어 화면에 찍히는 글자만 남긴다."""
    return re.sub(r"\{[^}]*\}", "", text)


# ── 자막 ────────────────────────────────────────────────


def test_word_steps_cover_the_phrase_without_gaps():
    ph = "서울 오피스텔 매매가격이"
    steps = c._word_steps(ph, 1.0, 2.5)
    assert len(steps) == 3
    assert (steps[0][0], steps[-1][1]) == (0, len(ph))
    assert (steps[0][2], steps[-1][3]) == (1.0, 2.5)
    for (_, b0, _, e0), (a1, _, s1, _) in zip(steps, steps[1:]):
        assert e0 == pytest.approx(s1)          # 어절 사이에 자막이 비는 틈이 없다
        assert b0 <= a1
    assert all(e - s >= c.MIN_WORD_SEC - 1e-9 for _, _, s, e in steps)


def test_word_steps_do_not_flicker_on_short_phrases():
    assert c._word_steps("올랐습니다.", 0.0, 1.0) == [(0, 6, 0.0, 1.0)]     # 어절 하나
    assert len(c._word_steps("전세는 0.12%, 월세는", 0.0, 0.3)) == 1         # 나눌 시간이 없다
    # 짧은 어절은 다음 어절과 묶여 한 번에 밝아진다.
    steps = c._word_steps("더 큰 폭으로 올랐습니다", 0.0, 1.0)
    assert all(e - s >= c.MIN_WORD_SEC - 1e-9 for _, _, s, e in steps)
    assert steps[-1][1] == len("더 큰 폭으로 올랐습니다")


def test_step_text_lights_one_word_and_keeps_numbers_yellow():
    ph = "월세는 3.53% 상승했고"
    mask = c._hl_mask(ph)
    first = c._step_text(ph, mask, 0, 3, pop=True)
    assert first == c._POP + c._HL_OFF + "월세는 " + c._HL_ON + "3.53% " + c._DIM + "상승했고"
    last = c._step_text(ph, mask, 10, len(ph))
    assert last == c._DIM + "월세는 " + c._HL_ON + "3.53% " + c._HL_OFF + "상승했고"
    assert plain(first) == plain(last) == ph    # 글자는 한 자도 바뀌지 않는다


def test_caption_ass_motion_keeps_the_text_and_timeline(tmp_path):
    script = "서울 오피스텔 매매가격이 22개월 만에 내렸습니다. 반면 전세는 0.12% 올랐습니다."

    def events(motion):
        out = c.build_caption_ass(script, 8.0, tmp_path / f"m{int(motion)}.ass", motion=motion)
        rows = [ln.split(",", 9) for ln in out.read_text(encoding="utf-8").splitlines()
                if ln.startswith("Dialogue:")]
        return [(r[1], r[2], r[9]) for r in rows]

    still, moving = events(False), events(True)
    assert len(moving) > len(still)
    assert not any(c._POP in t or c._DIM in t for _, _, t in still)      # 끈 쪽은 예전 그대로
    # 구절마다 첫 줄만 튕긴다. 구절 수는 같다.
    assert sum(c._POP in t for _, _, t in moving) == len(still)
    # 같은 글자가 같은 시간대에 뜬다. 구절의 시작·끝 시각이 그대로다.
    assert [plain(t) for _, _, t in moving if c._POP in t] == [plain(t) for _, _, t in still]
    assert moving[0][0] == still[0][0] and moving[-1][1] == still[-1][1]
    for (_, e0, _), (s1, _, _) in zip(moving, moving[1:]):
        assert e0 == s1


# ── 컷과 전환 ────────────────────────────────────────────


def test_cut_kinds_vary_and_keep_the_loop_seam():
    kinds = [mo.cut_kind(i) for i in range(8)]
    assert kinds[0] == "in"                       # 첫 컷은 배율 1.0에서 시작한다
    assert len(set(kinds)) >= 5                   # 줌만 번갈아 쓰지 않는다
    assert mo.cut_kind(5, loop_end=True) == "out"
    # 끝 컷은 이 컷 안에 줌을 다 써서 1.0에서 끝난다(첫 컷의 시작과 이어진다).
    z = mo.kenburns("out", 2.5, punch=False, exact=True)
    rate = float(re.search(r"max\(1\.12-([\d.]+)\*on", z).group(1))
    assert rate * 2.5 * mo.FPS == pytest.approx(mo.ZOOM_MAX - 1.0, abs=1e-3)
    assert "pow(" not in z and "pow(" in mo.kenburns("pan_r", 3.0, punch=True)
    # 팬은 확대 손실 없이 원본(1.2배) 안에서 움직인다.
    assert 1296 / mo.PAN_ZOOM >= 1080


def test_join_graph_keeps_cut_times_and_total_length():
    t = mo.XFADE_SEC
    seg_len = [3.0, 3.0 + t, 3.5 + t, 3.0, 3.0]
    g = mo.join_graph(seg_len, [None, mo.XFADE_IN, mo.XFADE_OUT, None])
    assert "[v0][v1]concat=n=2" in g and "[v3][v4]concat=n=2" in g
    assert f"xfade=transition={mo.XFADE_IN}:duration={t}:offset=6.000" in g    # 원래 컷 시각
    assert f"xfade=transition={mo.XFADE_OUT}:duration={t}:offset=9.500" in g
    assert g.endswith("[vc]")
    # 전환이 없으면 한 번에 잇는다.
    assert mo.join_graph([3.0, 3.0], [None]).startswith("[v0][v1]concat=n=2")


def test_article_scene_is_shorter_with_motion(force_arms):
    force_arms()
    for dur in (35.0, 54.1, 59.1):
        for motion, lo, hi in ((True, mo.ARTICLE_MIN_SEC, mo.ARTICLE_MAX_SEC),
                               (False, 4.0, c.ARTICLE_MAX_SEC)):
            segs = c._plan_segments(TITLE, ARTICLE, BGS, dur, motion=motion)
            art = [d for img, d in segs if img == ARTICLE]
            assert len(art) == 1 and lo <= art[0] <= hi
            assert sum(d for _, d in segs) == pytest.approx(dur, abs=0.01)


# ── 카드 ────────────────────────────────────────────────


def test_crop_card_returns_the_card_and_its_center(tmp_path):
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    img.paste((10, 10, 14, 205), (100, 600, 980, 1100))
    src = tmp_path / "stat_0.png"
    img.save(src)
    out, cx, cy = mo.crop_card(src)
    with Image.open(out) as im:
        assert im.size == (880, 500)
    assert (cx, cy) == (540, 850)
    Image.new("RGBA", (1080, 1920), (0, 0, 0, 0)).save(src)
    assert mo.crop_card(src) is None              # 빈 그림은 움직임 없이 예전처럼 얹는다


def test_pop_filter_ends_at_full_size():
    f = mo.pop_filter(7, "sc0", 12.5, 2.4)
    assert f.startswith("[7:v]") and f.endswith("[sc0]")
    assert "setpts=PTS+12.500/TB" in f            # 카드가 뜨는 시각으로 민다
    assert "max(1," in f                          # 튕긴 뒤 제 크기에서 멈춘다


def test_compare_frames_grow_bars_but_never_change_the_values(tmp_path):
    from src.editor.compare_card import GRAY, Pair, render_compare_card, render_compare_frames
    pair = Pair("5만 4천503건", "3만 4천584건", "1월 1일", "9월 말", 0, 10, False)
    pattern, n = render_compare_frames(pair, tmp_path / "compare_0", n=5)
    assert n == 5 and str(pattern).endswith("compare_0_f%02d.png")
    frames = [Image.open(str(pattern) % i).convert("RGBA") for i in range(n)]

    def bar_px(im):       # 앞 값 막대(회색)의 높이
        x = 1080 // 2 - 190
        return sum(1 for y in range(1920) if im.getpixel((x, y))[:3] == GRAY)

    heights = [bar_px(im) for im in frames]
    assert heights == sorted(heights) and heights[0] < heights[-1]
    # 마지막 장은 움직임 없는 카드와 같은 그림이다(값 글자를 따로 세어 올리지 않는다).
    still = Image.open(render_compare_card(pair, tmp_path / "still.png")).convert("RGBA")
    assert list(frames[-1].getdata()) == list(still.getdata())
