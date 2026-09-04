"""부동산 뉴스 자동 수집.

Google 뉴스 RSS(한국어)에서 부동산 관련 기사를 모아 후보를 만들고,
중복(history)·차단 도메인을 걸러 대표 기사 1건을 고른다.
선정된 기사는 원문 URL로 리다이렉트를 따라가 본문 요약과 대표 이미지를 확보한다.
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
    NEWS_QUERIES,
    NEWS_MAX_CANDIDATES,
    NEWS_BLOCK_DOMAINS,
)
from src.collector.history import is_duplicate, load_history
from src.utils.logger import setup_logger

log = setup_logger("news")

RSS_TMPL = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


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
        # 요약 소스2: 본문 <p> 중 한글 포함·충분한 길이 문단(중복 제거)
        seen, paras = set(), []
        for p in soup.find_all("p"):
            t = p.get_text(" ", strip=True)
            if len(t) >= 50 and re.search(r"[가-힣]", t) and t[:30] not in seen:
                seen.add(t[:30])
                paras.append(t)
        body = " ".join(paras[:6])
        art.summary = ((desc + " " if desc else "") + body).strip()[:1500]
    except Exception as e:  # 네트워크/파싱 실패는 후보에서 조용히 스킵 가능
        log.info(f"  enrich 실패({art.source}): {e}")


# 실수요자 직결 핵심 주제(가점) vs 추상·니치 주제(감점) — 조회 부진 원인이 주제 관련성.
_CORE_KW = [
    "집값", "전세", "월세", "전월세", "매매", "아파트", "분양", "청약", "재건축", "재개발",
    "대출", "금리", "세금", "세제", "보유세", "양도세", "종부세", "취득세", "규제", "임대차", "토허",
    "전세사기", "실수요", "내집마련", "신고가", "급등", "폭등", "하락", "전세난", "역전세",
    "공급", "입주", "갭투자", "깡통전세", "분양가", "미분양", "집주인", "세입자",
]
_NICHE_KW = [
    "글로벌", "해외", "도쿄", "일본", "미국", "중국", "유럽", "성과급", "반도체", "삼성전자",
    "하이닉스", "증시", "코스피", "코스닥", "채권", "리츠", "수익형", "환율", "비트코인",
    "가상자산", "코인", "연예", "스타", "배우", "가수",
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
    """제목의 실수요자 관련성 점수(핵심 주제 +2, 니치 주제 -3)."""
    t = title or ""
    core = sum(1 for k in _CORE_KW if k in t)
    niche = sum(1 for k in _NICHE_KW if k in t)
    return core * 2 - niche * 3


def topic_score(title: str) -> int:
    """영상 소재 점수 = 관련성 + 정책 비판 + '가격 폭등 고통' 강가점(대박 패턴 편향)."""
    t = title or ""
    crit = sum(1 for k in _CRIT_KW if k in t)
    pain = sum(1 for k in _PAIN_KW if k in t)
    return relatability_score(t) + crit * 2 + pain * 3


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
    history = load_history()   # 후보마다 재파싱하지 않도록 1회만 읽는다
    # 질의를 전부 돈다. 예전에는 후보가 max_candidates를 넘으면 break 했는데,
    # 첫 질의 하나만으로 70건이 넘어 나머지 9개 질의가 한 번도 쓰이지 않았다.
    # 그 결과 후보 풀이 한 질의에 갇혀, 신선한 기사가 아예 없는 날이 생겼다.
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
            candidates.append(art)
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


def pick_and_enrich(candidates: list[Article], top_n: int = 8) -> Article | None:
    """관련성 높은 순으로 원문 해소하여 본문 확보된 첫 기사를 반환한다."""
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    for art in candidates[:top_n]:
        _resolve_and_enrich(art, session)
        if art.url and not _blocked(art.url) and len(art.summary) >= 80:
            age = _age_days(art.published)
            log.info(f"선정({candidate_score(art)}점 = 소재 {topic_score(art.title)} + "
                     f"신선도 {recency_score(art.published)}, "
                     f"{'날짜불명' if age is None else f'{age:.1f}일 전'}): "
                     f"{art.title} ({art.source})")
            return art
        time.sleep(0.5)
    return candidates[0] if candidates else None
