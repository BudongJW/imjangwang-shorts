"""실험 배정과 판정."""

import json

import pytest

from src import experiments as ex


def _state(**exps):
    return {"experiments": exps}


def test_registry_is_consistent():
    keys = [e["key"] for e in ex.REGISTRY]
    assert len(keys) == len(set(keys))
    for e in ex.REGISTRY:
        assert len(e["arms"]) == 2 and e["control"] in e["arms"]
        assert e["metric"] in ex.METRIC_LABEL
        assert all(m in ex.METRIC_LABEL for m in e.get("also", []))


def test_queued_experiment_uses_control_and_decided_uses_winner():
    st = _state(bgm={"status": "queued"},
                hook_card={"status": "decided", "winner": "off"})
    assert ex.arm("bgm", "2026-11-10", st) == "on"
    assert ex.arm("hook_card", "2026-11-10", st) == "off"
    assert ex.arm("cut_pace", "2026-11-10", _state()) == "3s"     # 상태에 없으면 대조군


def test_running_experiment_alternates_by_slot():
    days = [f"2026-11-{d:02d}" for d in range(1, 17)]
    for slot, block in ((0, 1), (1, 2), (2, 4)):
        st = _state(len_mode={"status": "running", "slot": slot, "started": "2026-10-01"})
        arms = [ex.arm("len_mode", d, st) for d in days]
        assert set(arms) == {"normal", "short"}
        assert arms.count("normal") == arms.count("short") == 8
        runs = [len(x) for x in "".join("n" if a == "normal" else "s" for a in arms)
                .replace("ns", "n s").replace("sn", "s n").split()]
        assert all(r == block for r in runs[1:-1])        # 가운데 토막은 정확히 block일


def test_three_slots_cover_all_eight_combinations_in_eight_days():
    st = _state(len_mode={"status": "running", "slot": 0, "started": "2026-10-01"},
                outro_q={"status": "running", "slot": 1, "started": "2026-10-01"},
                bg_mode={"status": "running", "slot": 2, "started": "2026-10-01"})
    combos = {tuple(ex.arm(k, f"2026-11-{d:02d}", st) for k in ("len_mode", "outro_q", "bg_mode"))
              for d in range(1, 9)}
    assert len(combos) == 8


def test_experiment_does_not_apply_before_its_start_day():
    st = _state(outro_q={"status": "running", "slot": 0, "started": "2026-11-08"})
    assert ex.arm("outro_q", "2026-11-07", st) == "off"
    assert {ex.arm("outro_q", d, st) for d in ("2026-11-08", "2026-11-09")} == {"off", "on"}


def test_evaluate_waits_for_min_n_then_decides():
    lo, hi = [100.0 + i for i in range(10)], [500.0 + i for i in range(10)]
    assert ex.evaluate("len_mode", {"normal": lo[:9], "short": hi})["verdict"] == "collecting"
    res = ex.evaluate("len_mode", {"normal": lo, "short": hi})
    assert res["verdict"] == "winner" and res["winner"] == "short"
    res = ex.evaluate("len_mode", {"normal": hi, "short": lo})
    assert res["winner"] == "normal"


def test_evaluate_closes_as_no_diff_only_after_twice_min_n():
    mixed = [10.0, 900.0] * 5                       # 겹치는 분포
    assert ex.evaluate("len_mode", {"normal": mixed, "short": mixed})["verdict"] == "collecting"
    res = ex.evaluate("len_mode", {"normal": mixed * 2, "short": mixed * 2})
    assert res["verdict"] == "no_diff" and res["winner"] == "normal"      # 대조군 유지


def test_bootstrap_is_deterministic():
    a, b = [1.0, 5.0, 9.0, 4.0], [2.0, 8.0, 7.0, 3.0]
    assert ex.bootstrap_diff(a, b) == ex.bootstrap_diff(a, b)


def test_verdict_frees_the_slot_for_the_next_queued_experiment_in_registry_order():
    state = _state(len_mode={"status": "running", "slot": 0, "started": "2026-10-02"},
                   hook_card={"status": "decided", "winner": "off"},
                   bg_mode={"status": "running", "slot": 2, "started": "2026-10-10"},
                   outro_q={"status": "running", "slot": 1, "started": "2026-11-08"},
                   article_pos={"status": "queued"}, bgm={"status": "queued"},
                   cut_pace={"status": "queued"})
    notes = ex.apply_verdicts(
        state, {"len_mode": {"verdict": "no_diff", "winner": "normal", "n": {}}},
        day="2026-11-20")
    exps = state["experiments"]
    assert exps["len_mode"]["status"] == "decided" and exps["len_mode"]["winner"] == "normal"
    order = [e["key"] for e in ex.REGISTRY]
    nxt = next(k for k in order if k in ("article_pos", "bgm", "cut_pace"))
    assert exps[nxt] == {"status": "running", "slot": 0, "started": "2026-11-21"}
    assert sum(v["status"] == "running" for v in exps.values()) == ex.MAX_RUNNING
    assert any("다음 실험 시작" in n for n in notes)


def test_collecting_result_changes_nothing():
    state = _state(len_mode={"status": "running", "slot": 0, "started": "2026-10-02"})
    assert ex.apply_verdicts(state, {"len_mode": {"verdict": "collecting"}}) == []
    assert state["experiments"]["len_mode"]["status"] == "running"


def test_new_registry_entry_is_queued_when_missing_from_state_file(tmp_path, monkeypatch):
    path = tmp_path / "experiments_state.json"
    path.write_text(json.dumps(_state(len_mode={"status": "running", "slot": 0,
                                                "started": "2026-10-02"})), encoding="utf-8")
    monkeypatch.setattr(ex, "STATE_PATH", path)
    state = ex.load_state()
    assert state["experiments"]["article_pos"] == {"status": "queued"}
    assert set(ex.current_arms("2026-11-10")) == {e["key"] for e in ex.REGISTRY}


def test_broken_state_file_falls_back_to_defaults(tmp_path, monkeypatch):
    path = tmp_path / "experiments_state.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ex, "STATE_PATH", path)
    assert ex.load_state()["experiments"]["len_mode"]["status"] == "running"


@pytest.mark.parametrize("key", [e["key"] for e in ex.REGISTRY])
def test_committed_state_file_resolves_every_experiment(key):
    assert ex.arm(key, "2026-11-10") in ex.BY_KEY[key]["arms"]
