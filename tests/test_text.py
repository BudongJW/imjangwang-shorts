"""표기 교정, 발음형 변환, 링크 제거, 기록 병합."""

import json
import subprocess
import sys
from pathlib import Path

from src.script_gen.correct_terms import normalize_caption, to_speech
from src.utils.text import strip_links

ROOT = Path(__file__).resolve().parent.parent


def test_caption_typos_are_fixed():
    assert normalize_caption("LTB 규제로 실수 요자가 줄었다") == "LTV 규제로 실수요자가 줄었다"
    assert normalize_caption("아파트 72차를 팔았다") == "아파트 72채를 팔았다"


def test_complex_names_and_rounds_keep_their_digits():
    # 10-02: '신현대9차'가 '신현대9채'로 나갔다.
    for text in ("신현대9차 전용 84㎡", "신반포15차 재건축", "3차 신도시 발표", "1차 조사 결과"):
        assert normalize_caption(text) == text


def test_mixed_digit_numbers_become_plain_numbers():
    assert normalize_caption("4천6백6십8가구") == "4668가구"         # 10-02
    for text in ("4천만 원", "2만 4000가구", "3천 건"):               # 원래 쓰는 꼴은 그대로
        assert normalize_caption(text) == text


def test_garbled_ranges_are_restored():
    assert normalize_caption("2023에서 2025년 사이") == "2023~2025년 사이"
    assert normalize_caption("1에서 4월 누적") == "1~4월 누적"
    assert normalize_caption("2023에서 2025년으로 미뤘다") == "2023에서 2025년으로 미뤘다"


def test_speech_form_reads_symbols_and_ranges():
    assert to_speech("LTV 70%") == "엘티브이 70퍼센트"
    assert to_speech("전용 84㎡") == "전용 84제곱미터"
    assert to_speech("올해 1~9월 인허가") == "올해 1월부터 9월까지 인허가"
    assert to_speech("2023~2025년 평균") == "2023년부터 2025년까지 평균"
    assert "삼기 신도시" in to_speech("3기 신도시")


def test_strip_links_removes_every_url_form():
    out = strip_links("원문 https://www.mk.co.kr/article/1 과 www.example.com/a 참고")
    assert "http" not in out and "www." not in out and "원문" in out


def test_merge_history_keeps_both_sides_without_duplicates(tmp_path):
    base = [{"title": "가", "video_id": "a1", "date": "2026-11-08T02:00:00"},
            {"title": "나", "video_id": "b2", "date": "2026-11-09T02:00:00"}]
    run = [base[0], {"title": "다", "video_id": "c3", "date": "2026-11-10T02:00:00"}]
    bp, rp, out = tmp_path / "base.json", tmp_path / "run.json", tmp_path / "out.json"
    bp.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    rp.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "merge_history.py"),
                    str(bp), str(rp), str(out)], check=True)
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert [r["video_id"] for r in merged] == ["a1", "b2", "c3"]
