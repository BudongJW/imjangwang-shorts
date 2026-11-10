"""지정 대본 회귀 검사.

사람이 검수해 내보낸 지정 대본(output/pinned_plan.json)은 커밋 기록에 전부 남아 있다.
대본 사실 검사(src/script_gen/generator.py의 script_defects)를 고칠 때마다 이 대본들에
다시 돌려, 멀쩡한 대본을 새로 틀렸다고 잡지 않는지 본다. 검사를 하루에 하나씩 더하는
구조라 정규식 하나가 지난 대본을 무더기로 잡기 시작해도 모르고 지나가기 쉽다.

    python scripts/check_corpus.py            # 기준선과 견줘 새로 걸린 것만 보고, 있으면 종료코드 1
    python scripts/check_corpus.py --update   # 지금 결과를 기준선으로 저장
    python scripts/check_corpus.py --all      # 걸린 대본을 전부 보여 준다

기준선(tests/corpus_baseline.json)에는 '원래부터 걸리던 것'이 들어 있다. 9월과 10월
초의 지정 대본은 사람이 여러 자료를 보고 쓴 기획물이라, 기사 한 건에 없는 숫자와
연도가 들어 있어 걸리는 게 정상이다. 봐야 하는 것은 기준선에 없던 새 지적이다.

커밋 기록에서 읽으므로 얕은 클론(fetch-depth 1)에서는 대본이 몇 편 안 나온다.
그때는 `git fetch --unshallow` 뒤에 다시 돌린다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PIN_PATH = "output/pinned_plan.json"
BASELINE_PATH = ROOT / "tests" / "corpus_baseline.json"
MIN_BODY = 80       # generate()가 사실 대조를 켜는 본문 길이와 같다


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def load_pins() -> list[tuple[str, dict]]:
    """커밋 기록의 지정 대본 전부를 (커밋 해시 앞 10자, 내용)으로 돌려준다. 오래된 것부터."""
    out: list[tuple[str, dict]] = []
    seen: set[str] = set()
    commits = _git("log", "--format=%H", "--diff-filter=AM", "--", PIN_PATH).split()
    for c in reversed(commits):
        raw = _git("show", f"{c}:{PIN_PATH}")
        try:
            data = json.loads(raw)
            cap = data["plan"]["caption_script"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if cap in seen:         # 같은 대본을 다시 올린 커밋(재실행)
            continue
        seen.add(cap)
        out.append((c[:10], data))
    return out


def check(pins: list[tuple[str, dict]]) -> dict[str, list[str]]:
    """대본마다 걸린 사유. 안 걸린 대본은 넣지 않는다."""
    from src.script_gen.generator import script_defects
    found: dict[str, list[str]] = {}
    for key, data in pins:
        art, plan = data.get("article", {}), data.get("plan", {})
        body = (art.get("summary") or "").strip()
        if len(body) < MIN_BODY:
            continue
        hay = f"{art.get('title', '')} {body}"
        _needles, why = script_defects(str(plan.get("caption_script", "")), hay,
                                       art.get("title", ""), art.get("published", ""))
        if why:
            found[key] = sorted(set(why))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="지정 대본 회귀 검사")
    ap.add_argument("--update", action="store_true", help="지금 결과를 기준선으로 저장")
    ap.add_argument("--all", action="store_true", help="기준선에 있던 것까지 전부 출력")
    args = ap.parse_args()

    pins = load_pins()
    if not pins:
        print("지정 대본을 커밋 기록에서 찾지 못했다(얕은 클론이면 git fetch --unshallow).")
        return 0
    found = check(pins)
    titles = {k: d.get("plan", {}).get("youtube_title", "") for k, d in pins}

    if args.update:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(found, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
        print(f"기준선 저장: 대본 {len(pins)}편 중 {len(found)}편이 걸린 상태")
        return 0

    try:
        base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        base = {}
    fresh = {k: [w for w in why if w not in base.get(k, [])] for k, why in found.items()}
    fresh = {k: w for k, w in fresh.items() if w}

    print(f"지정 대본 {len(pins)}편 · 걸린 대본 {len(found)}편 · 기준선에 없던 지적 "
          f"{sum(len(w) for w in fresh.values())}건")
    for k, why in (found if args.all else fresh).items():
        print(f"\n[{k}] {titles.get(k, '')}")
        for w in why:
            mark = "" if w in base.get(k, []) else "  ← 새로 걸림"
            print(f"  - {w}{mark}")
    return 1 if fresh else 0


if __name__ == "__main__":
    sys.exit(main())
