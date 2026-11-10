"""테스트 공통 준비.

테스트는 네트워크와 키 없이 돈다(Gemini·TTS·Pexels·유튜브를 부르지 않는다).
실험 팔은 날짜와 output/experiments_state.json에 따라 달라지므로, 팔에 기대는 테스트는
force_arms로 고정한다.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _notes_in_tmp(tmp_path, monkeypatch):
    """빌드 메모가 실제 output/video/build_notes.txt에 섞여 들어가지 않게 한다."""
    from src.utils import buildnotes
    monkeypatch.setattr(buildnotes, "NOTES_PATH", tmp_path / "build_notes.txt")


@pytest.fixture
def force_arms(monkeypatch):
    """실험 팔을 고정한다. force_arms(article_pos="late")처럼 쓴다. 나머지는 대조군."""
    from src import experiments

    def _set(**arms):
        def fake(key, day=None, state=None):
            return arms.get(key, experiments.BY_KEY[key]["control"])
        monkeypatch.setattr(experiments, "arm", fake)
    return _set
