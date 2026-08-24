"""부동산 뉴스 자동 수집.

Google 뉴스 RSS(한국어)에서 부동산 관련 기사를 모아 후보를 만들고,
중복(history)·차단 도메인을 걸러 대표 기사 1건을 고른다.
선정된 기사는 원문 URL로 리다이렉트를 따라가 본문 요약과 대표 이미지를 확보한다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from config.settings import (
    NEWS_QUERIES,
    NEWS_MAX_CANDIDATES,
    NEWS_BLOCK_DOMAINS,
)
from src.collector.history import is_duplicate
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
    url = RSS_TMPL.format(q=quote(query))
    feed = feedparser.parse(url)
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
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            art.image_url = og["content"]
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


def relatability_score(title: str) -> int:
    """제목의 실수요자 관련성 점수(핵심 주제 +2, 니치 주제 -3)."""
    t = title or ""
    core = sum(1 for k in _CORE_KW if k in t)
    niche = sum(1 for k in _NICHE_KW if k in t)
    return core * 2 - niche * 3


def topic_score(title: str) -> int:
    """영상 소재 점수 = 관련성 + 정책 비판 신호(가점). 인기 패턴에 맞춰 선별."""
    t = title or ""
    crit = sum(1 for k in _CRIT_KW if k in t)
    return relatability_score(t) + crit * 2


def collect(max_candidates: int = NEWS_MAX_CANDIDATES) -> list[Article]:
    """부동산 뉴스 후보를 수집한다(중복·차단 제외, 관련성순 정렬)."""
    seen_titles: set[str] = set()
    candidates: list[Article] = []
    for q in NEWS_QUERIES:
        for art in _fetch_rss(q):
            key = art.title[:30]
            if not art.title or key in seen_titles:
                continue
            if _blocked(art.google_url):
                continue
            if is_duplicate(art.title):
                continue
            seen_titles.add(key)
            candidates.append(art)
        if len(candidates) >= max_candidates:
            break
    # 관련성 + 정책 비판 신호로 정렬(좋은 소재 자동 선별)
    candidates.sort(key=lambda a: topic_score(a.title), reverse=True)
    if candidates:
        log.info(f"수집 {len(candidates)}건 · 최상위 소재점수 "
                 f"{topic_score(candidates[0].title)} ({candidates[0].title[:24]})")
    return candidates[:max_candidates]


def pick_and_enrich(candidates: list[Article], top_n: int = 8) -> Article | None:
    """관련성 높은 순으로 원문 해소하여 본문 확보된 첫 기사를 반환한다."""
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    for art in candidates[:top_n]:
        _resolve_and_enrich(art, session)
        if art.url and not _blocked(art.url) and len(art.summary) >= 80:
            log.info(f"선정(소재점수 {topic_score(art.title)}): {art.title} ({art.source})")
            return art
        time.sleep(0.5)
    return candidates[0] if candidates else None
