"""숫자 카드에 띄울 수치 고르기(src/editor/stat_callout.pick_stat)."""

from src.editor.stat_callout import pick_stat


def test_per_area_basis_is_not_a_stat():
    # 11-21 지정 영상: "3.3㎡당 평균 분양가"의 3.3㎡가 숫자 카드로 떴다.
    assert pick_stat("경북의 3.3㎡당 평균") is None
    assert pick_stat("경북의 3.3제곱미터당 평균 분양가는 1642만원")[0] == "1642만"


def test_area_without_dang_still_counts_as_weak_stat():
    assert pick_stat("30평 아파트") is not None


def test_range_with_unit_on_head_is_shown_whole():
    # 11-22 지정 영상: "10억~15억원대 주택"에서 "10억"만 숫자 카드로 떴다.
    assert pick_stat("그래서 10억~15억원대 주택은")[0] == "10억~15억원"
    assert pick_stat("9~15%")[0] == "9~15%"


def test_competition_ratio_beats_area_and_scope_count_is_skipped():
    # 11-23 지정 영상: "45㎡는 65.5대"에서 45㎡가, "서울 7개 단지"에서 7개가 카드로 떴다.
    assert pick_stat("45㎡는 65.5대")[0] == "65.5대 1"
    assert pick_stat("평균 경쟁률 12대 1을")[0] == "12대 1"
    assert pick_stat("올해 하반기 서울 7개 단지를") is None
    assert pick_stat("올해 하반기 서울 7개") is None
    assert pick_stat("30대가 가장 많이") is None or pick_stat("30대가 가장 많이")[0] != "30대 1"


def test_label_drops_cut_comparison_head():
    # 11-25 지정 영상: 이름표가 "전과 비교하면 월세는"으로 떴다.
    from src.editor.stat_callout import stat_context
    assert stat_context("전과 비교하면 월세는 3.53% 상승했고", "3.53%")[0] == "월세는"
