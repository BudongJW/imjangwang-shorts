"""오케스트레이터의 글 다듬기: 정부명 표기, 끝맺음 질문, 설명란, 공개 시각."""

from datetime import datetime, timedelta, timezone

import pytest

from src.orchestrator import main as m
from src.script_gen.generator import ShortPlan

KST = timezone(timedelta(hours=9))


def plan(script, title="서울 전세 매물 14.3% 감소", headline=("서울 전세 매물", "14.3% 감소")):
    return ShortPlan(headline=list(headline), hook_word="", highlight_sentence="",
                     caption_script=script, speech_script=script, youtube_title=title,
                     hashtags=["부동산", "전세"])


# ── 정부명 표기 ──────────────────────────────────────────────

def test_government_is_named_once_in_plain_context():
    p = m._name_government(plan("정부가 대출 한도를 줄였습니다. 정부는 추가 대책을 예고했습니다."))
    assert p.caption_script.startswith(f"{m.GOV_NAME}가 대출 한도를")
    assert p.caption_script.count(m.GOV_NAME) == 1


@pytest.mark.parametrize("word", ["천정부지", "반정부", "정부미"])
def test_government_inside_another_word_is_untouched(word):
    # 09-15: "천정부지"가 "천이재명 정부지"가 됐다.
    script = f"집값이 {word}로 올랐습니다."
    assert m._name_government(plan(script)).caption_script == script


@pytest.mark.parametrize("script", [
    "지난 정부 3년 동안 수도권 착공 물량이 급감했습니다.",          # 10-25
    "문재인 정부가 2019년 도입한 분양가상한제입니다. 정부는 이를 유지했습니다.",
    "정부의 뉴홈 정책 기준에 따라 전용 모기지 대출이 지원됩니다.",   # 11-04
    "임대차 3법 이후 정부 대책이 이어졌습니다.",
])
def test_past_government_context_is_never_renamed(script):
    assert m._name_government(plan(script)).caption_script == script


def test_central_government_detection():
    assert m._article_names_central_gov("정부가 공급 대책을 발표했다")
    assert not m._article_names_central_gov("서울시가 재건축 공공임대 비율을 조정한다")   # 09-24
    assert not m._article_names_central_gov("집값이 천정부지로 올랐다")


# ── 끝맺음 질문 ──────────────────────────────────────────────

def test_outro_question_is_added_only_in_the_on_arm(monkeypatch):
    script = "서울 전세 매물이 줄었습니다. 전세가격은 올랐습니다."
    monkeypatch.setattr(m, "exp_arm", lambda key: "off")
    assert m._apply_outro_arm(plan(script)).caption_script == script
    monkeypatch.setattr(m, "exp_arm", lambda key: "on")
    out = m._apply_outro_arm(plan(script)).caption_script
    assert out.startswith(script) and "전월세는 어떤가요?" in out
    asked = "서울 전세 매물이 줄었습니다. 여러분 동네는 어떤가요?"
    assert m._apply_outro_arm(plan(asked)).caption_script == asked      # 이미 질문으로 끝남


def test_outro_question_stays_under_the_length_cap(monkeypatch):
    # 11-10: 질문을 붙여 411자(65초)가 됐다.
    monkeypatch.setattr(m, "exp_arm", lambda key: "on")
    sent = "서울 아파트 전세 매물이 1년 새 14.3% 줄었다고 한국부동산원이 밝혔습니다."
    out = m._apply_outro_arm(plan(" ".join([sent] * 12))).caption_script
    assert len(out) <= m.SCRIPT_CHARS_CAP
    assert out.endswith("댓글로 알려 주세요.")


# ── 설명란·제목 ──────────────────────────────────────────────

class Art:
    title = "서울 전세 매물 1년 새 14.3% 줄었다"
    source = "한국경제"
    url = "https://www.hankyung.com/article/1"


def test_description_has_source_but_no_links():
    # 09-19: 설명의 기사 URL이 스팸 정책 위반으로 삭제됐다.
    p = plan("자세한 내용은 https://example.com/a 에서 봅니다. 전세 매물이 줄었습니다.")
    desc = m._build_description(p, Art())
    assert "출처: 한국경제 「서울 전세 매물 1년 새 14.3% 줄었다」" in desc
    assert "http" not in desc and "example.com" not in desc and "hankyung.com" not in desc
    assert "투자 권유가 아닙니다" in desc and "#부동산" in desc


def test_title_gets_at_most_three_hashtags_and_fits_youtube_limit():
    p = plan("x", title="가" * 95)
    p.hashtags = ["부동산", "전세", "월세", "집값"]
    assert len(m._title_with_tags(p)) <= 100
    p = plan("x", title="서울 전세 매물 14.3% 감소")
    p.hashtags = ["부동산", "전세", "월세", "집값"]
    assert m._title_with_tags(p) == "서울 전세 매물 14.3% 감소 #부동산 #전세 #월세"


def test_title_style():
    assert m._title_style('뉴시스 "서울 월세 160만원 시대"') == "인용"
    assert m._title_style("서울 전세 매물 14.3% 감소") == "주장"


# ── 공개 시각 ────────────────────────────────────────────────

def test_publish_at_schedules_today_or_publishes_now():
    now = datetime.now(KST)
    later = now + timedelta(hours=2)
    if later.date() == now.date():
        got = m._publish_at(later.strftime("%H:%M"))
        want = later.replace(second=0, microsecond=0).astimezone(timezone.utc)
        assert got == want.strftime("%Y-%m-%dT%H:%M:%SZ")
    earlier = now - timedelta(hours=2)
    if earlier.date() == now.date():
        assert m._publish_at(earlier.strftime("%H:%M")) is None      # 날짜를 넘기지 않는다
    assert m._publish_at("오전 9시") is None                          # 형식 오류면 즉시 공개


# ── 지정 대본 ────────────────────────────────────────────────

PIN = {
    "article": {"title": "청년 전세대출 보증 대상 확대, 10월부터 시행", "source": "프라임경제",
                "url": "", "summary": "본문"},
    "plan": {"headline": ["청년 전세대출 보증", "만 39세까지 확대"], "hook_word": "만 39세",
             "highlight_sentence": "", "caption_script": "10월부터 나이가 늘어납니다.",
             "youtube_title": "청년 전세대출 보증 만 39세까지 확대, 조건 정리",
             "hashtags": ["청년전세대출"], "face": "none", "keep_len_arm": True},
}


def _write_pin(tmp_path, monkeypatch, **over):
    import json
    data = {**PIN, **over}
    (tmp_path / "pinned_plan.json").write_text(json.dumps(data, ensure_ascii=False),
                                               encoding="utf-8")
    monkeypatch.setattr(m, "OUTPUT_DIR", tmp_path)


def test_pinned_plan_for_today_is_loaded_once_with_selection_score(tmp_path, monkeypatch):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    _write_pin(tmp_path, monkeypatch, use_on=today)
    art, p, face = m._load_pinned_plan()
    assert p.caption_script == "10월부터 나이가 늘어납니다." and face == "none"
    assert p.keep_len_arm is True
    # 지정 대본도 소재 점수를 남긴다(예전에는 전부 0이었다).
    assert art.pick_topic_score == m.news.topic_score(art.title) > 0
    assert art.pick_age_days is None                 # 기사 날짜를 안 적었으면 모르는 값
    assert not (tmp_path / "pinned_plan.json").exists()      # 한 번 쓰면 지운다
    assert m._load_pinned_plan() is None


def test_pinned_plan_with_article_date_records_age(tmp_path, monkeypatch):
    today = datetime.now(KST)
    art_meta = {**PIN["article"], "published": (today - timedelta(days=1)).strftime("%Y-%m-%d")}
    _write_pin(tmp_path, monkeypatch, use_on=today.strftime("%Y-%m-%d"), article=art_meta)
    art, _p, _face = m._load_pinned_plan()
    assert 0 < art.pick_age_days < 3
    assert art.pick_recency_score in (1, 4)


def test_pinned_plan_for_another_day_is_ignored(tmp_path, monkeypatch):
    _write_pin(tmp_path, monkeypatch, use_on="2020-01-01")
    assert m._load_pinned_plan() is None
    assert (tmp_path / "pinned_plan.json").exists()          # 지우지 않고 둔다


def test_broken_pinned_plan_falls_back_to_auto(tmp_path, monkeypatch):
    (tmp_path / "pinned_plan.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(m, "OUTPUT_DIR", tmp_path)
    assert m._load_pinned_plan() is None
