"""컷 계획: 기사 화면 위치, 길이 합, 끝 컷."""

from pathlib import Path

import pytest

from src.editor import composer as c

TITLE = Path("title_card.png")
ARTICLE = Path("article.png")
BGS = [Path(f"bg_{i}.png") for i in range(6)]


def _article_window(segs):
    t = 0.0
    for img, d in segs:
        if img == ARTICLE:
            return t, t + d
        t += d
    return None


@pytest.mark.parametrize("dur", [35.0, 54.1, 59.1])
def test_segments_fill_the_audio(force_arms, dur):
    force_arms()
    segs = c._plan_segments(TITLE, ARTICLE, BGS, dur)
    assert sum(d for _, d in segs) == pytest.approx(dur, abs=0.01)
    assert all(d > 0 for _, d in segs)


def test_article_comes_right_after_first_cut_by_default(force_arms):
    force_arms()
    segs = c._plan_segments(TITLE, ARTICLE, BGS, 54.1)
    assert segs[1][0] == ARTICLE
    start, end = _article_window(segs)
    assert start < 3.5 and end - start <= c.ARTICLE_MAX_SEC


def test_late_arm_moves_article_to_the_middle(force_arms):
    force_arms(article_pos="late")
    for dur in (35.0, 54.1, 59.1):
        segs = c._plan_segments(TITLE, ARTICLE, BGS, dur)
        start, end = _article_window(segs)
        assert 0.3 * dur < start < 0.6 * dur
        assert segs[-1][0] != ARTICLE          # 끝 컷은 첫 컷과 잇는 자리다
        assert segs[0][0] == segs[-1][0]       # 반복 재생 이음새가 그대로다
        assert sum(d for _, d in segs) == pytest.approx(dur, abs=0.01)


def test_no_article_and_no_backgrounds_still_makes_a_video(force_arms):
    force_arms()
    assert c._plan_segments(TITLE, None, BGS, 30.0)
    segs = c._plan_segments(TITLE, None, [], 30.0)
    assert segs and all(img == TITLE for img, _ in segs)


def test_first_sentence_stat_is_deferred_not_dropped(force_arms, tmp_path, monkeypatch):
    """첫 화면을 비울 때, 거기 걸친 숫자는 그 뒤로 미뤄 띄운다(기사 화면이 없을 때)."""
    force_arms(article_pos="late")
    monkeypatch.setattr(c, "VIDEO_DIR", tmp_path)
    script = ("내년 1분기 서울에서 전월세로 나올 빌라가 1만 8452건으로 예측됐습니다. "
              "빌라 전월세 값은 오르고 있습니다. 평균 월세는 67만 9886원으로 최고치입니다.")
    stats = c._plan_stat_overlays(script, 20.0, 0.0, clear_until=c.HOOK_MAX_SEC)
    assert stats, "숫자 카드가 하나도 없다"
    assert stats[0][1] >= c.HOOK_MAX_SEC       # 첫 화면은 비어 있다
    assert stats[0][1] < 8.0                   # 그래도 첫 숫자는 앞쪽에서 나온다
