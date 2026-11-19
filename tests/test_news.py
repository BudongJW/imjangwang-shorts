"""기사 선정: 부동산 기사 판별, 소재 점수, 거르는 기사 종류."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from src.collector import news


class Art:
    def __init__(self, title="", summary=""):
        self.title, self.summary = title, summary


@pytest.mark.parametrize("title,want", [
    ("서울 아파트 전세 매물 1년 새 14.3% 줄었다", True),
    ("집 안 사는 게 더 이상하죠", True),                              # 홀로 선 '집'
    ("[특징주] 포스코퓨처엠, 대규모 수주에 급등했다 하락 마감(종합)", False),  # 09-24
    ("[특징주] 현대건설, 재건축 수주에 급등", False),                  # 부동산 낱말이 있어도 증시 기사
    ("반도체 수출 급등, 편집 방향 집중", False),                      # 집중·편집의 '집'
])
def test_is_real_estate(title, want):
    assert news.is_real_estate(title) is want


def test_unit_trade_briefs_and_official_remarks_rank_below_real_news():
    story = news.topic_score("강남 주춤한 사이 외곽 급등…하위 20% 아파트 11.87% 올라")
    brief = news.topic_score("[MAI부동산] 마포구 성사1차 풍림아파트 59.85㎡ 8억 3,000만 원 거래")
    remark = news.topic_score("LH 사장 \"집값 상승은 공급부족 탓\" 국감 발언")
    assert story > brief and story > remark


def test_rule_change_articles_get_a_bonus():
    title = "청년 전세대출 보증 만 39세까지 확대"
    assert news.is_rule_change(title)
    assert not news.is_rule_change("서울 아파트값 86주 연속 상승")
    assert news.topic_score(title) >= news.relatability_score(title) + news.RULE_CHANGE_BONUS


def _rfc822(days_ago):
    return format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago))


def test_recency_score_prefers_fresh_news():
    scores = [news.recency_score(_rfc822(d)) for d in (0.5, 5, 20, 60, 200)]
    assert scores == sorted(scores, reverse=True) and scores[0] > 0 > scores[-1]
    assert news.recency_score("") == -2 and news.recency_score("날짜 아님") == -2


def test_age_days_reads_rss_and_iso_dates():
    assert news._age_days(_rfc822(2)) == pytest.approx(2, abs=0.01)
    iso = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert news._age_days(iso) == pytest.approx(3, abs=0.01)
    assert news._age_days("") is None and news._age_days("어제") is None


def test_opinion_autogen_event_and_promo_articles_are_skipped():
    assert news._is_opinion(Art("[기고] 전세 시장의 미래"))
    assert news._is_opinion(Art("전세 대책 평가", "다음은 박합수 교수의 글이다. 전세 시장은"))
    assert news._is_autogen(Art("[MAI부동산] 신현대9차 75억 거래"))
    assert news._is_event_notice(Art("불안한 전월세…서울시 해법 모색",
                                     "서울시는 7일 서울주거포럼이 개최된다고 밝혔다."))
    assert not news._is_event_notice(Art("서울주거포럼", "서울시는 7일 서울주거포럼을 개최했다."))
    assert news._is_promo(Art("두산건설, 부산 북구서 '두산위브 트리니뷰 구명역' 공급"))
    assert news._is_promo(Art("대학 몰린 곳, 집값도 올랐다",
                              "천안 아이파크 시티가 분양 중이다. 분양 관계자는 수요가 풍부하다고 했다."))
    plain = Art("서울 아파트 전세 매물 1년 새 14.3% 줄었다", "한국부동산원에 따르면 매물이 줄었다.")
    assert not any(f(plain) for f in (news._is_opinion, news._is_autogen,
                                      news._is_event_notice, news._is_promo))


def test_blocked_domains():
    assert news._blocked("https://blog.naver.com/x/1")
    assert not news._blocked("https://www.mk.co.kr/article/12201950")
