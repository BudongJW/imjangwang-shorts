"""숫자 스탯 콜아웃 — 대본의 핵심 수치를 화면 중앙에 큼직하게 띄운다.

영상 중앙 빈 공간을 채우고, 부동산 뉴스의 '숫자 임팩트'를 시각화한다.
방향(상승/하락)에 따라 색·화살표를 달리해 직관적으로 보이게 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import SHORTS_WIDTH, SHORTS_HEIGHT, VIDEO_DIR
from src.editor.fonts import font_bold, fix_glyphs

RED = (232, 50, 50)      # 상승
BLUE = (46, 130, 235)    # 하락
YELLOW = (255, 214, 10)  # 중립
DARK = (16, 16, 20)

# 강한 수치(단위 포함)만 콜아웃 대상. 단위를 넓힌다 — 예전 목록에는 천·명·
# 건·원·주·개월·제곱미터가 없어서 "200명", "3천 건", "84제곱미터"가 통째로
# 빠졌다.
_STAT_RE = re.compile(
    r"(\d[\d,\.]*)\s?"
    r"(%p|%|퍼센트포인트|퍼센트|포인트|억원|억|만원|만|천|원|배|채|가구|세대(?!\s*\d?\s*주택)|"
    r"세(?![대금입율])|호|실|동|곳|명|건|위|조|평|제곱미터|㎡|개월|주(?!택)|년|일|개\s?층|층|개)")
# 연도는 수치가 아니라 날짜다. "2024년"을 화면 한복판에 210px로 띄우면
# 숫자 임팩트가 아니라 잡음이 된다(2026-09-14 실측에서 2회 잡혔다).
_YEAR_RE = re.compile(r"^(1[89]\d{2}|20\d{2})년$")
_RANGE_HEAD_RE = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)\s?[~∼～]\s?$")
# 같은 구절에 여러 수치가 있으면 '센' 쪽을 띄운다. 기간(5년·2주·3개월)은
# 대개 기준일 뿐 임팩트가 아니다 — "지난 5년 평균 대비"의 5년을 화면
# 한복판에 띄워 봐야 시청자에게 남는 게 없다(2026-09-14 실측).
# 면적(84㎡·30평)도 같다. 기사의 주인공이 아니라 대상 설명이다. 같은
# 구절에 "12억"이 있으면 그쪽을 띄운다(09-25 검증: "60" 콜아웃).
# 나이(만 39세)도 대상 설명이다. 구절에 금액이 같이 있으면 금액을 띄우고,
# 나이만 있을 때 띄운다(09-30 청년 전세대출 보증 영상: 핵심 수치가 나이였다).
_WEAK_UNITS = ("년", "주", "개월", "일", "개", "제곱미터", "㎡", "m²", "평", "세")
_UP = ["오르", "올랐", "상승", "폭등", "급등", "최고", "신고가", "뛰", "올라", "증가", "늘"]
_DOWN = ["하락", "급락", "폭락", "내리", "줄", "감소", "떨어", "최저", "급감"]


# "2만 3천 가구"처럼 수가 여러 토막으로 이어지는 경우. 첫 조각만 집으면
# "2만"이 화면에 뜨는데, 실제 값은 2만 3천이라 숫자를 틀리게 보여주는 셈이다
# (2026-09-14 검증 프레임 16초 지점).
_CONT_RE = re.compile(
    r"\s?(\d[\d,\.]*\s?(?:%|억원|억|천만원|천만|만원|만|천|원|가구|세대|호|명|건|채|평|㎡))")


_TAIL_UNIT_RE = re.compile(r"\s?(?:가구|세대|명|건|채|호|원|평|㎡|개)")
# "3년 3개월"은 한 기간이다. 앞 토막만 띄우면 "3년"이 돼 사실과 달라진다
# (09-28 검증: 최장 3년 3개월 유예가 "3년"으로 떴다).
_MONTH_TAIL_RE = re.compile(r"\s?\d+\s?개월")
_MONTH_BEFORE_RE = re.compile(r"\d{1,2}\s?월\s?$")
_FROM_RE = re.compile(r"\s*(?:이하|이상|미만|초과)?\s*에서")
_BASIS_RE = re.compile(r"\s*(?:원|짜리)?\s*기준")
_DAY_SPAN_RE = re.compile(r"\s?(?:만|동안|간|이내|안에|째|치)")


def _extend(phrase: str, m: re.Match) -> str:
    """이어지는 수 조각을 흡수해 온전한 수치로 만든다."""
    out, pos = m.group(0), m.end()
    # 연도에서 시작하면 잇지 않는다. "2021년 3130만"이 한 덩어리로 떴다(09-27).
    if _YEAR_RE.match(out.replace(",", "").strip()):
        return out.strip()
    if out.rstrip().endswith("년"):
        mo = _MONTH_TAIL_RE.match(phrase, pos)
        return (out + mo.group(0)).strip() if mo else out.strip()
    while True:
        # 자릿수(조·억·만·천)로 끝날 때만 잇는다. "84㎡ 12억"은 면적과 값이지
        # 한 수가 아니다.
        if not out.rstrip().endswith(("조", "억", "만", "천")):
            break
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


# 다른 낱말 속에 든 핵심어. "전세금으로"의 '세금'을 띄워 전세금 얘기가
# 세금 얘기처럼 보였다(10-01 청년 전세대출 영상 55초 지점).
_NOT_AFTER = {"세금": ("전", "월")}


def pick_keyword(phrase: str) -> str | None:
    """구절에 실제로 나온 핵심어 1개. 없으면 None.

    긴 것부터 찾는다 — "공급 부족"이 있는데 "공급"만 띄우면 뜻이 달라진다.
    """
    for term in sorted(_KEY_TERMS, key=len, reverse=True):
        for m in re.finditer(re.escape(term), phrase):
            if phrase[:m.start()].endswith(_NOT_AFTER.get(term, ())):
                continue
            return term
    return None


def pick_stat(phrase: str) -> tuple[str, str] | None:
    """구절에서 대표 수치 1개와 방향(up/down/flat)을 뽑는다. 없으면 None.

    연도는 제외하고, 강한 단위(%·억·가구·명 …)를 기간 단위보다 우선한다.
    """
    best, weak = None, None
    best_end = weak_end = 0
    for m in _STAT_RE.finditer(phrase):
        cand = _extend(phrase, m)
        if _YEAR_RE.match(cand.replace(",", "")):
            continue
        # 범위는 통째로 띄운다. 10-05 지정 영상: "9~15%", "3~4%대"가 "15%",
        # "4%"로 떠서 다른 숫자처럼 보였다.
        rng = _RANGE_HEAD_RE.search(phrase[:m.start()])
        if rng:
            cand = f"{rng.group(1)}~{cand}"
        # "12월 31일"의 31일도 날짜다. 떼어 띄우면 뜻 없는 숫자가 된다(09-28 검증).
        if cand.endswith("일") and _MONTH_BEFORE_RE.search(phrase[:m.start()]):
            continue
        # "1세대 1주택"은 세제 용어지 가구 수가 아니다. 자막 줄이 "1세대"에서
        # 끊겨도 띄우지 않는다.
        if cand.replace(" ", "") == "1세대":
            continue
        # "29일 국무회의", "다음 달 1일부터"의 일은 날짜다. "30일 만에"처럼
        # 기간일 때만 수치로 본다(09-29 일시적 2주택 영상 제작 중 확인).
        if cand.endswith("일") and not _DAY_SPAN_RE.match(phrase[m.start() + len(cand):]):
            continue
        # "보증금 1000만원 기준"의 1000만은 조건이지 뉴스가 아니다. 크게 띄우면
        # 그게 월세처럼 보인다(09-29 대학가 원룸 영상).
        if _BASIS_RE.match(phrase[m.start() + len(cand):]):
            continue
        # "660㎡ 이하에서 1,000㎡ 미만으로"는 바뀐 뒤 값이 뉴스다. 앞 값을
        # 띄우면 옛 기준을 새 기준처럼 보여준다(09-28 제작 중 확인).
        if cand.endswith(_WEAK_UNITS):
            if weak is None or "에서" in phrase[weak_end:m.start()]:
                weak, weak_end = cand, m.end()
            continue
        # %가 가장 날카롭다. 같은 구절에 %와 다른 단위가 같이 있으면 %를 쓴다.
        if (best is None or (cand.endswith("%") and not best.endswith("%"))
                or ("에서" in phrase[best_end:m.start()]
                    and cand.endswith("%") == best.endswith("%"))):
            best, best_end = cand, m.end()
    cand = best or weak
    if not cand:
        return None
    # 구절이 "3개 층에서"처럼 바뀌기 전 값에서 끝나면 새 값은 다음 줄에 있다.
    # 옛 값만 크게 띄우면 그게 새 기준처럼 보인다.
    if _FROM_RE.match(phrase[best_end if best else weak_end:]):
        return None
    big = cand.replace("퍼센트", "%").replace("제곱미터", "㎡")
    big = fix_glyphs(re.sub(r"만\s?원$", "만", big))
    direction = "flat"
    if any(k in phrase for k in _UP):
        direction = "up"
    elif any(k in phrase for k in _DOWN):
        direction = "down"
    return big, direction


# 카드에 숫자만 크게 뜨면 무슨 숫자인지 모른다(10-08 "1%", "2%", "100%").
# 대본에서 숫자 바로 앞 말을 그대로 따서 위에 작게 단다. 말을 새로 지으면
# 틀릴 수 있어서 대본 글자만 쓴다.
LABEL_MAX_WORDS = 3
LABEL_MAX_CHARS = 14
_LEAD_DROP = {"그리고", "하지만", "특히", "다만", "또", "또한", "결국", "이어",
              "반면", "그러나", "즉"}
# 화살표는 그 숫자가 실제로 오르거나 내렸다고 말할 때만 단다. 10-08 LH 영상의
# "6억 한도를 더 줄여야"는 앞으로 줄이자는 말인데 6억에 내림 화살표가 붙어
# 이미 줄어든 것처럼 보였다. 숫자 바로 뒤 두 어절의 '끝난 변화'만 본다.
_UP_DONE = re.compile(r"올랐|오른|올라|상승했|상승한|상승해|늘었|늘어난|늘어|증가했|증가한|증가해|"
                      r"뛰었|뛴|급등했|급등한|치솟")
_DOWN_DONE = re.compile(r"내렸|내린|하락했|하락한|하락해|줄었|줄어든|줄어|감소했|감소한|감소해|"
                        r"떨어졌|떨어진|급락했|급락한|빠졌|빠진")


def stat_context(sentence: str, big: str, near: int = 0) -> tuple[str, str]:
    """문장에서 big 숫자 앞의 이름표와 방향(up/down/flat)을 찾는다.

    near: 문장 안에서 이 위치 이후의 첫 출현을 쓴다(같은 숫자가 두 번 나올 때).
    """
    m0 = re.match(r"[\d,\.]+", big or "")
    if not m0 or not sentence:
        return "", "flat"
    num = m0.group(0).rstrip(".,")
    rest = big[len(num):].strip()
    # 단위까지 맞춰 찾는다. "85~92%"를 숫자만으로 찾으면 앞의 "85제곱미터"에
    # 걸린다. 범위면 뒤 숫자까지, 아니면 단위 첫 글자까지 본다.
    rng = re.match(r"[~∼～]\s?([\d,\.]+)", rest)
    if rng:
        tail_pat = r"\s?[~∼～]\s?" + re.escape(rng.group(1))
    elif rest:
        u0 = rest[0]
        tail_pat = r"\s?" + {"%": "[%퍼]", "㎡": "[㎡제]"}.get(u0, re.escape(u0))
    else:
        tail_pat = ""
    pat = re.compile(rf"(?<![\d.,]){re.escape(num)}(?![\d]){tail_pat}")
    m = pat.search(sentence, max(0, near)) or pat.search(sentence)
    if not m:
        return "", "flat"
    # 숫자가 든 어절의 시작과 끝
    ws = sentence.rfind(" ", 0, m.start()) + 1
    we = sentence.find(" ", m.end())
    we = len(sentence) if we < 0 else we
    # 이름표: 앞 어절을 거꾸로 모은다. 숫자가 든 어절을 만나면 그 수식이 잘려
    # 뜻이 바뀌므로 이름표를 버린다. "3.3제곱미터당"처럼 단위 기준만 예외.
    words = sentence[:ws].split()
    label: list[str] = []
    for w in reversed(words):
        if len(label) >= LABEL_MAX_WORDS or len(" ".join([w] + label)) > LABEL_MAX_CHARS:
            break
        if w.endswith((",", "，")):
            break
        if re.search(r"\d", w):
            if w.endswith("당") and not label:
                label.insert(0, w)
                break
            label = []
            break
        label.insert(0, w)
    # 앞 숫자에 붙는 말("85제곱미터 이하"의 '이하')로 시작하면 뗀다.
    while label and (label[0] in _LEAD_DROP or len(label[0]) == 1
                     or label[0] in ("이하", "이상", "미만", "초과", "이내", "동안", "정도", "가량")):
        label.pop(0)
    # 방향: 숫자 어절 뒤 두 어절. 숫자가 든 어절이 나오면 거기서 멈춘다.
    after = []
    for w in sentence[we:].split()[:2]:
        if re.search(r"\d", w):
            break
        after.append(w)
    tail = sentence[m.end():we] + " " + " ".join(after)
    direction = "up" if _UP_DONE.search(tail) else "down" if _DOWN_DONE.search(tail) else "flat"
    return " ".join(label), direction


def _rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render_stat_card(big: str, direction: str, out: Path, label: str = "") -> Path:
    """화면 중앙에 수치 콜아웃을 렌더한 전체 투명 PNG. label은 숫자 위 작은 글씨."""
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
    lab_font, lab_h, lab_w = None, 0, 0
    if label:
        lab_font = ImageFont.truetype(font_bold(), 66)
        lb = draw.textbbox((0, 0), label, font=lab_font)
        lab_w, lab_h = lb[2] - lb[0], lb[3] - lb[1]
    half_w = max(tw, lab_w) // 2
    top_extra = lab_h + 34 if label else 0
    panel = [cx - half_w - pad_x, cy - th // 2 - pad_y - top_extra,
             cx + half_w + pad_x, cy + th // 2 + pad_y]
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

    # 이름표
    if label:
        draw.text((cx, panel[1] + 16 + pad_y // 2 + lab_h // 2), label, font=lab_font,
                  fill=(235, 235, 240, 255), anchor="mm")
    # 큰 수치
    draw.text((cx, cy), big, font=font, fill=color + (255,),
              anchor="mm", stroke_width=10, stroke_fill=DARK + (255,))

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
