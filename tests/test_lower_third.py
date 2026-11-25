"""이름표: 말한 사람·출처·임장왕 체크를 찾는 규칙, 띄우는 시각, 기사 화면 한 줄씩 나타나기."""

import re

import pytest
from PIL import Image

from src.editor import composer as c
from src.editor import lower_third as lt


def tags_of(script: str) -> list[tuple[str, str]]:
    return [(t.kind, t.text) for t in lt.find_tags(script)]


# ── 찾는 규칙 ────────────────────────────────────────────

# 지난 지정 대본에 실제로 있던 문장들이다.
SAID = [
    ("윤지해 부동산평론가는 금리가 오르면 수익률이 떨어질 수 있다고 말했습니다.", "윤지해 부동산평론가"),
    ("부동산원은 중대형 평형 오피스텔의 관망세로 상승폭이 줄며 하락했다고 설명했습니다.", "부동산원"),
    ("반면 함영진 우리은행 랩장은 직접 영향을 주는 건 입주 물량이라고 진단했습니다.", "함영진 우리은행 랩장"),
    ("김영국 국토부 주택토지실장은 최근 입주 물량이 늘어난 영향이 있다고 봤습니다.", "김영국 국토부 주택토지실장"),
    ("유상범 의원은 \"밑 빠진 독에 물 붓기\"라고 지적했습니다.", "유상범 의원"),
    ("장 위원장은 지난해 600명을 채용했지만 1000명이 퇴직했다고 말했습니다.", "장 위원장"),
    ("이성훈 LH 사장이 주담대 6억 한도를 더 줄여야 한다고 했습니다.", "이성훈 LH 사장"),
]
SOURCE = [
    ("한국부동산원에 따르면 10월 둘째 주 경기 아파트값은 0.26% 올랐습니다.", "한국부동산원"),
    ("매일경제와 프롭티어 자료에 따르면, 수도권 빌라 전월세 매물은 3만 4천584건으로 급감했습니다.",
     "매일경제와 프롭티어 자료"),
    ("부동산 플랫폼 다방에 따르면 평균 70만 9천원으로 1년 전보다 8% 올랐습니다.", "부동산 플랫폼 다방"),
]
# 이름표를 붙이면 안 되는 문장. 누구 말인지 확실하지 않거나 말을 옮긴 문장이 아니다.
NO_TAG = [
    "서울 아파트값은 0.2% 올랐다고 부동산원은 밝혔습니다.",     # 문장 머리가 말한 사람이 아니다
    "정부가 수도권 6곳에 7만 6700가구 규모 신규 택지를 발표했습니다.",   # 말을 옮긴 꼴이 아니다
    "매일경제가 국토부 실거래가 자료를 분석했습니다.",
    "서울 오피스텔 매매가격이 22개월 만에 내렸습니다.",
    "전세 부족에 따른 매수가 집값 하락을 제약할 수 있다는 분석입니다.",
    "자금 여력에 맞춰 청약 자격과 대출 조건부터 따져 보라는 조언입니다.",   # 남의 조언이다
    "그는 공공임대 단지에 승부를 걸어야 한다고도 했습니다.",       # 누구인지 이름이 없다
    "여러분 동네 분양 분위기는 어떤가요? 댓글로 알려 주세요.",
]


@pytest.mark.parametrize("sent,who", SAID)
def test_speaker_is_taken_verbatim_from_the_sentence(sent, who):
    assert tags_of(sent) == [("said", who)]
    assert who in sent                          # 대본에 있는 말 그대로다


@pytest.mark.parametrize("sent,who", SOURCE)
def test_source_is_taken_verbatim_from_the_sentence(sent, who):
    assert tags_of(sent) == [("source", who)]
    assert who in sent


@pytest.mark.parametrize("sent", NO_TAG)
def test_unsure_sentences_get_no_tag(sent):
    assert tags_of(sent) == []


def test_host_tag_marks_the_takeaway_and_the_host_reading():
    assert tags_of("전세 계약을 앞두고 있다면 보증 가입 가능 여부부터 확인하세요.") == [("host", lt.HOST_CHECK)]
    assert tags_of("재건축 단지를 보고 있다면 인허가 일정이 늦어질 가능성까지 따져 보세요.") == [
        ("host", lt.HOST_CHECK)]
    assert tags_of("면적별 경쟁률과 단지별 특징을 꼼꼼히 확인하는 것이 중요합니다.") == [("host", lt.HOST_CHECK)]
    assert tags_of("저는 매물부터 늘리는 대책이 먼저라고 봅니다.") == [("host", lt.HOST_VIEW)]


def test_only_one_host_tag_and_it_is_the_last():
    script = ("저는 공급이 먼저라고 봅니다. 한국부동산원에 따르면 전세는 0.12% 올랐습니다. "
              "계약 전 선순위 보증금부터 확인하세요. 여러분 동네는 어떤가요? 댓글로 알려 주세요.")
    assert tags_of(script) == [("source", "한국부동산원"), ("host", lt.HOST_CHECK)]


def test_first_speaker_tag_ends_before_the_second_speaker():
    script = ("김학렬 소장은 \"전세난은 올겨울에 온다\"고 했고, 김인만 대표는 2030년까지 "
              "토지 보상을 마칠 수 있을지도 불확실하다고 했습니다.")
    (tag,) = lt.find_tags(script)
    covered = re.sub(r"\s+", " ", script)[tag.start_pos:tag.end_pos]
    assert tag.text == "김학렬 소장"
    assert "김인만" not in covered               # 뒷사람의 말에는 앞사람 이름표가 없다


# ── 그림 ────────────────────────────────────────────────


@pytest.mark.parametrize("tag", [
    lt.Tag("said", "구독자 185만명 부읽남TV의 정태익 대표", 0, 10),
    lt.Tag("source", "KB부동산", 0, 10),
    lt.Tag("host", lt.HOST_CHECK, 0, 10),
])
def test_tag_stays_between_the_number_card_and_the_caption(tmp_path, tag):
    with Image.open(lt.render_tag(tag, tmp_path / "tag.png")) as im:
        x0, y0, x1, y1 = im.getbbox()
    assert x0 == lt.TAG_X and x1 <= lt.TAG_X + lt.TAG_MAX_W      # 긴 이름도 화면 안에 든다
    assert (y0, y1) == (lt.TAG_Y, lt.TAG_Y + lt.TAG_H + 1)
    assert y1 < 1920 - 480 - 96                  # 자막 상자(아래에서 480px, 글자 72px) 위


# ── 띄우는 시각 ──────────────────────────────────────────

SCRIPT = ("서울 원룸 월세가 70만원을 넘었습니다. 부동산 플랫폼 다방에 따르면 평균 70만 9천원으로 "
          "1년 전보다 8% 올랐습니다. 권대중 한성대 석좌교수는 월세 전환이 빨라졌다고 진단했습니다. "
          "계약 전에는 관리비에 무엇이 들어가는지 꼭 확인하세요.")


def test_tags_show_while_their_sentence_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "VIDEO_DIR", tmp_path)
    plan = c._plan_tag_overlays(SCRIPT, 24.0)
    assert len(plan) == 3
    for (_, s0, e0), (_, s1, _e1) in zip(plan, plan[1:]):
        assert e0 < s1                           # 이름표끼리 겹치지 않는다
    for _, s, e in plan:
        assert c.TAG_MIN_SEC <= e - s <= c.TAG_MAX_SEC
    assert plan[0][1] > 2.0                      # 첫 문장(이름표 없음)이 지난 뒤에 뜬다
    assert plan[-1][2] <= 24.0 - 0.25            # 끝 프레임에는 남기지 않는다(첫 화면과 이어진다)


def test_tags_avoid_the_article_scene_and_the_clear_first_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "VIDEO_DIR", tmp_path)
    base = c._plan_tag_overlays(SCRIPT, 24.0)
    s0, e0 = base[0][1], base[0][2]
    # 기사 화면이 그 문장을 통째로 덮으면 그 이름표는 띄우지 않는다.
    blocked = c._plan_tag_overlays(SCRIPT, 24.0, blocked=(s0 - 0.5, e0 + 0.5))
    assert len(blocked) == len(base) - 1
    # 비워 두는 첫 화면에 걸친 이름표는 그 뒤로 미룬다.
    script = "한국부동산원에 따르면 서울 전세는 0.12% 올랐고 월세는 0.30% 올랐습니다. 다음 문장입니다."
    late = c._plan_tag_overlays(script, 12.0, clear_until=c.HOOK_MAX_SEC)
    assert late and late[0][1] >= c.HOOK_MAX_SEC


# ── 기사 화면 한 줄씩 나타나기 ────────────────────────────


def test_reveal_frames_go_from_base_to_final_one_line_at_a_time(tmp_path):
    from src.collector import article_capture as ac
    base = Image.new("RGB", (1080, 1920), (14, 26, 43))
    final = base.copy()
    boxes = [(80, 900, 1000, 980), (80, 980, 1000, 1060)]
    for box in boxes:
        final.paste((255, 255, 255), box)
    still = tmp_path / "article_quote.png"
    final.save(still)
    n = ac._reveal_frames(base, final, boxes, still)
    assert n == ac.REVEAL_HOLD_FRAMES + ac.REVEAL_STAGGER_FRAMES + ac.REVEAL_LINE_FRAMES
    assert ac.reveal_count(still) == n
    frames = [Image.open(str(ac.reveal_pattern(still)) % i).convert("RGB") for i in range(n)]

    def shown(im, box):       # 그 줄이 나타난 폭(px)
        y = (box[1] + box[3]) // 2
        return sum(1 for x in range(box[0], box[2]) if im.getpixel((x, y)) == (255, 255, 255))

    assert shown(frames[0], boxes[0]) == 0                       # 처음에는 증거 캡처만 있다
    first = [shown(f, boxes[0]) for f in frames]
    second = [shown(f, boxes[1]) for f in frames]
    assert first == sorted(first) and second == sorted(second)   # 닦이듯 늘어나기만 한다
    assert all(a >= b for a, b in zip(first, second))            # 윗줄이 먼저 나온다
    assert list(frames[-1].getdata()) == list(final.getdata())   # 마지막 장은 한 장짜리와 같다
