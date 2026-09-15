"""topic_history.json 두 사본을 합친다.

업로드는 run/daily-force-* 같은 일회용 브랜치에서도 일어난다. 그때 기록을
그 브랜치에 커밋하면 아무도 다시 읽지 않는다. 그래서 워크플로는 기본 브랜치
사본 위에 실행 사본을 합쳐 기본 브랜치로 되돌려 보낸다. 그 사이 기본 브랜치가
먼저 갱신됐을 수 있으므로 덮어쓰지 않고 합친다.

사용: python scripts/merge_history.py <기본브랜치사본> <실행사본> <출력>
"""

import json
import sys
from pathlib import Path

MAX_ROWS = 200


def _load(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in rows if isinstance(r, dict)]


def merge(base: list[dict], new: list[dict]) -> list[dict]:
    """date가 같고 title이 같으면 같은 기록으로 본다.

    video_id로는 못 묶는다 — 검증 실행은 video_id가 빈 문자열이고,
    업로드 실패 후 재시도한 기록도 빈 문자열로 남는다.
    """
    seen = {(r.get("title"), r.get("date")) for r in base}
    merged = base + [r for r in new if (r.get("title"), r.get("date")) not in seen]
    merged.sort(key=lambda r: str(r.get("date", "")))
    return merged[-MAX_ROWS:]


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    base_p, new_p, out_p = (Path(a) for a in sys.argv[1:])
    merged = merge(_load(base_p), _load(new_p))
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"병합 {len(merged)}건 → {out_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
