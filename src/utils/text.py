"""설명·자막 문자열 손질. 무거운 의존성 없이 단독으로 import된다.

youtube_admin.py는 Actions에서 google-api 계열만 설치한 채로 돌기 때문에,
여기에 있는 함수가 orchestrator.main에 있으면 feedparser·PIL·playwright까지
끌려와 ModuleNotFoundError로 죽는다. 그래서 별도 모듈로 둔다.
"""

from __future__ import annotations

import re

# 도메인만 남겨 두면 유튜브가 자동 링크로 만드는 경우가 있어 www. 형태까지 뺀다.
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)


def strip_links(text: str) -> str:
    """설명에서 외부 링크를 걷어낸다.

    2026-09-19에 유튜브가 설명 속 기사 URL을 "스팸, 현혹 행위, 사기에 대한
    정책" 위반으로 삭제했다. 채널 경고는 없었지만, 이 채널은 160편 전부가
    같은 자리에 외부 기사 링크를 달고 있어 링크 스팸 신호로 남는다.
    """
    out = _URL_RE.sub("", text)
    # 링크만 있던 줄이 "출처:" 껍데기로 남지 않게 정리
    lines = [ln.rstrip() for ln in out.split("\n")]
    lines = [ln for ln in lines if ln.strip() not in ("출처:", "사진:")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
