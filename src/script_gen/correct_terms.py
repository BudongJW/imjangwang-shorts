"""용어 교정 — 개선안 ④.

분석에서 드러난 오류(LTV를 'LTB', 실수요자를 '실수 요자', 폭증을 '복증',
위례를 '위래', 72채를 '72차')는 대본/자막 단계에서 원천 차단한다.

두 가지를 제공한다:
  - normalize_caption(): 화면 자막용 '정확한 한글 표기'로 정규화.
  - to_speech(): edge-tts가 정확히 읽도록 약어·단위·기호를 '발음형'으로 변환.
    (화면에는 'LTV'로 쓰되, 음성은 '엘티브이'로 읽게 해 오독을 방지)
"""

import re

# 화면 자막 표기 교정: 흔한 오탈자 → 올바른 표기
CAPTION_FIXES = {
    "LTB": "LTV",
    "실수 요자": "실수요자",
    "실수요 자": "실수요자",
    "복증": "폭증",
    "위래": "위례",
    "역세권 대단지": "역세권 대단지",
    "다주택 자": "다주택자",
    "갭 투자": "갭투자",
    "재 건축": "재건축",
    "재 개발": "재개발",
}

# '채'(집 수량) 오독 교정: 숫자+차 → 숫자+채
# 단지 이름과 차수는 건드리지 않는다. 10-02 초안에서 '신현대9차'가
# '신현대9채'로 바뀌어 자막에 나갔다. 이름에 붙은 숫자(신반포15차·한양8차)와
# 한 자리 차수(1차 조사·3차 신도시)는 그대로 두고, 띄어 쓴 두 자리 이상
# 숫자 뒤에 조사·문장부호가 바로 올 때만 고친다("72차를" → "72채를").
_CHAE = re.compile(r"(?<![가-힣A-Za-z\d])(\d{2,}[\d,]*)\s*차(?=[에을를가이은는도]|[.,]|$)")

# TTS 발음 변환: 약어/기호 → 한글 발음
SPEECH_MAP = {
    "LTV": "엘티브이",
    "DSR": "디에스알",
    "DTI": "디티아이",
    "GTX": "지티엑스",
    "PF": "피에프",
    "㎡": "제곱미터",
    "m²": "제곱미터",
    "%": "퍼센트",
    "3기": "삼기",       # 3기 신도시 → '삼기 신도시'
    "1기": "일기",
    "2기": "이기",
}


# 생성 모델이 기사의 "2023~2025년", "1~4월"을 "2023에서 2025년",
# "1에서 4월"로 풀어 쓴다(09-29 재생성본). 앞 수에 단위가 없는 이 모양은
# 우리말로도 어색해서 범위 표기로 되돌린다. "2023에서 2025년으로"처럼
# 바뀐 값을 말하는 경우는 건드리지 않는다.
_RANGE_YEAR_GARBLE = re.compile(r"(?<![\d.])(\d{4})에서 (\d{4})년(?!으로|도|까지)")
_RANGE_MONTH_GARBLE = re.compile(r"(?<![\d.])(\d{1,2})에서 (\d{1,2})월(?!로|으로|까지)")
# 읽을 때는 물결표 대신 말로 푼다.
_RANGE_YEAR = re.compile(r"(\d{4})\s?[~∼～]\s?(\d{4})년")
_RANGE_MONTH = re.compile(r"(?<![\d.])(\d{1,2})\s?[~∼～]\s?(\d{1,2})월")


# 숫자를 자릿수 글자와 섞어 쓴 것("4천6백6십8가구"). 10-02 초안 자막에 그대로
# 나갔다. 백·십이 들어간 것만 아라비아 숫자로 되돌린다. "4천만 원", "2만 4000"
# 같은 금액 표기는 원래 쓰는 꼴이라 건드리지 않는다(뒤에 만·억이 오면 제외).
_MIXED_NUM_RE = re.compile(
    r"(?<![\d.])(?:(\d)천\s?)?(?:(\d)백\s?)?(?:(\d)십\s?)?(\d)?(?![\d만억천백십])")


def _mixed_num(m: re.Match) -> str:
    th, hu, te, one = m.groups()
    if not (hu or te):
        return m.group(0)
    n = int(th or 0) * 1000 + int(hu or 0) * 100 + int(te or 0) * 10 + int(one or 0)
    tail = m.group(0)[len(m.group(0).rstrip()):]
    return f"{n}{tail}"


def normalize_caption(text: str) -> str:
    """화면 자막용 정확 표기로 교정."""
    for bad, good in CAPTION_FIXES.items():
        text = text.replace(bad, good)
    text = _MIXED_NUM_RE.sub(_mixed_num, text)
    text = _CHAE.sub(r"\1채", text)
    text = _RANGE_YEAR_GARBLE.sub(r"\1~\2년", text)
    text = _RANGE_MONTH_GARBLE.sub(r"\1~\2월", text)
    # 다중 공백 정리
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text


def to_speech(text: str) -> str:
    """edge-tts 입력용 발음형으로 변환(약어·기호·억 단위)."""
    text = normalize_caption(text)
    text = _RANGE_YEAR.sub(r"\1년부터 \2년까지", text)
    text = _RANGE_MONTH.sub(r"\1월부터 \2월까지", text)
    for k, v in SPEECH_MAP.items():
        text = text.replace(k, v)
    # '20억' 같은 금액은 edge-tts가 잘 읽지만, 붙은 표기 안전화
    text = re.sub(r"(\d)\s*억", r"\1억 ", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text
