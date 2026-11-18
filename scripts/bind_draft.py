"""토픽 기록의 초안 ID를 실제 유튜브 영상 ID로 바꾼다.

DRAFT_ONLY 빌드는 유튜브에 올리지 않아 기록에 'draft-<run_id>'를 남긴다. admin의
publish가 그 초안을 올리면 실제 ID로 바꿔야 분석(실험 배정, 성적)이 영상과 이어진다.

사용: python scripts/bind_draft.py <topic_history.json> <draft_id> <video_id>
"""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    path, draft, vid = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    rows = json.loads(path.read_text(encoding="utf-8"))
    hit = 0
    for r in rows:
        if isinstance(r, dict) and r.get("video_id") == draft:
            r["video_id"] = vid
            hit += 1
    if not hit:
        print(f"기록에 {draft} 가 없습니다(이미 바뀌었거나 지운 초안)")
        return 0
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{draft} → {vid} ({hit}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
