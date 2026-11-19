"""지정 대본 회귀 검사를 테스트로 돌린다(scripts/check_corpus.py와 같은 기준)."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MIN_PINS = 30      # 이보다 적으면 얕은 클론이다. 기준선과 견줄 수 없다.


def _corpus():
    spec = importlib.util.spec_from_file_location("check_corpus",
                                                  ROOT / "scripts" / "check_corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_approved_scripts_get_no_new_flags():
    cc = _corpus()
    pins = cc.load_pins()
    if len(pins) < MIN_PINS:
        pytest.skip(f"커밋 기록에 지정 대본이 {len(pins)}편뿐이다(얕은 클론). "
                    "git fetch --unshallow 뒤에 다시 돌린다.")
    base = json.loads(cc.BASELINE_PATH.read_text(encoding="utf-8"))
    found = cc.check(pins)
    fresh = {k: [w for w in why if w not in base.get(k, [])] for k, why in found.items()}
    fresh = {k: w for k, w in fresh.items() if w}
    assert not fresh, ("사람이 검수해 내보낸 대본이 새로 걸렸다. 검사가 넓어졌는지 본다. "
                       f"의도한 변화면 python scripts/check_corpus.py --update: {fresh}")
