"""영상용 배경 이미지 수집.

개선안 ②·③에 맞춰 '실제 뉴스 캡처 중심'을 보조할 b-roll 이미지를 모은다.
소스 우선순위: Pexels(키 있을 때) → 그라디언트 생성. 기사 대표이미지는 쓰지 않는다.
반환 이미지는 모두 세로(1080x1920)로 크롭·리사이즈된 PNG.
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path

import re
import requests
from PIL import Image, ImageDraw, ImageFilter

from config.settings import (
    SHORTS_WIDTH,
    SHORTS_HEIGHT,
    VIDEO_DIR,
    PEXELS_API_KEY,
)
from src.utils.buildnotes import note
from src.utils.logger import setup_logger

log = setup_logger("images")

UA = {"User-Agent": "Mozilla/5.0"}
# 부동산 톤 그라디언트 팔레트(어두운 남색~차분한 톤)
GRADIENTS = [
    ((16, 24, 48), (40, 58, 96)),
    ((28, 20, 20), (70, 42, 42)),
    ((18, 34, 40), (34, 70, 78)),
    ((30, 28, 44), (66, 58, 96)),
]


def _to_portrait(im: Image.Image) -> Image.Image:
    """이미지를 1080x1920로 커버 크롭."""
    im = im.convert("RGB")
    tw, th = SHORTS_WIDTH, SHORTS_HEIGHT
    ratio = max(tw / im.width, th / im.height)
    nw, nh = int(im.width * ratio), int(im.height * ratio)
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def _download(url: str) -> Image.Image | None:
    try:
        r = requests.get(url, headers=UA, timeout=12)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content))
    except Exception as e:
        log.info(f"  이미지 다운로드 실패: {e}")
        return None


def _draw_skyline(base: Image.Image, idx: int) -> None:
    """하단에 야경 도시 스카이라인(실루엣 건물 + 창문 불빛)을 그린다."""
    rnd = random.Random(idx * 7 + 13)
    draw = ImageDraw.Draw(base, "RGBA")
    W, H = base.size
    x = -20
    while x < W + 20:
        bw = rnd.randint(70, 150)
        bh = rnd.randint(int(H * 0.14), int(H * 0.38))
        top = H - bh
        shade = rnd.randint(6, 20)
        draw.rectangle([x, top, x + bw, H], fill=(shade, shade, shade + 10, 240))
        # 창문 불빛
        for wy in range(top + 20, H - 24, 36):
            for wx in range(x + 14, x + bw - 12, 28):
                if rnd.random() < 0.26:
                    c = rnd.choice([(255, 214, 120), (255, 236, 180), (170, 195, 255)])
                    a = rnd.randint(120, 210)
                    draw.rectangle([wx, wy, wx + 9, wy + 13], fill=c + (a,))
        x += bw + rnd.randint(-6, 12)


def _gradient(idx: int) -> Image.Image:
    c1, c2 = GRADIENTS[idx % len(GRADIENTS)]
    base = Image.new("RGB", (SHORTS_WIDTH, SHORTS_HEIGHT))
    top = Image.new("RGB", (1, SHORTS_HEIGHT))
    for y in range(SHORTS_HEIGHT):
        t = y / SHORTS_HEIGHT
        top.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    base = top.resize((SHORTS_WIDTH, SHORTS_HEIGHT))
    _draw_skyline(base, idx)   # 도시 스카이라인 실루엣 + 창문 불빛
    # 은은한 비네트
    v = Image.new("L", (SHORTS_WIDTH, SHORTS_HEIGHT), 0)
    dv = ImageDraw.Draw(v)
    dv.ellipse([-200, -300, SHORTS_WIDTH + 200, SHORTS_HEIGHT + 300], fill=60)
    v = v.filter(ImageFilter.GaussianBlur(180))
    base = Image.composite(base, Image.new("RGB", base.size, (0, 0, 0)), v.point(lambda x: 255 - x))
    return base


# 부동산 화면에 어울리지 않는 스톡 결과. 검색어가 도시·아파트여도
# Pexels는 가끔 엉뚱한 것을 섞는다(09-29 대학가 원룸 영상에 금불상이
# 3초 떴다). 사진은 alt, 영상은 페이지 주소 슬러그에 설명이 있다.
# 검색을 locale=ko-KR로 해서 alt가 한국어로 온다. 영어 단어만 보던 첫
# 버전은 같은 날 금불상 컷을 또 통과시켰다.
_OFFTOPIC_RE = re.compile(
    r"buddha|statue|sculpture|temple|shrine|church|cathedral|mosque|religio|"
    r"monk|pray|candle|portrait|selfie|food|dish|meal|cat\b|dog\b|pet\b|"
    r"flower|bouquet|wedding|bikini|model\b|"
    r"불상|부처|불교|사찰|사원|성당|교회|성전|동상|조각상|기도|승려|스님|양초|촛불|"
    r"음식|요리|고양이|강아지|반려|꽃다발|웨딩|결혼|셀카|초상|"
    # 10-08 LH 영상: "bank loan documents"로 너구리, 서양인 남성, 연체 독촉장이
    # 왔다. 서양인 얼굴은 "이성훈 LH 사장이" 자막 밑에 떠서 본인처럼 보였다.
    # 실명이 나오는 영상이 많아 한 사람이 찍힌 사진은 아예 뺀다.
    r"raccoon|wildlife|animal|squirrel|bird\b|너구리|동물|야생|다람쥐|조류|"
    r"\bman\b|\bwoman\b|businessman|businesswoman|남성|여성|남자|여자|"
    r"past\s?due|overdue|bankrupt|foreclos|연체|파산|독촉", re.I)


# 한국 부동산 영상에 외국 도시가 뜨면 안 된다. 검색어에 seoul·korean을
# 붙여도 Pexels는 비슷한 아시아 도시를 섞는다(10-08 "korean apartment
# complex"가 베이징 아파트를 가져왔다). alt에 나라·도시가 적혀 오니 거른다.
_FOREIGN_RE = re.compile(
    r"\b(?:china|chinese|beijing|shanghai|shenzhen|guangzhou|hong\s?kong|japan|tokyo|"
    r"osaka|vietnam|hanoi|saigon|bangkok|thailand|singapore|taipei|taiwan|dubai|"
    r"new\s?york|manhattan|london|paris|malaysia|kuala|manila|jakarta)|"
    r"중국|베이징|상하이|광저우|홍콩|일본|도쿄|오사카|베트남|하노이|호찌민|호치민|"
    r"태국|방콕|싱가포르|대만|타이베이|두바이|뉴욕|맨해튼|런던|말레이시아|"
    r"쿠알라|필리핀|마닐라|인도네시아|자카르타", re.I)
# 눈 덮인 겨울 사진은 겨울에만 쓴다(10-08 영상에 눈 쌓인 단지가 떴다).
_WINTER_RE = re.compile(r"눈\s?(?:으로\s?)?덮인|눈이\s?(?:내리|쌓인|덮)|설경|겨울|snow|winter", re.I)


def _offtopic(meta: dict) -> bool:
    text = f"{meta.get('alt') or ''} {meta.get('url') or ''}".replace("-", " ")
    if _OFFTOPIC_RE.search(text) or _FOREIGN_RE.search(text):
        return True
    from datetime import datetime, timezone, timedelta
    month = datetime.now(timezone(timedelta(hours=9))).month
    return month not in (12, 1, 2) and bool(_WINTER_RE.search(text))


# 이 영상에서 쓴 스톡 사진·영상 ID. 토픽 기록에 남겨 다음 영상들이 피한다.
USED_MEDIA: list[str] = []
# 설명에 나라가 안 적혀 위 필터를 지나간 외국 컷. 보이는 대로 여기 더한다.
# v34435020: 이스탄불 고속도로(터키어 표지판), 10-08 두 초안에 연달아 떴다.
# p30764160: 지붕 위 판잣집 항공 사진, 10-08 초안.
# p20111013: 눈 덮인 가평 리조트(설명에 눈이 없다), 10-08 초안.
# v19327271: 일본 주택가 골목, 10-08 LH 영상.
BANNED_MEDIA = {"v34435020", "p30764160", "p20111013", "v19327271"}
# 최근 몇 편과 겹치지 않게 할지. 하루 3편 안팎이라 닷새치다.
MEDIA_AVOID_RECENT = 15


def _recent_media() -> set[str]:
    """최근 영상들이 쓴 스톡 ID. 10-01 하루 세 편이 같은 사진을 그대로 썼다."""
    try:
        from src.collector.history import load_history
        ids: set[str] = set()
        for e in load_history()[-MEDIA_AVOID_RECENT:]:
            ids.update(str(x) for x in (e.get("media") or []))
        return ids
    except Exception:
        return set()


def _pexels(query: str, n: int, page: int = 1) -> list[Image.Image]:
    if not PEXELS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": min(40, n * 4), "page": page,
                    "orientation": "portrait", "locale": "ko-KR"},
            timeout=12,
        )
        r.raise_for_status()
        imgs, kept = [], []
        avoid = _recent_media() | set(USED_MEDIA) | BANNED_MEDIA
        for photo in r.json().get("photos", []):
            pid = f"p{photo.get('id')}"
            if pid in avoid:
                continue
            if _offtopic(photo):
                note(f"배경 사진 제외(주제 밖): {(photo.get('alt') or '')[:40]}")
                continue
            im = _download(photo["src"]["large2x"])
            if im:
                imgs.append(im)
                USED_MEDIA.append(pid)
                kept.append((photo.get("alt") or str(photo.get("id", "")))[:30])
            if len(imgs) >= n:
                break
        # 어떤 사진이 들어갔는지 남긴다. 엉뚱한 컷이 떴을 때 alt로 원인을 본다.
        note(f"배경 사진({query}): " + " | ".join(kept))
        return imgs
    except Exception as e:
        log.info(f"  Pexels 실패: {e}")
        return []


# 검색어를 고정하면 Pexels가 매번 같은 사진군을 준다. 날짜별로 돌려
# 배경이 겹치지 않게 한다(썸네일 구도·얼굴 크롭 회전과 같은 방식).
PEXELS_QUERIES = (
    "seoul apartment building",
    "korean city skyline night",
    "apartment construction site",
    "seoul street rain",
    "high rise apartment window",
    "moving boxes empty room",
    "real estate agency window",
    "han river apartment aerial",
)


# 기사 낱말로 고르는 검색어. 날짜로만 돌리면 같은 날 영상이 모두 같은
# 사진을 쓴다(10-01 청년 전세대출·LH 대출규제·오피스텔 세 편이 비 오는
# 거리와 경찰버스 사진을 똑같이 썼다). 말하는 내용과도 상관이 없었다.
# 위에 있을수록 구체적이다. 영어로 찾는 게 Pexels 결과가 훨씬 많다.
# 건물 바깥을 찾는 말에는 seoul·korean을 붙인다. 10-05 지정 영상에서
# "officetel building"이 베트남 하노이 거리(현지어 간판)를 가져왔다.
# 열쇠·서류·상자처럼 실내·물건 사진은 나라가 드러나지 않아 그대로 둔다.
_TOPICS: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (re.compile(r"오피스텔|아파텔"), ("seoul residential tower", "studio apartment interior",
                                  "seoul high rise apartments")),
    (re.compile(r"미분양"), ("empty new apartment", "korean apartment complex")),
    (re.compile(r"청약|(?<!미)분양|견본주택|모델하우스"), ("model house interior", "korean apartment complex",
                                          "apartment sales office")),
    (re.compile(r"재건축|재개발|정비사업"), ("old korean apartment", "demolition site",
                                     "apartment construction crane")),
    (re.compile(r"원룸|대학가|기숙사"), ("small studio room", "seoul university street")),
    (re.compile(r"빌라|다가구|다세대|비아파트"), ("seoul residential alley",
                                         "korean residential alley")),
    (re.compile(r"전세|월세|임대차|세입자|임차|보증금|집주인"), ("apartment keys hand", "moving boxes empty room",
                                                  "apartment door hallway")),
    (re.compile(r"대출|LTV|DSR|금리|보금자리론|디딤돌|은행"), ("calculator house model", "signing contract desk",
                                                  "seoul apartment buildings")),
    (re.compile(r"세금|보유세|종부세|양도세|취득세|과세"), ("tax documents calculator", "paperwork desk calculator")),
    (re.compile(r"공급|착공|입주|건설|공사비|인허가"), ("apartment construction crane", "korean construction site")),
    (re.compile(r"한강|강남|서초|송파|용산|마포|성동"), ("han river apartment aerial", "seoul apartment buildings")),
    (re.compile(r"대구|부산|울산|광주|대전|경북|경남|충남|충북|전북|전남|강원|지방"),
     ("korean city apartment complex", "korean apartment blocks")),
    (re.compile(r"서울|수도권|경기|인천"), ("seoul apartment buildings", "seoul city skyline")),
]


_REGION_FROM = 10   # _TOPICS에서 이 번호부터는 지역


def topic_queries(text: str, seed: int = 0, k: int = 3) -> list[str]:
    """기사·대본에 많이 나온 주제 순서로 검색어 k개. 모자라면 날짜 회전으로 채운다.

    같은 주제라도 seed(기사마다 다름)로 검색어를 돌려 영상끼리 겹치지 않게 한다.
    """
    text = text or ""
    scored = []
    for i, (rx, qs) in enumerate(_TOPICS):
        hits = len(rx.findall(text))
        # 지역은 주제보다 덜 친다. '서울 오피스텔'이면 오피스텔 사진이 먼저다.
        if i >= _REGION_FROM:
            hits *= 0.5
        if hits:
            scored.append((-hits, i, qs))
    scored.sort()
    out: list[str] = []
    for _, _, qs in scored:
        q = qs[seed % len(qs)]
        if q not in out:
            out.append(q)
        if len(out) >= k:
            break
    j = 0
    while len(out) < k:
        q = _today_query(seed + j)
        if q not in out:
            out.append(q)
        j += 1
    return out


def _today_ordinal() -> int:
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9))).date().toordinal()


def _today_query(offset: int = 0) -> str:
    return PEXELS_QUERIES[(_today_ordinal() + offset) % len(PEXELS_QUERIES)]


def _today_page() -> int:
    """검색어가 한 바퀴(8일) 돌 때마다 결과 페이지를 넘긴다.

    페이지를 고정하면 8일마다 똑같은 사진이 똑같은 순서로 다시 나온다.
    """
    return (_today_ordinal() // len(PEXELS_QUERIES)) % 5 + 1


def _luma(im: Image.Image) -> float:
    return sum(im.convert("L").resize((64, 114)).getdata()) / (64 * 114)


# 첫 컷이 이보다 어두우면 가장 밝은 사진과 바꾼다. 첫 프레임은 넘길지
# 말지를 정하는 화면인데, 09-25분은 화면 절반이 검은 창틀로 시작했다.
DARK_OPEN_LUMA = 80


def collect_backgrounds(article_image_url: str = "", need: int = 3,
                        query: str = "", queries: list[str] | None = None,
                        seed: int = 0) -> list[Path]:
    """b-roll 배경 이미지 need개를 확보해 파일로 저장하고 경로 리스트 반환.

    queries를 주면(기사 주제 검색어) 앞의 두 개에서 반씩 받는다.
    """
    query = query or (queries[0] if queries else _today_query())
    second = queries[1] if queries and len(queries) > 1 else _today_query(1)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    pool: list[Image.Image] = []

    # 기사 대표이미지(og:image)는 배경으로 쓰지 않는다. 대부분 언론사
    # 보도사진이라 기사 캡처에서 저작권 때문에 걷어내는 바로 그 사진이다
    # (article_capture._STRIP_PHOTO_JS). 배경의 첫 장은 첫 화면이자 끝 화면
    # (반복 재생 이음새)이라 캡처보다 더 오래, 화면 가득 뜬다.
    # 10-04 초안: 뉴시스 대표사진이 다른 유튜브 채널(월급쟁이부자들TV) 출연
    # 화면 캡처였고, 출연자 얼굴이 첫 화면과 끝 화면에 그대로 떴다.
    # article_image_url 인자는 호출부 호환을 위해 남겨 둔다.

    # 한 검색어에서 다 받으면 같은 촬영자의 비슷한 사진이 연달아 나온다.
    # 오늘 검색어와 다음 검색어에서 반씩 받는다.
    if len(pool) < need:
        page = (_today_page() + seed) % 3 + 1
        first = (need - len(pool) + 1) // 2
        pool += _pexels(query, first, page)
        if len(pool) < need:
            pool += _pexels(second, need - len(pool), page)
        if len(pool) < need:
            # 주제 검색어가 결과를 덜 줬으면 날짜 회전 검색어로 채운다.
            pool += _pexels(_today_query(seed), need - len(pool), 1)

    # 폴백 그라디언트도 날짜를 시작점으로 — 소스가 전부 실패한 날에도
    # 최소한 어제와 같은 색은 피한다.
    from datetime import datetime, timezone, timedelta
    idx = datetime.now(timezone(timedelta(hours=9))).date().toordinal()
    while len(pool) < need:
        pool.append(_gradient(idx))
        idx += 1

    frames = [_to_portrait(im) for im in pool[:need]]
    if len(frames) > 1 and _luma(frames[0]) < DARK_OPEN_LUMA:
        j = max(range(len(frames)), key=lambda k: _luma(frames[k]))
        frames[0], frames[j] = frames[j], frames[0]
    paths = []
    for i, im in enumerate(frames):
        p = VIDEO_DIR / f"bg_{i:02d}.png"
        im.save(p)
        paths.append(p)
    log.info(f"  배경 이미지 {len(paths)}개 확보")
    return paths


# ── 동영상 b-roll ──────────────────────────────────────────────
#
# "정적 이미지 루프"는 유튜브가 AI 양산 채널을 가려내는 지표로 직접 지목한
# 형태다(2026년 정리된 채널들의 공통 지문: 합성 나레이션 · 템플릿 썸네일 ·
# 정적 이미지 루프 · 비인간적 업로드 속도). 지금 배경은 사진에 켄번즈만
# 걸어 둔 것이라 정확히 그 모양이다.
#
# Pexels 영상은 사진과 같은 라이선스라 상업적 이용이 되고 출처 표기 의무도
# 없다. 키도 이미 등록돼 있다(PEXELS_API_KEY).
def _pexels_videos(query: str, n: int) -> list[str]:
    """세로 영상 링크 n개. 실패하면 빈 리스트."""
    if not PEXELS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": max(n * 4, 12),
                    "orientation": "portrait", "size": "medium"},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        log.info(f"  Pexels 영상 실패: {e}")
        return []

    links: list[str] = []
    slugs: list[str] = []
    avoid = _recent_media() | set(USED_MEDIA) | BANNED_MEDIA
    for vid in r.json().get("videos", []):
        vkey = f"v{vid.get('id')}"
        if vkey in avoid:
            continue
        # 너무 짧으면 컷 하나도 못 채우고, 너무 길면 내려받는 시간이 아깝다.
        if not (3 <= (vid.get("duration") or 0) <= 60):
            continue
        if _offtopic(vid):
            note(f"b-roll 제외(주제 밖): {(vid.get('url') or '')[-50:]}")
            continue
        best, best_h = None, 0
        for f in vid.get("video_files", []):
            w, h = f.get("width") or 0, f.get("height") or 0
            if f.get("file_type") != "video/mp4" or not w or not h:
                continue
            if w >= h:                      # 가로 영상은 크롭하면 다 잘린다
                continue
            if h > best_h and h <= 1920:    # 1080x1920 넘게 받을 이유가 없다
                best, best_h = f.get("link"), h
        if best:
            links.append(best)
            USED_MEDIA.append(vkey)
            slugs.append((vid.get("url") or "").rstrip("/").rsplit("/", 1)[-1][:40])
        if len(links) >= n:
            break
    note(f"b-roll({query}): " + " | ".join(slugs))
    return links


def collect_video_broll(query: str = "", need: int = 2) -> list[Path]:
    """세로 b-roll 영상 need개를 내려받아 경로 리스트 반환. 실패 시 []."""
    # 사진(오늘·다음 검색어)과 겹치지 않게 그다음 검색어를 쓴다.
    query = query or _today_query(2)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    links = _pexels_videos(query, need)
    if len(links) < need and query != _today_query(2):
        # 주제 검색어로 세로 영상이 모자라면 날짜 회전 검색어로 채운다.
        links += _pexels_videos(_today_query(2), need - len(links))
    for i, link in enumerate(links):
        dst = VIDEO_DIR / f"broll_{i}.mp4"
        try:
            with requests.get(link, headers=UA, timeout=30, stream=True) as resp:
                resp.raise_for_status()
                with open(dst, "wb") as f:
                    for chunk in resp.iter_content(1 << 16):
                        f.write(chunk)
        except Exception as e:
            log.info(f"  b-roll 내려받기 실패: {e}")
            continue
        if dst.exists() and dst.stat().st_size > 50_000:
            out.append(dst)
    log.info(f"  b-roll 영상 {len(out)}개 ('{query}')")
    return out
