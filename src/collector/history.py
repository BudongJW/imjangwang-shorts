"""토픽 중복 방지: 이전에 사용한 토픽을 추적한다."""

import json
import re
from datetime import datetime, timedelta
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


def is_duplicate(
    title: str, threshold: float = 0.6, history: list[dict] | None = None
) -> bool:
    """제목이 이전에 사용된 토픽과 유사한지 확인한다.
    단순 키워드 겹침 비율로 판단 (외부 라이브러리 불필요).

    history를 넘기면 파일을 다시 읽지 않는다 — 후보 수백 건을 훑는
    collect()에서 호출마다 JSON을 재파싱하지 않게 하기 위한 것.
    """
    if history is None:
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


# 같은 사건을 다른 매체가 다른 제목으로 쓴 경우. 낱말 겹침 비율(위)로는
# 안 잡힌다. 10-02 초안이 전날 지정 대본으로 올린 LH 사장 대출규제 발언을
# 아시아경제 제목으로 다시 골랐다('LH사장 발언에 국토부 술렁' 대 '이성훈 LH
# 사장 "집값 안정 위해 대출규제 필요"'). 흔한 부동산 낱말을 빼고 남은 고유한
# 낱말이 최근 며칠 안 기록과 STORY_MIN_SHARED개 이상 겹치면 같은 사건으로 본다.
STORY_DAYS = 3
STORY_MIN_SHARED = 3
_STOP = set("""서울 수도권 경기 인천 지방 전국 아파트 주택 집값 전세 월세 매매 분양 청약
상승 하락 급등 급락 증가 감소 정부 부동산 시장 가격 거래 필요 대책 정책 규제 확대 축소
올해 내년 지난해 이번 최대 최고 최저 역대 기자 단독 종합 속보 뉴스 발표 전망 분석
있어 없어 위해 대해 통해 이후 이전 만에 가구 억원 만원 경우 관련 논란 우려 지적""".split())
_JOSA_RE = re.compile(r"(?:은|는|이|가|을|를|에|의|도|로|으로|에서|까지|부터|와|과|만|서)$")


def _story_tokens(title: str) -> set[str]:
    t = re.sub(r"[^0-9A-Za-z가-힣]+", " ", title or "")
    # 'LH사장'처럼 붙은 영문·한글을 가른다.
    t = re.sub(r"([A-Za-z]+)([가-힣])", r"\1 \2", t)
    t = re.sub(r"([가-힣])([A-Za-z]+)", r"\1 \2", t)
    out = set()
    for w in t.lower().split():
        w = _JOSA_RE.sub("", w) if len(w) > 2 else w
        if len(w) < 2 or w in _STOP or w.isdigit():
            continue
        out.add(w)
    return out


def is_recent_same_story(title: str, history: list[dict] | None = None,
                         days: int = STORY_DAYS) -> str | None:
    """최근 days일 안에 같은 사건을 다룬 기록이 있으면 그 제목, 없으면 None."""
    if history is None:
        history = load_history()
    mine = _story_tokens(title)
    if len(mine) < STORY_MIN_SHARED:
        return None
    cutoff = datetime.now() - timedelta(days=days)
    for entry in history:
        if not entry.get("video_id"):
            continue      # 검증 렌더·업로드 실패 기록은 소비한 주제가 아니다
        try:
            when = datetime.fromisoformat(str(entry.get("date", ""))[:26])
        except ValueError:
            continue
        if when < cutoff:
            continue
        if len(mine & _story_tokens(entry.get("title", ""))) >= STORY_MIN_SHARED:
            return entry.get("title", "")
    return None


def record_topic(title: str, video_id: str = "", **meta) -> None:
    """사용한 토픽을 기록한다.

    meta로 넘긴 값(len_mode, script_chars 등)을 함께 남긴다. 길이 실험처럼
    코호트를 나눠 비교해야 하는 변경은 날짜로 추정하면 크론 실패·수동 재업로드
    한 번에 경계가 무너진다. 영상마다 어떤 설정으로 만들어졌는지 박아 둔다.
    """
    history = load_history()
    history.append({
        "title": title,
        "video_id": video_id,
        "date": datetime.now().isoformat(),
        **{k: v for k, v in meta.items() if v is not None},
    })
    # 최근 200개만 유지
    if len(history) > 200:
        history = history[-200:]
    save_history(history)


def filter_new_topics(titles: list[str]) -> list[str]:
    """중복되지 않는 토픽만 필터링한다."""
    return [t for t in titles if not is_duplicate(t)]
