"""자막: 숫자 강조, 줄바꿈, 발화 시각 맞춤."""

from src.editor import composer as c


def marked(text: str) -> str:
    """노랑으로 칠한 구간을 [ ]로 바꿔 읽기 쉽게."""
    return c._highlight(text).replace(c._HL_ON, "[").replace(c._HL_OFF, "]")


def test_highlight_covers_number_with_its_unit():
    # 11-10 영상: "1분기"의 1, "9886원"의 원 앞까지만 칠해졌다.
    assert marked("내년 1분기 서울에서") == "내년 [1분기] 서울에서"
    assert marked("67만 9886원으로 통계") == "[67만] [9886원]으로 통계"
    assert marked("4월 2억 2757만원에서") == "[4월] [2억] [2757만원]에서"
    assert marked("10.26%포인트 올랐습니다") == "[10.26%포인트] 올랐습니다"
    assert marked("1억 1000만 원 올랐습니다") == "[1억] [1000만 원] 올랐습니다"


def test_highlight_leaves_names_and_other_words_alone():
    assert marked("화곡6동 재개발") == "화곡6동 재개발"       # 이름에 붙은 숫자
    assert marked("제1호 사업") == "제1호 사업"
    assert marked("1주택자 양도세") == "[1]주택자 양도세"     # '주'는 단위가 아니다
    assert marked("2만 원룸") == "[2만] 원룸"
    assert marked("5천 건물") == "[5천] 건물"


def test_wrap_keeps_number_and_unit_on_one_line():
    for line in c._split_phrases("서울 아파트 전세 매물이 1년 새 2억 2천 만원 넘게 올랐습니다."):
        assert not line.startswith(("만원", "억", "천", "%"))
        assert len(line) <= c.GLUE_MAX
    lines = c._wrap_tokens("검단 푸르지오 더 베뉴는 분양가가 올랐습니다".split())
    assert any("푸르지오 더 베뉴는" in x for x in lines)      # 단지 이름이 갈리지 않는다


def test_first_caption_starts_at_zero():
    cues = [("내년 일분기 서울에서 전월세로 나올 빌라가 줄었습니다.", 0.37, 4.0),
            ("빌라 전월세 값은 오르고 있습니다.", 4.2, 7.0)]
    script = "내년 1분기 서울에서 전월세로 나올 빌라가 줄었습니다. 빌라 전월세 값은 오르고 있습니다."
    out = c._phrase_timings(script, 7.0, cues)
    assert out[0][1] == 0.0                 # 첫 프레임부터 자막이 있다
    assert out[-1][2] == 7.0                # 마지막 자막은 끝까지
    starts = [s for _, s, _ in out]
    assert starts == sorted(starts)
    assert all(e > s for _, s, e in out)


def test_phrase_timings_without_cues_fill_the_audio():
    out = c._phrase_timings("집값이 올랐습니다. 전세도 올랐습니다.", 6.0)
    assert out[0][1] == 0.0 and out[-1][2] <= 6.0


def test_parse_srt_cues(tmp_path):
    srt = tmp_path / "n.srt"
    srt.write_text("1\n00:00:00,370 --> 00:00:04,000\n첫 문장입니다.\n\n"
                   "2\n00:00:04,200 --> 00:00:07,000\n둘째 문장입니다.\n", encoding="utf-8")
    assert c.parse_srt_cues(srt) == [("첫 문장입니다.", 0.37, 4.0), ("둘째 문장입니다.", 4.2, 7.0)]
    assert c.parse_srt_cues(tmp_path / "missing.srt") == []


def test_clip_blocked_keeps_the_longer_side():
    assert c._clip_blocked(1.0, 2.0, None) == (1.0, 2.0)
    assert c._clip_blocked(1.0, 2.0, (3.0, 8.0)) == (1.0, 2.0)
    assert c._clip_blocked(7.0, 10.0, (3.0, 8.0)) == (8.0, 10.0)   # 뒤쪽이 길다
    assert c._clip_blocked(1.0, 4.0, (3.0, 8.0)) == (1.0, 3.0)     # 앞쪽이 길다
    s, e = c._clip_blocked(4.0, 6.0, (3.0, 8.0))                   # 통째로 들어가면 길이 0
    assert e - s == 0


def test_lead_trim_bounds(monkeypatch):
    for silence, want in ((0.37, 0.29), (0.12, 0.0), (0.0, 0.0), (2.9, 0.0)):
        monkeypatch.setattr(c, "_leading_silence", lambda p, s=silence: s)
        assert c._lead_trim("x.mp3") == want
