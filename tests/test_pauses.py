"""나레이션 사이 공백 줄이기: 어디를 자르는지, 자른 뒤 시각이 어떻게 당겨지는지."""

import subprocess

import pytest

from src.editor import composer as c

# 11-25 검증 영상(Azure, 51.65초)에서 잰 무음 구간의 앞부분과 끝. 맨 앞 0.37초는 앞 무음이다.
SILENCES = [(0.0, 0.37), (3.19, 4.63), (6.30, 6.57), (9.21, 10.66), (50.55, 51.65)]
TOTAL, LEAD = 51.65, 0.29


@pytest.fixture
def measured(monkeypatch):
    monkeypatch.setattr(c, "_silences", lambda path: list(SILENCES))


def test_long_pauses_are_cut_to_a_short_breath(measured):
    cuts = c._pause_cuts("n.mp3", TOTAL, LEAD)
    assert len(cuts) == 3                        # 문장 사이 둘 + 맨 끝. 앞 무음과 쉼표는 뺀다
    # 문장 사이 1.44초에서 가운데를 잘라 0.25초만 남긴다(양쪽에 절반씩).
    a, b = cuts[0]
    assert a == pytest.approx(3.19 + c.PAUSE_KEEP_SEC / 2 - LEAD)
    assert b == pytest.approx(4.63 - c.PAUSE_KEEP_SEC / 2 - LEAD)
    assert (4.63 - 3.19) - (b - a) == pytest.approx(c.PAUSE_KEEP_SEC)
    # 쉼표에서 쉬는 0.27초는 건드리지 않는다.
    assert not any(a < 6.4 - LEAD < b for a, b in cuts)
    # 맨 끝은 0.2초만 남기고 끝까지 자른다.
    a, b = cuts[-1]
    assert a == pytest.approx(50.55 + c.TAIL_KEEP_SEC - LEAD)
    assert b == pytest.approx(TOTAL - LEAD)


def test_times_after_a_cut_move_earlier_by_what_was_removed(measured):
    cuts = c._pause_cuts("n.mp3", TOTAL, LEAD)
    removed = [b - a for a, b in cuts]
    assert c._shift(1.0, cuts) == 1.0                                  # 자르기 전 시각은 그대로
    assert c._shift(5.0, cuts) == pytest.approx(5.0 - removed[0])       # 첫 공백 뒤
    assert c._shift(20.0, cuts) == pytest.approx(20.0 - removed[0] - removed[1])
    # 잘라 낸 구간 안의 시각은 그 구간이 시작하는 자리로 모인다(음수가 되거나 뒤집히지 않는다).
    mid = (cuts[0][0] + cuts[0][1]) / 2
    assert c._shift(mid, cuts) == pytest.approx(cuts[0][0])
    ts = [x / 10 for x in range(0, 515)]
    shifted = [c._shift(t, cuts) for t in ts]
    assert shifted == sorted(shifted)
    # 전체 길이는 잘라 낸 만큼 줄어든다.
    assert c._shift(TOTAL - LEAD, cuts) == pytest.approx(TOTAL - LEAD - sum(removed))


def test_silent_or_unmeasured_audio_is_left_alone(monkeypatch):
    # 미리보기의 무음 나레이션: 통째로 무음이라 말소리가 어디인지 모른다.
    monkeypatch.setattr(c, "_silences", lambda path: [(0.0, 55.0)])
    assert c._pause_cuts("n.mp3", 55.0) == []
    monkeypatch.setattr(c, "_silences", lambda path: [])
    assert c._pause_cuts("n.mp3", 55.0) == []
    assert c._shift(12.3, []) == 12.3


def test_silences_are_read_from_ffmpeg_and_an_open_end_runs_to_the_end(monkeypatch):
    log = ("[silencedetect @ 0x1] silence_start: 0\n"
           "[silencedetect @ 0x1] silence_end: 0.37 | silence_duration: 0.37\n"
           "[silencedetect @ 0x1] silence_start: 3.19\n"
           "[silencedetect @ 0x1] silence_end: 4.63 | silence_duration: 1.44\n"
           "[silencedetect @ 0x1] silence_start: 50.55\n")       # 끝 시각을 안 적어 준 경우

    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout="", stderr=log)

    monkeypatch.setattr(c, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(c.subprocess, "run", fake_run)
    sil = c._silences("n.mp3")
    assert sil[:2] == [(0.0, 0.37), (3.19, 4.63)]
    assert sil[2][0] == 50.55 and sil[2][1] == float("inf")
    # 끝이 열린 무음은 전체 길이까지로 보고 맨 끝 무음으로 자른다.
    cuts = c._pause_cuts("n.mp3", 51.65)
    assert cuts[-1] == (pytest.approx(50.55 + c.TAIL_KEEP_SEC), pytest.approx(51.65))
