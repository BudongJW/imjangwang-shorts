"""영상 개선 실험 — 성과 데이터로 영상을 계속 고쳐 나가는 고리.

한 번에 한 가지씩 바꾸고, 바꾼 것과 안 바꾼 것을 같은 기간에 번갈아 내보내
성과를 비교한다. 판정이 나면 이긴 쪽을 기본값으로 굳히고 다음 실험을 연다.

    제작(main·composer)  : 오늘 날짜로 각 실험의 팔(arm)을 정해 그대로 만들고,
                           토픽 기록에 영상별 팔을 남긴다(exp).
    분석(analyze_performance): 게시 48시간 조회수·첫 화면 통과율·평균 시청률을
                           팔별로 모아 판정하고, 이 파일의 상태를 갱신한다.
    상태(output/experiments_state.json): master에 남아 다음 제작이 읽는다.

배정은 날짜(KST)로 한다. 하루 한두 편이라 영상 단위로 섞으면 같은 날 영상끼리
조건이 갈려 확인이 어렵다. 동시에 도는 실험은 두 개까지이고, 서로 다른 칸(slot)
을 써서 겹치지 않게 나눈다.
    slot 0: 날짜 홀짝            (하루씩 번갈아)
    slot 1: 날짜를 2로 나눈 홀짝  (이틀씩 번갈아)
네 날이면 두 실험의 네 조합이 한 번씩 나온다. 한 실험의 효과가 다른 실험의
팔에 몰려 섞이지 않는다.

판정 규칙(evaluate)은 보수적으로 둔다. 표본이 적은 상관계수로 방향을 정했다가
뒤집힌 적이 있다(2026-09-13 길이↔지속률 -0.52가 09-16에 +0.58).
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "output" / "experiments_state.json"
KST = timezone(timedelta(hours=9))

# 위에서부터 차례로 돈다. 대조군(control)은 지금 쓰는 쪽이다. 판정이 '차이
# 없음'이면 대조군을 그대로 둔다(바꿀 이유가 없으면 바꾸지 않는다).
#   metric: 판정에 쓰는 지표. also: 리포트에 같이 보여 주는 지표.
#   pinned: 사람이 쓴 지정 대본도 표본에 넣는지. 길이는 사람이 정하므로 뺀다.
REGISTRY: list[dict] = [
    {"key": "len_mode", "title": "대본 길이", "arms": ["normal", "short"],
     "control": "normal", "metric": "views_48h", "also": ["avg_pct", "pass_rate"],
     "pinned": False,
     "about": "normal 48~55초, short 31~37초"},
    {"key": "hook_card", "title": "첫 화면 숫자 카드", "arms": ["on", "off"],
     "control": "on", "metric": "pass_rate", "also": ["views_48h"],
     "pinned": True,
     "about": "0~2.6초에 앞 문장의 가장 센 숫자를 크게 띄우는지"},
    {"key": "bgm", "title": "배경음", "arms": ["on", "off"],
     "control": "on", "metric": "avg_pct", "also": ["views_48h"],
     "pinned": True,
     "about": "나레이션 아래 잔잔한 배경음을 까는지"},
    {"key": "cut_pace", "title": "컷 길이", "arms": ["3s", "2s"],
     "control": "3s", "metric": "avg_pct", "also": ["views_48h"],
     "pinned": True,
     "about": "배경 사진·영상 한 컷을 3초까지 두는지 2초까지 두는지"},
]
BY_KEY = {e["key"]: e for e in REGISTRY}
MAX_RUNNING = 2
# 팔마다 이만큼 모이면 판정을 시도한다. 구간이 0을 안 넘으면 이긴 쪽으로,
# 두 배가 모여도 넘으면 '차이 없음'으로 닫는다.
MIN_N = 10
METRIC_LABEL = {"views_48h": "게시 48시간 조회수", "pass_rate": "첫 화면 통과율",
                "avg_pct": "평균 시청률"}

# 상태 파일이 없을 때의 시작점. 길이 실험은 10-02부터 날짜 홀짝으로 돌고
# 있었다(그날 config/settings.py의 ab). 첫 화면 카드는 10-02부터 늘 켜져
# 있었으므로 10-04부터 실험으로 연다.
DEFAULT_STATE = {
    "experiments": {
        "len_mode": {"status": "running", "slot": 0, "started": "2026-10-02"},
        "hook_card": {"status": "running", "slot": 1, "started": "2026-10-04"},
        "bgm": {"status": "queued"},
        "cut_pace": {"status": "queued"},
    }
}


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("experiments"), dict):
            # 목록에 새로 넣은 실험이 상태 파일에 없으면 대기로 붙인다.
            for e in REGISTRY:
                data["experiments"].setdefault(e["key"], {"status": "queued"})
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: dict) -> None:
    state["updated"] = datetime.now(KST).isoformat(timespec="seconds")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _slot_bit(day: str, slot: int) -> int:
    o = datetime.strptime(day, "%Y-%m-%d").date().toordinal()
    return (o // (2 ** slot)) % 2


def arm(key: str, day: str | None = None, state: dict | None = None) -> str:
    """오늘(또는 day) 이 실험에서 쓸 팔. 판정이 났으면 이긴 쪽, 대기 중이면 대조군."""
    exp = BY_KEY[key]
    st = (state or load_state())["experiments"].get(key, {})
    status = st.get("status")
    if status == "decided":
        return st.get("winner") or exp["control"]
    day = day or today_kst()
    if status == "running" and day >= st.get("started", day):
        return exp["arms"][_slot_bit(day, int(st.get("slot", 0)))]
    return exp["control"]


def current_arms(day: str | None = None) -> dict[str, str]:
    """토픽 기록에 남길 영상별 팔. 모든 실험의 값을 남겨 나중에 다시 가를 수 있게 한다."""
    state = load_state()
    return {e["key"]: arm(e["key"], day, state) for e in REGISTRY}


# ── 판정 ─────────────────────────────────────────────────────────────


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def bootstrap_diff(a: list[float], b: list[float], iters: int = 2000,
                   seed: int = 7) -> tuple[float, float, float]:
    """(b 중앙값 − a 중앙값, 90% 구간 하한, 상한). 같은 입력이면 늘 같은 값이다."""
    rnd = random.Random(seed)
    diffs = []
    for _ in range(iters):
        ra = [rnd.choice(a) for _ in a]
        rb = [rnd.choice(b) for _ in b]
        diffs.append(_median(rb) - _median(ra))
    diffs.sort()
    lo, hi = diffs[int(iters * 0.05)], diffs[int(iters * 0.95) - 1]
    return _median(b) - _median(a), lo, hi


def evaluate(key: str, samples: dict[str, list[float]]) -> dict:
    """팔별 표본으로 판정. 반환: {'verdict': 'collecting'|'winner'|'no_diff', ...}."""
    exp = BY_KEY[key]
    a_name, b_name = exp["arms"]
    a, b = samples.get(a_name, []), samples.get(b_name, [])
    out = {"n": {a_name: len(a), b_name: len(b)}, "verdict": "collecting"}
    if a:
        out["median_" + a_name] = _median(a)
    if b:
        out["median_" + b_name] = _median(b)
    if min(len(a), len(b)) < MIN_N:
        return out
    diff, lo, hi = bootstrap_diff(a, b)
    out.update({"diff": diff, "ci": [lo, hi]})
    if lo > 0:
        out.update({"verdict": "winner", "winner": b_name})
    elif hi < 0:
        out.update({"verdict": "winner", "winner": a_name})
    elif min(len(a), len(b)) >= 2 * MIN_N:
        out.update({"verdict": "no_diff", "winner": exp["control"]})
    return out


def apply_verdicts(state: dict, results: dict[str, dict], day: str | None = None) -> list[str]:
    """판정이 난 실험을 닫고 빈 칸에 다음 대기 실험을 연다. 바뀐 내용을 글로 돌려준다."""
    day = day or today_kst()
    exps = state["experiments"]
    notes: list[str] = []
    for key, res in results.items():
        st = exps.get(key, {})
        if st.get("status") != "running" or res.get("verdict") not in ("winner", "no_diff"):
            continue
        freed = st.get("slot", 0)
        exps[key] = {**st, "status": "decided", "winner": res["winner"],
                     "verdict": res["verdict"], "decided": day, "result": res}
        title = BY_KEY[key]["title"]
        if res["verdict"] == "winner":
            notes.append(f"{title}: '{res['winner']}' 쪽이 나아서 기본값으로 굳힘")
        else:
            notes.append(f"{title}: 차이 없음, 지금 쓰는 '{res['winner']}' 유지")
        nxt = next((e["key"] for e in REGISTRY
                    if exps.get(e["key"], {}).get("status") == "queued"), None)
        if nxt:
            # 내일부터 연다. 오늘 이미 만든 영상은 새 실험 팔이 없다.
            start = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            exps[nxt] = {"status": "running", "slot": freed, "started": start}
            notes.append(f"다음 실험 시작({start}): {BY_KEY[nxt]['title']}")
    running = [k for k, v in exps.items() if v.get("status") == "running"]
    if len(running) > MAX_RUNNING:      # 손으로 고친 상태 파일이 넘치게 연 경우
        notes.append(f"주의: 동시에 도는 실험이 {len(running)}개다(최대 {MAX_RUNNING})")
    return notes
