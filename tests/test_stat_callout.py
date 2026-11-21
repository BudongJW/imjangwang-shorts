"""숫자 카드에 띄울 수치 고르기(src/editor/stat_callout.pick_stat)."""

from src.editor.stat_callout import pick_stat


def test_per_area_basis_is_not_a_stat():
    # 11-21 지정 영상: "3.3㎡당 평균 분양가"의 3.3㎡가 숫자 카드로 떴다.
    assert pick_stat("경북의 3.3㎡당 평균") is None
    assert pick_stat("경북의 3.3제곱미터당 평균 분양가는 1642만원")[0] == "1642만"


def test_area_without_dang_still_counts_as_weak_stat():
    assert pick_stat("30평 아파트") is not None
