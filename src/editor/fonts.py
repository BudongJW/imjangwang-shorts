"""한글 폰트 경로 탐색. assets/fonts 번들 → 시스템 나눔/맑은고딕 순."""

from pathlib import Path

from config.settings import FONT_DIR

# 후보 경로: 번들(assets/fonts) → 리눅스(fonts-nanum) → macOS → Windows
_BOLD = [
    FONT_DIR / "NanumGothicBold.ttf",
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic-Bold.ttf"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
]
_REGULAR = [
    FONT_DIR / "NanumGothic.ttf",
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
]


def _first(cands: list[Path]) -> str:
    for c in cands:
        if Path(c).exists():
            return str(c)
    # 최후: PIL 기본(한글 깨질 수 있음) — CI에선 fonts-nanum 설치로 도달 안 함
    raise FileNotFoundError(
        "한글 폰트를 찾지 못했습니다. assets/fonts/NanumGothic(Bold).ttf 를 두거나 "
        "시스템에 나눔고딕을 설치하세요 (CI: apt-get install fonts-nanum)."
    )


def font_bold() -> str:
    return _first(_BOLD)


def font_regular() -> str:
    return _first(_REGULAR)
