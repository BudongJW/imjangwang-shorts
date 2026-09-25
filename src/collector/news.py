"""부동산 뉴스 자동 수집.

두 곳에서 후보를 모은다.
  1. 언론사 RSS(PUBLISHER_FEEDS) — 원문 URL과 본문이 그대로 딸려 온다.
  2. 구글뉴스 RSS 검색 — 매체를 가로질러 폭넓게 잡지만 원문 URL을 주지 않는다.
중복(history)·차단 도메인을 걸러 대표 기사 1건을 고르고, 본문 요약과 대표
이미지를 확보한다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from config.settings import (
    PUBLISHER_FEEDS,
    NEWS_QUERIES,
    NEWS_MAX_CANDIDATES,
    NEWS_BLOCK_DOMAINS,
)
from src.collector.history import is_duplicate, load_history
from src.utils.buildnotes import note
from src.utils.logger import setup_logger

log = setup_logger("news")

RSS_TMPL = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# 피드 요청 전용. 한국경제는 브라우저를 흉내 낸 긴 UA에 403을 준다
# (2026-09-17 실측: Chrome UA 403, "Mozilla/5.0" 200). 기사 본문 요청은
# 기존 UA를 그대로 쓴다 — 본문 쪽은 반대로 짧은 UA를 막는 매체가 있다.
FEED_UA = "Mozilla/5.0"


@dataclass
class Article:
    title: str
    source: str = ""
    published: str = ""
    google_url: str = ""
    url: str = ""          # 리다이렉트 해소된 원문 URL
    summary: str = ""      # 본문 발췌 (대본 근거)
    image_url: str = ""    # 대표 이미지(og:image)
    query: str = ""
    extras: dict = field(default_factory=dict)
    # 선정 당시의 근거. 왜 이 기사가 뽑혔는지를 나중에 조회수와 맞춰 보려면
    # 남겨야 한다. 2026-08-31에 신선도 감점(90일 초과 -12)을 넣었는데 그 뒤
    # 조회수가 내려앉았다. 같은 커밋 메시지에 "3월 기사 기반 08-27 영상
    # 2,239회"라는 반례가 적혀 있다. 추측으로 되돌리지 않고 기록부터 쌓는다.
    pick_age_days: float | None = None
    pick_topic_score: int = 0
    pick_recency_score: int = 0


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _blocked(url: str) -> bool:
    d = _domain(url)
    return any(b in d or b in url for b in NEWS_BLOCK_DOMAINS)


def _fetch_rss(query: str) -> list[Article]:
    """질의 하나의 구글뉴스 RSS를 읽는다. 실패해도 빈 리스트로 넘어간다.

    feedparser.parse(url)은 내부 urllib에 타임아웃이 없어 한 피드가 멈추면
    잡 전체가 묶인다. 질의를 전부 도는 지금은 노출이 10배라 requests로
    받아서 넘긴다.
    """
    url = RSS_TMPL.format(q=quote(query))
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": UA})
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"RSS 수집 실패({query}): {e}")
        return []
    feed = feedparser.parse(resp.content)
    out: list[Article] = []
    for e in feed.entries:
        raw = e.get("title", "")
        # 구글뉴스 제목은 "헤드라인 - 언론사" 형태가 많다
        m = re.match(r"^(.*?)\s*[-–]\s*([^-–]+)$", raw)
        title = (m.group(1) if m else raw).strip()
        source = (m.group(2).strip() if m else e.get("source", {}).get("title", "")) or ""
        out.append(
            Article(
                title=title,
                source=source,
                published=e.get("published", ""),
                google_url=e.get("link", ""),
                query=query,
            )
        )
    return out


# og:image 하나만 보면 기사 대표사진이 자주 비고, 그러면 배경이 폴백
# 그라디언트로 떨어져 썸네일이 텅 빈다. 후보를 넓히고 명백한 로고·아이콘만
# 걸러낸다.
_IMG_META = (
    ("meta", {"property": "og:image"}),
    ("meta", {"property": "og:image:url"}),
    ("meta", {"name": "twitter:image"}),
    ("meta", {"name": "twitter:image:src"}),
    ("link", {"rel": "image_src"}),
)
_IMG_BAD = ("logo", "favicon", "icon", "sprite", "blank", "default_",
            "profile", "avatar", "banner", "btn_", "ico_")


def _pick_article_image(soup, page_url: str) -> str:
    """기사 대표 이미지 URL을 고른다. 메타 태그 우선, 없으면 본문 <img>."""
    from urllib.parse import urljoin

    def ok(u: str) -> bool:
        if not u or u.startswith("data:"):
            return False
        low = u.lower()
        return not any(b in low for b in _IMG_BAD)

    for tag, attrs in _IMG_META:
        el = soup.find(tag, attrs=attrs)
        if not el:
            continue
        u = el.get("content") or el.get("href") or ""
        if ok(u):
            return urljoin(page_url, u)

    # 메타가 없으면 본문에서 가장 큰 이미지를 고른다(width/height 속성 기준).
    best, best_area = "", 0
    for img in soup.find_all("img")[:60]:
        u = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if not ok(u):
            continue
        try:
            area = int(img.get("width", 0) or 0) * int(img.get("height", 0) or 0)
        except (TypeError, ValueError):
            area = 0
        if area > best_area:
            best, best_area = urljoin(page_url, u), area
        elif not best:
            best = urljoin(page_url, u)     # 크기 정보가 없으면 첫 후보라도
    return best


def _fetch_publisher_rss(source: str, url: str) -> list[Article]:
    """언론사 RSS 하나를 읽는다. 실패해도 빈 리스트로 넘어간다.

    구글뉴스와 달리 link가 원문 URL이라 _resolve_and_enrich가 그대로 본문을
    긁어 온다. google_url 자리에 원문 URL을 넣어 이후 경로를 공유한다.
    """
    # UA 취향이 매체마다 갈린다. 2026-09-17 실측: 한국경제는 브라우저를
    # 흉내 낸 긴 UA에 403을 주고 "Mozilla/5.0"에 200, 매일경제는 정반대다.
    # 하나로는 어느 쪽이든 피드 하나가 통째로 죽는다. 둘 다 시도한다.
    resp = None
    last = None
    for ua in (FEED_UA, UA):
        try:
            r = requests.get(url, timeout=12, headers={"User-Agent": ua})
            r.raise_for_status()
            resp = r
            break
        except requests.RequestException as e:
            last = e
    if resp is None:
        log.warning(f"언론사 RSS 실패({source}): {last}")
        return []
    feed = feedparser.parse(resp.content)
    out: list[Article] = []
    for e in feed.entries:
        link = e.get("link", "")
        if not link.startswith("http"):
            continue
        out.append(
            Article(
                title=(e.get("title", "") or "").strip(),
                source=source,
                published=e.get("published", ""),
                google_url=link,
                query=f"feed:{source}",
            )
        )
    return out


def _resolve_and_enrich(art: Article, session: requests.Session) -> None:
    """구글뉴스 리다이렉트를 따라가 원문 URL·본문·이미지를 채운다. 실패해도 무해."""
    try:
        r = session.get(art.google_url, timeout=12, allow_redirects=True)
        final = r.url
        # 구글뉴스가 중간 페이지를 주는 경우, 본문 내 첫 외부 링크를 추출
        if "news.google.com" in _domain(final):
            soup = BeautifulSoup(r.text, "lxml")
            a = soup.find("a", href=re.compile(r"^https?://(?!news\.google)"))
            if a:
                final = a["href"]
                r = session.get(final, timeout=12, allow_redirects=True)
                final = r.url
        art.url = final
        if _blocked(final):
            return
        soup = BeautifulSoup(r.text, "lxml")
        art.image_url = _pick_article_image(soup, final)
        # 요약 소스1: 메타 설명(og:description / description) — 대개 기사 1~2문장 요약
        desc = ""
        for attrs in ({"property": "og:description"}, {"name": "description"}):
            m = soup.find("meta", attrs=attrs)
            c = (m.get("content").strip() if m and m.get("content") else "")
            # 구글뉴스 자체 보일러플레이트는 본문이 아니므로 제외
            if len(c) >= 30 and "Google News" not in c and "aggregated from sources" not in c:
                desc = c
                break
        body = _extract_body(soup)
        art.summary = ((desc + " " if desc else "") + body).strip()[:2500]
    except Exception as e:  # 네트워크/파싱 실패는 후보에서 조용히 스킵 가능
        log.info(f"  enrich 실패({art.source}): {e}")


# 매체별 기사 본문 컨테이너. <p>만 모으는 방식은 본문을 거의 못 가져오는
# 매체가 있다 — 2026-09-16 실측에서 매일경제 기사 본문 2,024자 중 <p>로
# 잡힌 것은 293자(14%)뿐이었다. 본문이 <div>와 <br>로 조판돼 있어서다.
# 그 상태로 대본을 쓰면 모델이 빈칸을 추측으로 메운다. 실제로 기사의
# "월 생활비 최저 190만원(서비스 포함)"이 대본에서 "월세 190만원"이 됐다.
_BODY_SELECTORS = (
    ".news_cnt_detail_wrap",        # 매일경제
    "#textBody", ".viewer",         # 뉴시스
    ".story-news", "#articleWrap",  # 연합뉴스
    ".article-text", ".text",       # 한겨레
    "#articleBody", ".art_body",    # 경향신문
    "article",                      # 일반
)
_BODY_JUNK = ("script, style, iframe, figure, figcaption, "
              ".ad, .banner, .relate, .reporter, .share, .copyright, "
              ".txt-copyright, .comp-box-title, .end-photo, .article-photo, "
              ".photo, .caption, .img-con, .adrs")
# 본문 앞에 붙는 위젯·사진설명 찌꺼기. 태그로 못 걸러지는 것만 여기서 턴다.
# 실측으로 확인된 것만 넣는다 — 일반화하려다 본문 첫 문장을 날리면 손해가 크다.
_BODY_LEAD_JUNK = re.compile(
    r"^(?:광고\s*|\S{2,4}\s*기자\s*구독\s*구독중\s*이전\s*다음\s*)+")
_MIN_BODY = 300


def _extract_body(soup) -> str:
    """기사 본문 텍스트. 매체별 컨테이너를 먼저 보고, 없으면 <p>를 모은다."""
    for sel in _BODY_SELECTORS:
        node = soup.select_one(sel)
        if node is None:
            continue
        for junk in node.select(_BODY_JUNK):
            junk.decompose()
        text = _BODY_LEAD_JUNK.sub("", re.sub(r"\s+", " ", node.get_text(" ")).strip())
        if len(text) >= _MIN_BODY:
            return text
    # 폴백: 한글이 든 충분한 길이의 <p>를 모은다(중복 제거).
    seen, paras = set(), []
    for p in soup.find_all("p"):
        t = p.get_text(" ", strip=True)
        if len(t) >= 50 and re.search(r"[가-힣]", t) and t[:30] not in seen:
            seen.add(t[:30])
            paras.append(t)
    return " ".join(paras[:10])


# 실수요자 직결 핵심 주제(가점) vs 추상·니치 주제(감점) — 조회 부진 원인이 주제 관련성.
#
# 핵심 주제를 둘로 나눈다. '부동산 고유어'와 '자산 공통어'다.
#
# 2026-09-24 실측: 파이프라인이 "[특징주] 포스코퓨처엠, 대규모 수주에 급등했다
# 하락 마감(종합)"을 골랐다. 이차전지 종목 기사다. 급등과 하락이 둘 다 핵심
# 주제로 세어져 7점이 나왔고, 그날 부동산 기사들과 같은 점수대였다.
# 급등·폭등·하락·신고가는 값이 움직였다는 말일 뿐이라 주식에도 그대로 붙는다.
# 부동산 소재인지는 '집값·전세·아파트' 같은 고유어가 정해야 한다.
_DOMAIN_KW = [
    "집값", "전세", "월세", "전월세", "매매", "아파트", "분양", "청약", "재건축", "재개발",
    "보유세", "양도세", "종부세", "취득세", "임대차", "토허", "전세사기", "내집마련",
    "전세난", "역전세", "입주", "갭투자", "깡통전세", "분양가", "미분양", "집주인", "세입자",
    "주택", "부동산", "오피스텔", "빌라", "단지", "상가", "토지", "임대", "공시가", "매물",
    "정비사업", "분담금", "기부채납", "용적률", "실거래",
    # 2026-09-25 추가: 실제 피드에서 부동산 기사인데 빠지던 것들.
    # "HUG, PF 보증료 최대 40% 인하", "李 분당집 '17억 근저당'" 등.
    # '착공'은 넣지 않는다. 같은 날 "반도체 팹 2기 2028년 착공"이 있었다.
    "주거", "근저당", "PF", "HUG", "주택도시보증공사", "LH", "등기", "전입",
]
# 어느 자산에나 붙는 말. 가점은 주되 이것만으로 부동산 소재라고 보지 않는다.
_GENERIC_KW = [
    "대출", "금리", "세금", "세제", "규제", "실수요", "신고가", "급등", "폭등", "하락", "공급",
]
_CORE_KW = _DOMAIN_KW + _GENERIC_KW

# 낱말로 홀로 선 '집'도 고유어로 본다. "집 안 사는 게 더 이상하죠"(2026-09-20,
# 873회)가 고유어 목록에 하나도 안 걸렸다. 앞뒤가 한글이면 집중·수집·편집이라
# 세지 않는다.
_BARE_HOUSE_RE = re.compile(r"(?<![가-힣])집(?![가-힣])")

# 고유어가 하나도 없으면 부동산 기사가 아닐 가능성이 크다. 점수에서도 깎지만
# 선정 단계에서는 아예 빼 버린다(is_real_estate). 감점만 두면 소재가 얇은 날
# 다시 올라온다.
NO_DOMAIN_PENALTY = 6

# 증시 기사 표지. 제목에 부동산 낱말이 있어도 이게 붙으면 주식 기사다.
#   "[특징주] 현대건설, 재건축 수주에 급등" → 건설주 시세 기사
# '주가'는 앞에 한글이 붙으면 세지 않는다("민주가 추진" 같은 오탐 방지).
_STOCK_RE = re.compile(
    r"특징주|종목|상한가|하한가|공모주|시가총액|시총|장중|증시|코스피|코스닥|증권가|"
    r"목표주가|순매수|순매도|(?<![가-힣])주가")


def _has_domain(title: str) -> bool:
    t = title or ""
    return any(k in t for k in _DOMAIN_KW) or bool(_BARE_HOUSE_RE.search(t))


def is_real_estate(title: str) -> bool:
    """이 채널이 다룰 수 있는 기사인가. 부동산 채널이지 주식 채널이 아니다.

    2026-09-25 사용자 지시: 부동산 관련 내용만 사용한다. 전날 파이프라인이
    이차전지 종목 기사("[특징주] 포스코퓨처엠…")를 골랐다.
    """
    t = title or ""
    return _has_domain(t) and not _STOCK_RE.search(t)

_NICHE_KW = [
    "글로벌", "해외", "도쿄", "일본", "미국", "중국", "유럽", "성과급", "반도체", "삼성전자",
    "하이닉스", "증시", "코스피", "코스닥", "채권", "리츠", "수익형", "환율", "비트코인",
    "가상자산", "코인", "연예", "스타", "배우", "가수",
    # 증시 기사 표지. 연합뉴스 [특징주]가 하루 수십 건 나온다.
    "특징주", "종목", "주가", "상한가", "하한가", "공모주", "시가총액", "시총", "장중",
]
# 정책 비판·부작용 신호(가점) — 인기 영상은 '정책 역효과·서민 피해'를 다뤘다.
_CRIT_KW = [
    "부작용", "역효과", "역설", "실패", "오판", "논란", "반발", "우려", "비판", "실효성",
    "무력화", "부메랑", "폭탄", "직격", "날벼락", "눈물", "피해", "잠김", "전가", "징벌",
    "덫", "헛발", "붕괴", "성토",
]
# '가격 폭등 고통' = 최고 대박 패턴(집값 14% 폭등/월세 폭탄 등). 세금 메커니즘보다 강가점.
_PAIN_KW = [
    "폭등", "급등", "치솟", "천정부지", "폭발", "전세난", "월세난", "전세대란", "월세대란",
    "지옥", "못 산다", "못산다", "미쳤", "미친", "역대급", "트리플", "신고가", "폭탄전가",
]


def relatability_score(title: str) -> int:
    """제목의 실수요자 관련성 점수(핵심 주제 +2, 니치 주제 -3, 고유어 없으면 감점)."""
    t = title or ""
    core = sum(1 for k in _CORE_KW if k in t)
    niche = sum(1 for k in _NICHE_KW if k in t)
    no_domain = 0 if _has_domain(t) else NO_DOMAIN_PENALTY
    return core * 2 - niche * 3 - no_domain


# 단지 하나짜리 실거래가 글. 제목에 전용면적이 박혀 있는 것이 신호다.
#   "[MAI부동산] 마포구 성사1차 풍림아파트 59.85㎡ 8억 3,000만 원 거래"
# 매일경제가 이 코너를 하루 10건씩 쏟아내고, 제목에 아파트·거래·신고가가
# 들어가 topic_score가 11점까지 나온다. 2026-09-16과 09-17 이틀 연속으로
# 이게 영상 소재가 됐다(조회수 162회, 51회로 채널 최저권).
#
# 이건 성적 추정이 아니라 편집 판단이다. 단지 한 채 실거래가는 뉴스가
# 아니라 시세 DB 자동 생성 글이고, 이 채널의 상위 영상은 전부 전국 공통
# 이슈였다. 배제가 아니라 감점이라 소재가 없는 날에는 여전히 쓸 수 있다.
_UNIT_BRIEF_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:㎡|m2|제곱미터)")
BRIEF_PENALTY = 8


def topic_score(title: str) -> int:
    """영상 소재 점수 = 관련성 + 정책 비판 + '가격 폭등 고통' 강가점(대박 패턴 편향)."""
    t = title or ""
    crit = sum(1 for k in _CRIT_KW if k in t)
    pain = sum(1 for k in _PAIN_KW if k in t)
    brief = BRIEF_PENALTY if _UNIT_BRIEF_RE.search(t) else 0
    return relatability_score(t) + crit * 2 + pain * 3 - brief


def _age_days(published: str) -> float | None:
    """RSS pubDate(RFC822) → 현재 기준 경과일. 값이 없거나 파싱 실패면 None."""
    if not published:
        return None
    try:
        dt = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


# 구글뉴스 RSS는 질의어만 맞으면 몇 년 전 기사도 상위로 올려준다. 키워드 점수만으로
# 정렬하면 옛날 기사가 오늘 통계 기사를 이긴다 — 실측(2026-09-01, 후보 761건)에서
# 상위 5건이 4개월~6년 전 기사였다. 매일 아침 브리핑 채널이므로 신선도를 점수에
# 직접 반영한다. 다만 하드 필터가 아니라 감점이다: 전부 오래된 날에도 후보가
# 비지 않아야 하고, 공시가·보유세처럼 몇 달 지나도 유효한 소재가 실제로 성과를
# 낸 적이 있다(3월 기사 기반 08-27 영상 2,239회).
def recency_score(published: str) -> int:
    age = _age_days(published)
    if age is None:
        return -2      # 날짜 불명 — 정상 기사일 수 있으므로 약하게만
    if age <= 2:
        return 4       # 오늘·어제 뉴스
    if age <= 7:
        return 1
    if age <= 30:
        return -3
    if age <= 90:
        return -7
    return -12         # 3개월 초과 — 사실관계가 바뀌었을 가능성이 크다


def candidate_score(art: Article) -> int:
    """정렬용 최종 점수 = 소재 점수 + 신선도."""
    return topic_score(art.title) + recency_score(art.published)


def collect(max_candidates: int = NEWS_MAX_CANDIDATES) -> list[Article]:
    """부동산 뉴스 후보를 수집한다(중복·차단 제외, 관련성순 정렬)."""
    seen_titles: set[str] = set()
    candidates: list[Article] = []
    dropped = 0                # 부동산 기사가 아니라서 뺀 수
    history = load_history()   # 후보마다 재파싱하지 않도록 1회만 읽는다
    # 질의를 전부 돈다. 예전에는 후보가 max_candidates를 넘으면 break 했는데,
    # 첫 질의 하나만으로 70건이 넘어 나머지 9개 질의가 한 번도 쓰이지 않았다.
    # 그 결과 후보 풀이 한 질의에 갇혀, 신선한 기사가 아예 없는 날이 생겼다.
    # 언론사 피드를 먼저 넣는다. 원문 URL·본문이 딸려 있어 본문 확보 성공률이
    # 훨씬 높고, 점수가 같으면 먼저 들어온 쪽이 남는다.
    feeds = [(src, url, _fetch_publisher_rss(src, url)) for src, url in PUBLISHER_FEEDS]
    n_feed = sum(len(x[2]) for x in feeds)
    for _src, _url, arts in feeds:
        for art in arts:
            key = art.title[:30]
            if not art.title or key in seen_titles:
                continue
            seen_titles.add(key)
            if is_duplicate(art.title, history=history) or _blocked(art.google_url):
                continue
            if not is_real_estate(art.title):
                dropped += 1
                continue
            candidates.append(art)

    for q in NEWS_QUERIES:
        for art in _fetch_rss(q):
            key = art.title[:30]
            if not art.title or key in seen_titles:
                continue
            if _blocked(art.google_url):
                continue
            if is_duplicate(art.title, history=history):
                continue
            seen_titles.add(key)
            if not is_real_estate(art.title):
                dropped += 1
                continue
            candidates.append(art)
    if dropped:
        log.info(f"부동산 외 기사 {dropped}건 제외(고유어 없음 또는 증시 기사)")
    # 관련성 + 정책 비판 신호 + 신선도로 정렬(좋은 소재 자동 선별)
    candidates.sort(key=candidate_score, reverse=True)
    if candidates:
        top = candidates[0]
        age = _age_days(top.published)
        log.info(f"수집 {len(candidates)}건 · 최상위 {candidate_score(top)}점"
                 f"(소재 {topic_score(top.title)} + 신선도 {recency_score(top.published)}"
                 f", {'날짜불명' if age is None else f'{age:.1f}일 전'}) "
                 f"({top.title[:24]})")
    return candidates[:max_candidates]


def _enrich_order(candidates: list[Article]) -> list[Article]:
    """본문을 실제로 가져올 수 있는 후보를 먼저 시도하도록 재배열한다.

    구글뉴스 링크는 원문 URL을 주지 않아 본문 확보가 거의 확정적으로
    실패한다(과제 #14). 2026-09-17 실측: 점수 상위 8개 중 7개가 구글뉴스라
    본문 0자로 탈락하고, 8번째 언론사 직접 링크 하나만 통과했다. 그래서
    실질적으로 매일경제 부동산 피드 하나에 소재가 묶였다.

    점수 순서는 그대로 두고, 같은 점수대에서 직접 링크를 앞에 놓는다.
    직접 링크가 모두 실패하면 구글뉴스 후보도 그대로 시도한다.
    """
    direct = [a for a in candidates if "news.google" not in _domain(a.google_url or a.url)]
    via_google = [a for a in candidates if a not in direct]
    return direct + via_google


# 구글뉴스 후보가 앞을 채우는 날이 있어 실제로 시도되는 언론사 후보가
# 한두 개뿐이었다. 넉넉히 본다(각 후보당 약 0.5초).
PICK_TOP_N = 14


def pick_and_enrich(candidates: list[Article], top_n: int = PICK_TOP_N) -> Article | None:
    """관련성 높은 순으로 원문 해소하여 본문 확보된 첫 기사를 반환한다.

    아무도 기준(본문 80자)을 넘기지 못하면, 예전에는 candidates[0]을 그대로
    돌려줬다. 그 후보는 해소를 시도조차 안 했을 수 있어 summary가 빈
    문자열이고 url이 구글뉴스 리다이렉트 주소다. 그러면 뒤에서 전부 무너진다.
      - 기사 캡처가 리다이렉트 페이지에서 실패하고
      - 폴백 카드는 본문 0자로 거의 빈 화면이 되고(8초 구간)
      - 영상 설명의 '출처' 링크가 구글뉴스 주소로 나간다
    (2026-09-14 검증에서 lead 0자로 실측)
    그래서 돌아본 후보 중 본문이 가장 긴 것을 대신 돌려준다.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    best: Article | None = None
    for art in _enrich_order(candidates)[:top_n]:
        _resolve_and_enrich(art, session)
        if art.url and not _blocked(art.url) and len(art.summary) >= 80:
            age = _age_days(art.published)
            log.info(f"선정({candidate_score(art)}점 = 소재 {topic_score(art.title)} + "
                     f"신선도 {recency_score(art.published)}, "
                     f"{'날짜불명' if age is None else f'{age:.1f}일 전'}): "
                     f"{art.title} ({art.source})")
            note(f"기사 선정: 본문 {len(art.summary)}자 · {art.source} · "
                 f"{age if age is None else round(age, 1)}일 전 · "
                 f"{candidate_score(art)}점(소재 {topic_score(art.title)} + "
                 f"신선도 {recency_score(art.published)}) · {art.title[:40]}")
            art.pick_age_days = age
            art.pick_topic_score = topic_score(art.title)
            art.pick_recency_score = recency_score(art.published)
            return art
        if best is None or len(art.summary) > len(best.summary):
            best = art
        time.sleep(0.5)
    if best is None:
        best = candidates[0] if candidates else None
    if best is not None:
        log.warning(f"본문 80자 이상인 기사를 찾지 못했다 → 본문 {len(best.summary)}자짜리로 진행")
        note(f"기사 선정 실패: {top_n}개 모두 본문 80자 미만 → "
             f"본문 {len(best.summary)}자짜리 사용 ({best.source} · {best.title[:40]})")
    return best
