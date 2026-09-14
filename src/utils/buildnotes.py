"""빌드 진단 메모 — 검증 아티팩트에 실어 보는 용도.

Actions 로그는 tail이 잘려 생성 단계 출력까지 닿지 않는 경우가 많다
(2026-09-14에 기사 카드 진단 로그를 세 번 놓쳤다). 그래서 판정에 필요한
사실만 파일로 따로 남긴다. 실패해도 파이프라인을 멈추지 않는다.
"""

from __future__ import annotations

from config.settings import VIDEO_DIR

NOTES_PATH = VIDEO_DIR / "build_notes.txt"


def reset() -> None:
    """실행 시작 시 1회. 이전 실행의 메모가 섞이지 않게 비운다."""
    try:
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        NOTES_PATH.write_text("", encoding="utf-8")
    except OSError:
        pass


def note(line: str) -> None:
    try:
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        with open(NOTES_PATH, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except OSError:
        pass
