"""이름표 — 누가 한 말인지, 어디 자료인지, 어느 대목이 이 채널의 해설인지를 화면에 붙인다.

대본은 "윤지해 부동산평론가는 …라고 말했습니다", "한국부동산원에 따르면 …"처럼 말한
사람과 출처를 밝혀 쓴다(대본 규칙 13·15). 그런데 화면에서는 그 문장도 다른 문장과 똑같은
자막 한 줄로 지나갔다. 누구 말인지가 화면에 따로 뜨면 소리 없이 보는 사람도 '이건 이 사람의
말'이라는 걸 알고, 영상마다 같은 틀에 다른 요소가 생긴다(2026-11-25 레퍼런스 조사: WSJ 쇼츠는
말하는 사람·자료 이름이 아래에서 밀려 들어온다).

마지막에 시청자가 확인할 일을 짚는 문장(대본 규칙 7)과 진행자 본인의 읽기(규칙 16의
"저는 …로 봅니다")에는 '임장왕 체크'를 붙인다. 기사를 읽는 대목과 이 채널이 덧붙인 대목이
화면에서 갈린다.

이름표에 쓰는 글자는 모두 대본에 있는 말 그대로다. 여기서 새로 짓는 말은 '발언'·'출처'·
'임장왕 체크' 같은 갈래 이름뿐이다. 누구인지 확실하지 않으면 띄우지 않는다.

끄려면 NAME_TAGS=0 (config/settings.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import SHORTS_WIDTH, SHORTS_HEIGHT
from src.editor.fonts import font_bold, fix_glyphs

YELLOW = (255, 214, 10)
NAVY = (14, 26, 43)
INK = (18, 18, 20)
WHITE = (245, 245, 245)

# 자막 상자(위쪽 끝 약 1343px) 바로 위, 숫자 카드(아래쪽 끝 약 1100px) 아래.
TAG_X = 60
TAG_Y = 1232
TAG_H = 84
TAG_MAX_W = SHORTS_WIDTH - 2 * TAG_X
WHO_MAX_CHARS = 24

# 문장 앞에 붙는 이음말은 이름이 아니다("반면 함영진 우리은행 랩장은").
_LEAD_RE = re.compile(r"^(?:반면|다만|한편|또한|또|이에 대해|이에|그러나|하지만|이어|앞서|특히)\s+")
# 사람을 가리키는 직함. 이것으로 끝나는 주어만 '말한 사람'으로 본다.
_ROLE = (r"(?:교수|위원장|연구위원|위원|연구원|대표|소장|애널리스트|원장|장관|차관|차장|의원|"
         r"센터장|랩장|팀장|실장|본부장|국장|과장|부장|사장|회장|이사장|이사|총리|부총리|대통령|"
         r"시장|지사|구청장|청장|대변인|평론가|전문가|관계자|수석|변호사|세무사|중개사|총재|행장)")
# 기관. 통계와 설명을 내는 곳들이다.
_ORG = (r"(?:부동산원|국토교통부|국토부|기획재정부|기재부|금융위원회|금융위|금융감독원|금감원|"
        r"한국은행|한은|국세청|통계청|서울시|경기도|인천시|정부|당국|LH|HUG|SH|HF|KDI|"
        r"KB부동산|KB국민은행|부동산R114|부동산114|직방|다방|주택산업연구원|주산연|"
        r"건설산업연구원|건산연|국토연구원|[가-힣A-Za-z]{2,10}(?:연구원|연구소|협회|공사|위원회))")
_WHO_RE = re.compile(r"^(?P<who>.{0,22}?(?:" + _ROLE + "|" + _ORG + r"))(?:은|는|이|가)\s")
# 남의 말을 옮기는 꼴로 끝나는 문장. "~다고 말했습니다", "~라고 봤습니다".
_SAID_END_RE = re.compile(
    r"(?:다고|라고|자고|냐고|다며|라며|[\"”’'])\s?(?:[가-힣]{0,6}\s)?"
    r"(?:말했|밝혔|설명했|분석했|전망했|내다봤|지적했|강조했|평가했|진단했|주장했|경고했|"
    r"조언했|예상했|덧붙였|전했|주문했|촉구했|요구했|비판했|반박했|당부했|봤|했)습니다[.!?]*$")
_BY_RE = re.compile(r"^(?P<who>.{2,26}?)에\s?따르면[,，]?\s")
# 한 문장에 말한 사람이 둘이면("김학렬 소장은 …고 했고, 김인만 대표는 …") 앞사람의 이름표는
# 뒷사람이 나오기 전까지만 띄운다. 끝까지 두면 뒷사람의 말이 앞사람 말로 보인다.
_NEXT_WHO_RE = re.compile(r"[,，]\s*.{0,22}?(?:" + _ROLE + "|" + _ORG + r")(?:은|는|이|가)\s")
# 주어 안에 이런 토막이 끼면 주어가 아니라 문장의 앞부분을 통째로 집은 것이다
# ("서울 아파트값은 …했다고 부동산원" 같은 꼴).
_NOT_NAME_TOK_RE = re.compile(r"(?:은|는|을|를|에서|에게|으로|다고|라고|며|면|고)$")

# 시청자에게 확인할 일을 짚는 끝 문장(대본 규칙 7).
_CHECK_RE = re.compile(
    r"(?:확인|점검|비교|계산|준비)하(?:세요|십시오|셔야)|"
    r"(?:따져|살펴|챙겨|알아|짚어|눈여겨)\s?(?:보|두)(?:세요|십시오|셔야)|"
    r"(?:챙기|살피|따지)(?:세요|십시오|셔야)|"
    r"(?:확인|점검|비교)하는\s?(?:것이|게)\s?(?:중요|필요)합니다|"
    r"(?:확인|점검)(?:이|이\s)?\s?(?:필요|중요)합니다|유의하(?:세요|십시오|셔야)")
# 진행자 본인의 읽기(규칙 16). 1인칭이 드러난 문장만 본다.
_HOST_RE = re.compile(r"(?:^|\s)(?:저는|제\s?눈에는?|제가\s?보기에는?)\s")
# 끝맺음 질문과 구독 안내는 해설이 아니다.
_NOT_TAKE_RE = re.compile(r"\?\s*$|댓글|구독")

KICKER = {"said": "발언", "source": "출처"}
HOST_CHECK = "임장왕 체크"
HOST_VIEW = "임장왕의 해석"


@dataclass
class Tag:
    kind: str          # "said" | "source" | "host"
    text: str          # 이름표에 쓸 말(대본 그대로, 또는 HOST_*)
    start_pos: int     # 대본(공백을 하나로 줄인 것) 안에서 문장이 시작하는 자리
    end_pos: int       # 문장이 끝나는 자리


def _clean_who(who: str) -> str:
    who = _LEAD_RE.sub("", who.strip())
    return who.strip(" ,·")


def _looks_like_name(who: str) -> bool:
    if not who or len(who) > WHO_MAX_CHARS:
        return False
    toks = who.split()
    # 마지막 토막(직함·기관)을 뺀 앞 토막에 조사가 붙어 있으면 이름이 아니다.
    return not any(_NOT_NAME_TOK_RE.search(t) for t in toks[:-1])


def _sentences(norm: str) -> list[tuple[int, int, str]]:
    out = []
    for m in re.finditer(r".+?(?:(?<=[다요죠])[.!?]+(?!\d)|$)", norm):
        s = m.group(0)
        if s.strip():
            lead = len(s) - len(s.lstrip())
            out.append((m.start() + lead, m.end(), s.strip()))
    return out


def find_tags(caption_script: str) -> list[Tag]:
    """대본에서 이름표를 붙일 문장을 찾는다. 자리는 공백을 하나로 줄인 대본 기준이다."""
    norm = re.sub(r"\s+", " ", caption_script or "").strip()
    sents = _sentences(norm)
    tags: list[Tag] = []
    host: Tag | None = None
    for a, b, sent in sents:
        m = _BY_RE.match(sent)
        if m:
            who = _clean_who(m.group("who"))
            if _looks_like_name(who):
                tags.append(Tag("source", who, a, b))
                continue
        m = _WHO_RE.match(_LEAD_RE.sub("", sent))
        if m and _SAID_END_RE.search(sent):
            who = _clean_who(m.group("who"))
            if _looks_like_name(who):
                nxt = _NEXT_WHO_RE.search(sent, len(m.group(0)))
                tags.append(Tag("said", who, a, a + nxt.start() if nxt else b))
                continue
        if _NOT_TAKE_RE.search(sent):
            continue
        if _HOST_RE.search(sent):
            host = Tag("host", HOST_VIEW, a, b)
        elif _CHECK_RE.search(sent):
            host = Tag("host", HOST_CHECK, a, b)
    # 해설 이름표는 영상에 하나만, 가장 뒤의 것을 쓴다(끝 문장이 대개 실전 시사점이다).
    if host:
        tags.append(host)
    return sorted(tags, key=lambda t: t.start_pos)


def _fit(draw, text: str, size: int, max_w: int, floor: int = 30):
    while size > floor:
        font = ImageFont.truetype(font_bold(), size)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return ImageFont.truetype(font_bold(), floor)


def render_tag(tag: Tag, out: Path) -> Path:
    """이름표 한 장(투명 전체 화면 PNG). 왼쪽 노란 칸에 갈래, 오른쪽 남색 칸에 이름."""
    img = Image.new("RGBA", (SHORTS_WIDTH, SHORTS_HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y0, y1 = TAG_Y, TAG_Y + TAG_H
    cy = (y0 + y1) // 2
    if tag.kind == "host":
        # 채널의 말은 통째로 노란 칸에 쓴다. 남의 말(남색 칸)과 한눈에 갈린다.
        font = ImageFont.truetype(font_bold(), 46)
        tw = int(d.textlength(tag.text, font=font))
        x1 = TAG_X + 78 + tw + 30
        d.rounded_rectangle([TAG_X, y0, x1, y1], radius=18, fill=YELLOW + (255,))
        # 체크 표시(폰트에 없는 글자라 선으로 그린다)
        cx = TAG_X + 44
        d.line([(cx - 16, cy + 1), (cx - 5, cy + 13), (cx + 17, cy - 13)], fill=INK + (255,),
               width=8, joint="curve")
        d.text((TAG_X + 78, cy), tag.text, font=font, fill=INK + (255,), anchor="lm")
    else:
        kicker = KICKER[tag.kind]
        kf = ImageFont.truetype(font_bold(), 36)
        kw = int(d.textlength(kicker, font=kf)) + 44
        name = fix_glyphs(tag.text)
        nf = _fit(d, name, 46, TAG_MAX_W - kw - 56)
        nw = int(d.textlength(name, font=nf)) + 56
        d.rounded_rectangle([TAG_X, y0, TAG_X + kw + nw, y1], radius=18, fill=NAVY + (236,))
        d.rounded_rectangle([TAG_X, y0, TAG_X + kw, y1], radius=18, fill=YELLOW + (255,))
        # 노란 칸의 오른쪽 모서리는 각지게 닫아 남색 칸과 맞닿게 한다.
        d.rectangle([TAG_X + kw - 18, y0, TAG_X + kw, y1], fill=YELLOW + (255,))
        d.text((TAG_X + kw // 2, cy), kicker, font=kf, fill=INK + (255,), anchor="mm")
        d.text((TAG_X + kw + 26, cy), name, font=nf, fill=WHITE + (255,), anchor="lm")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
