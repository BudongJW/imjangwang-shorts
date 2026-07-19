"""토픽 중복 방지: 이전에 사용한 토픽을 추적한다."""

import json
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path(__file__).parent.parent.parent / "output" / "topic_history.json"


def load_history() -> list[dict]:
    """사용 이력을 로드한다."""
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def save_history(history: list[dict]) -> None:
    """사용 이력을 저장한다."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_duplicate(title: str, threshold: float = 0.6) -> bool:
    """제목이 이전에 사용된 토픽과 유사한지 확인한다.
    단순 키워드 겹침 비율로 판단 (외부 라이브러리 불필요).
    """
    history = load_history()
    title_words = set(title.lower().split())

    for entry in history:
        prev_words = set(entry["title"].lower().split())
        if not title_words or not prev_words:
            continue
        overlap = len(title_words & prev_words) / max(len(title_words), len(prev_words))
        if overlap >= threshold:
            return True
    return False


def record_topic(title: str, video_id: str = "") -> None:
    """사용한 토픽을 기록한다."""
    history = load_history()
    history.append({
        "title": title,
        "video_id": video_id,
        "date": datetime.now().isoformat(),
    })
    # 최근 200개만 유지
    if len(history) > 200:
        history = history[-200:]
    save_history(history)


def filter_new_topics(titles: list[str]) -> list[str]:
    """중복되지 않는 토픽만 필터링한다."""
    return [t for t in titles if not is_duplicate(t)]
