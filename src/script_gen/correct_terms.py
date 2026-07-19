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
_CHAE = re.compile(r"(\d+)\s*차(?=[에을\s\.,]|$)")

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


def normalize_caption(text: str) -> str:
    """화면 자막용 정확 표기로 교정."""
    for bad, good in CAPTION_FIXES.items():
        text = text.replace(bad, good)
    text = _CHAE.sub(r"\1채", text)
    # 다중 공백 정리
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text


def to_speech(text: str) -> str:
    """edge-tts 입력용 발음형으로 변환(약어·기호·억 단위)."""
    text = normalize_caption(text)
    for k, v in SPEECH_MAP.items():
        text = text.replace(k, v)
    # '20억' 같은 금액은 edge-tts가 잘 읽지만, 붙은 표기 안전화
    text = re.sub(r"(\d)\s*억", r"\1억 ", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text
