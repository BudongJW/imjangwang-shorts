"""숫자 스탯 콜아웃 — 대본의 핵심 수치를 화면 중앙에 큼직하게 띄운다.

영상 중앙 빈 공간을 채우고, 부동산 뉴스의 '숫자 임팩트'를 시각화한다.
방향(상승/하락)에 따라 색·화살표를 달리해 직관적으로 보이게 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import SHORTS_WIDTH, SHORTS_HEIGHT, VIDEO_DIR
from src.editor.fonts import font_bold

RED = (232, 50, 50)      # 상승
BLUE = (46, 130, 235)    # 하락
YELLOW = (255, 214, 10)  # 중립
DARK = (16, 16, 20)

# 강한 수치(단위 포함)만 콜아웃 대상. 단위를 넓힌다 — 예전 목록에는 천·명·
# 건·원·주·개월·제곱미터가 없어서 "200명", "3천 건", "84제곱미터"가 통째로
# 빠졌다.
_STAT_RE = re.compile(
    r"(\d[\d,\.]*)\s?"
    r"(%p|%|퍼센트포인트|퍼센트|포인트|억원|억|만원|만|천|원|배|채|가구|세대|"
    r"호|실|동|곳|명|건|위|조|평|제곱미터|㎡|개월|주|년|일|개)")
# 연도는 수치가 아니라 날짜다. "2024년"을 화면 한복판에 210px로 띄우면
# 숫자 임팩트가 아니라 잡음이 된다(2026-09-14 실측에서 2회 잡혔다).
_YEAR_RE = re.compile(r"^(1[89]\d{2}|20\d{2})년$")
# 같은 구절에 여러 수치가 있으면 '센' 쪽을 띄운다. 기간(5년·2주·3개월)은
# 대개 기준일 뿐 임팩트가 아니다 — "지난 5년 평균 대비"의 5년을 화면
# 한복판에 띄워 봐야 시청자에게 남는 게 없다(2026-09-14 실측).
_WEAK_UNITS = ("년", "주", "개월", "일", "개")
_UP = ["오르", "상승", "폭등", "급등", "최고", "신고가", "뛰", "올라", "증가", "늘"]
_DOWN = ["하락", "급락", "폭락", "내리", "줄", "감소", "떨어", "최저", "급감"]


# "2만 3천 가구"처럼 수가 여러 토막으로 이어지는 경우. 첫 조각만 집으면
# "2만"이 화면에 뜨는데, 실제 값은 2만 3천이라 숫자를 틀리게 보여주는 셈이다
# (2026-09-14 검증 프레임 16초 지점).
_CONT_RE = re.compile(
    r"\s?(\d[\d,\.]*\s?(?:%|억원|억|만원|만|천|원|가구|세대|호|명|건|채|평|㎡))")


_TAIL_UNIT_RE = re.compile(r"\s?(?:가구|세대|명|건|채|호|원|평|㎡|개)")


def _extend(phrase: str, m: re.Match) -> str:
    """이어지는 수 조각을 흡수해 온전한 수치로 만든다."""
    out, pos = m.group(0), m.end()
    while True:
        nxt = _CONT_RE.match(phrase, pos)
        if not nxt:
            break
        out += nxt.group(0)
        pos = nxt.end()
    # "2만 3천"처럼 자릿수 단위로 끝났으면 뒤따르는 조수사를 마저 붙인다.
    # 자릿수로 끝날 때만 본다 — 그러지 않으면 "5년 평균"의 '평'까지 붙는다.
    if out.rstrip().endswith(("만", "천", "억", "조")):
        tail = _TAIL_UNIT_RE.match(phrase, pos)
        if tail:
            out += tail.group(0)
    return out.strip()


def is_weak(big: str) -> bool:
    """기간처럼 임팩트가 약한 수치인지."""
    return big.endswith(_WEAK_UNITS)


# 대본 후반은 해석·결론이라 숫자가 없다. 기사의 숫자는 실거래가 문단에
# 몰려 있고, 없는 숫자를 지어내라고 할 수는 없다(규칙 17). 그래서 후반
# 구간은 숫자 대신 대본에 실제로 나온 핵심어를 띄운다. 화면을 채우면서
# 없는 사실을 만들지 않는 유일한 방법이다.
# (2026-09-15 실측: 52초 영상에서 수치 6개가 전부 14.2~25.8초에 있었다)
_KEY_TERMS = (
    "풍선효과", "공급 부족", "공급부족", "전세난", "역전세", "거래절벽", "미분양",
    "갭투자", "깡통전세", "전세사기", "신고가", "규제 완화", "규제완화",
    "대출 규제", "대출규제", "임대차 3법", "임대차3법", "재건축", "재개발",
    "분양가상한제", "토지거래허가", "보유세", "종부세", "양도세", "취득세",
    "특별공급", "청약통장", "실거주 의무", "입주 가뭄", "월세화",
    # 2026-09-16 추가. 오피스텔 월세 기사에서 구절 31개 중 핵심어가 단 1개만
    # 잡혀 후반 25초가 비었다. 실제 대본에 나오는 개념어를 채운다.
    "입주 물량", "입주물량", "공급 절벽", "공급절벽", "매물 잠김", "매물잠김",
    "전월세 전환율", "전월세전환율", "전세수급지수", "실수요자", "1인 가구",
    "시니어타운", "다운사이징", "기준금리", "금리 인상", "임대료", "보증금",
    "주거비", "분양가", "갱신청구권", "계약갱신",
    # 마지막 보루. 이 채널이 매번 다루는 개념이라 카드로 띄워도 말이 된다.
    # 긴 항목이 먼저 매칭되므로 "공급 부족"이 있으면 "공급"은 안 뜬다.
    "공급", "수요", "규제", "세금",
)


def pick_keyword(phrase: str) -> str | None:
    """구절에 실제로 나온 핵심어 1개. 없으면 None.

    긴 것부터 찾는다 — "공급 부족"이 있는데 "공급"만 띄우면 뜻이 달라진다.
    """
    for term in sorted(_KEY_TERMS, key=len, reverse=True):
        if term in phrase:
            return term
    return None


def pick_stat(phrase: str) -> tuple[str, str] | None:
    """구절에서 대표 수치 1개와 방향(up/down/flat)을 뽑는다. 없으면 None.

    연도는 제외하고, 강한 단위(%·억·가구·명 …)를 기간 단위보다 우선한다.
    """
    best, weak = None, None
    for m in _STAT_RE.finditer(phrase):
        cand = _extend(phrase, m)
        if _YEAR_RE.match(cand.replace(",", "")):
            continue
        if cand.endswith(_WEAK_UNITS):
            weak = weak or cand
            continue
        # %가 가장 날카롭다. 같은 구절에 %와 다른 단위가 같이 있으면 %를 쓴다.
        if best is None or (cand.endswith("%") and not best.endswith("%")):
            best = cand
    cand = best or weak
    if not cand:
        return None
    big = cand.replace("퍼센트", "%").replace("만원", "만").replace("제곱미터", "㎡")
    direction = "flat"
    if any(k in phrase for k in _UP):
        direction = "up"
    elif any(k in phrase for k in _DOWN):
        direction = "down"
    return big, direction


def _rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render_stat_card(big: str, direction: str, out: Path) -> Path:
    """화면 중앙에 수치 콜아웃을 렌더한 전체 투명 PNG."""
    img = Image.new("RGBA", (SHORTS_WIDTH, SHORTS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = {"up": RED, "down": BLUE}.get(direction, YELLOW)

    # 숫자는 짧아 210px로 충분하지만 핵심어("공급 부족")는 넘친다.
    # 패널 좌우 여백까지 고려해 폭 안에 들어올 때까지 줄인다.
    max_w = SHORTS_WIDTH - 200
    size = 210
    while size > 70:
        font = ImageFont.truetype(font_bold(), size)
        bbox = draw.textbbox((0, 0), big, font=font, stroke_width=10)
        if bbox[2] - bbox[0] <= max_w:
            break
        size -= 10
    bbox = draw.textbbox((0, 0), big, font=font, stroke_width=10)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    cx, cy = SHORTS_WIDTH // 2, int(SHORTS_HEIGHT * 0.46)
    pad_x, pad_y = 90, 70
    panel = [cx - tw // 2 - pad_x, cy - th // 2 - pad_y,
             cx + tw // 2 + pad_x, cy + th // 2 + pad_y]
    _rounded(draw, panel, 48, (10, 10, 14, 205))
    # 상단 컬러 액센트 바
    _rounded(draw, [panel[0], panel[1], panel[2], panel[1] + 16], 8, color + (255,))

    # 화살표(상승/하락)
    if direction in ("up", "down"):
        ax = panel[2] - 40
        ay = panel[1] - 30
        if direction == "up":
            draw.polygon([(ax, ay - 70), (ax - 55, ay + 20), (ax + 55, ay + 20)], fill=color)
        else:
            draw.polygon([(ax, ay + 90), (ax - 55, ay), (ax + 55, ay)], fill=color)

    # 큰 수치
    draw.text((cx, cy), big, font=font, fill=color + (255,),
              anchor="mm", stroke_width=10, stroke_fill=DARK + (255,))

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
