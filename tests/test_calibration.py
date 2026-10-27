import random

import pytest

from airmouse import calibration as cal
from airmouse.config import GestureConfig
from airmouse.features import HandFeatures
from airmouse.gestures import is_click_left, is_click_right, is_fist, is_flip, is_switcher_pose

CUR = GestureConfig()


def hf(index=0.93, middle=0.95, ring=0.96, pinky=0.94, pinch=0.05, facing=-0.7) -> HandFeatures:
    return HandFeatures(curl={"thumb": 0.9, "index": index, "middle": middle,
                              "ring": ring, "pinky": pinky},
                        pinch=pinch, hand_size=0.2, anchor=(0.5, 0.5), spread=1.0,
                        palm_facing=facing)


def jitter(rng, base, amp=0.02):
    return base + rng.uniform(-amp, amp)


def make_samples(seed=1, n=40) -> dict[str, list[HandFeatures]]:
    rng = random.Random(seed)
    j = lambda v, a=0.02: jitter(rng, v, a)  # noqa: E731
    return {
        "relaxed": [hf(j(.93), j(.95), j(.96), j(.94), j(.05, .01), j(-.7, .1)) for _ in range(n)],
        "fold_index": [hf(j(.62), j(.75), j(.96), j(.94), j(.05, .01)) for _ in range(n)],
        "fold_middle": [hf(j(.93), j(.62), j(.96), j(.94), j(.05, .01)) for _ in range(n)],
        "pinch": [hf(j(.9), j(.93), j(.95), j(.93), j(.012, .004)) for _ in range(n)],
        "fist": [hf(j(.4), j(.35), j(.4), j(.4), j(.04, .01)) for _ in range(n)],
        "three": [hf(j(.98), j(.98), j(.98), j(.6, .05), j(.08, .01)) for _ in range(n)],
        "flip": [hf(j(.95), j(.95), j(.95), j(.95), j(.05, .01), j(.7, .1)) for _ in range(n)],
    }


def test_full_synthetic_run_has_no_problems_and_matches_predicates():
    res = cal.compute(make_samples(), CUR)
    assert res.problems == {}
    v = {k: val for (_, k), val in res.values.items()}
    assert set(v) == {k for _, k in cal.RESULT_KEYS}
    assert v["normal_facing"] == -1
    assert 0.3 <= v["flip_facing_min"] <= 0.5
    assert v["click_curl_down"] > v["curl_down"]
    assert v["curl_up"] > v["curl_down"] and v["pinch_down"] < v["pinch_up"]
    assert v["switcher_gap_hold"] < v["switcher_gap_on"]

    # ค่าที่ได้ต้องทำให้ predicate ของ gestures แยกท่าได้จริง
    from dataclasses import replace
    cfg = replace(CUR, **{k: val for k, val in v.items()})
    s = make_samples(seed=2)
    assert all(is_fist(f.curl, cfg) for f in s["fist"])
    assert not any(is_fist(f.curl, cfg) for f in s["relaxed"])
    assert all(is_switcher_pose(f.curl, cfg) for f in s["three"])
    assert not any(is_switcher_pose(f.curl, cfg) for f in s["relaxed"])
    assert sum(is_click_left(f.curl, f.pinch, cfg) for f in s["fold_index"]) >= 0.9 * 40
    assert sum(is_click_right(f.curl, f.pinch, cfg) for f in s["fold_middle"]) >= 0.9 * 40
    assert not any(is_click_left(f.curl, f.pinch, cfg) for f in s["relaxed"])
    assert all(is_flip(f.palm_facing, cfg.normal_facing, cfg) for f in s["flip"])
    assert not any(is_flip(f.palm_facing, cfg.normal_facing, cfg) for f in s["relaxed"])


def test_values_are_rounded():
    res = cal.compute(make_samples(), CUR)
    for (_, k), v in res.values.items():
        if k.startswith("pinch"):
            assert round(v, 4) == v
        elif k != "normal_facing":
            assert round(v, 3) == v
    assert isinstance(res.values[("gesture", "normal_facing")], int)


def test_too_few_samples_is_a_problem():
    s = make_samples()
    s["fist"] = s["fist"][:5]
    del s["flip"]
    res = cal.compute(s, CUR)
    assert res.problems["fist"] == "เห็นมือน้อยเกินไป"
    assert res.problems["flip"] == "เห็นมือน้อยเกินไป"
    assert ("gesture", "curl_down") not in res.values
    assert ("gesture", "flip_facing_min") not in res.values
    assert ("gesture", "pinch_down") in res.values  # ท่าอื่นยังคำนวณได้


def test_fist_indistinguishable_from_relaxed():
    s = make_samples()
    s["fist"] = [hf(.9, .9, .9, .9) for _ in range(30)]
    res = cal.compute(s, CUR)
    assert "แยกท่าไม่ออก" in res.problems["fist"]
    assert ("gesture", "curl_down") not in res.values


def test_fold_index_not_folded_reports_that_pose():
    s = make_samples()
    s["fold_index"] = [hf(.92, .95) for _ in range(30)]
    res = cal.compute(s, CUR)
    assert "แยกท่าไม่ออก" in res.problems["fold_index"]
    assert ("gesture", "click_curl_down") not in res.values
    assert ("gesture", "curl_down") in res.values


def test_click_takes_stricter_finger():
    s = make_samples()
    # กลางพับลึกกว่าชี้ → เกณฑ์ down ของกลางต่ำกว่า ต้องเลือกค่าที่ต่ำกว่า (เข้มกว่า)
    rng = random.Random(3)
    s["fold_middle"] = [hf(.93, jitter(rng, .4, .01)) for _ in range(40)]
    res = cal.compute(s, CUR)
    d_idx = cal.split_thresholds([f.curl["index"] for f in s["relaxed"]],
                                 [f.curl["index"] for f in s["fold_index"]])[0]
    d_mid = cal.split_thresholds([f.curl["middle"] for f in s["relaxed"]],
                                 [f.curl["middle"] for f in s["fold_middle"]])[0]
    assert d_mid < d_idx
    assert res.values[("gesture", "click_curl_down")] == round(d_mid, 3)
    assert res.values[("gesture", "click_curl_up")] <= min(
        cal.percentile([f.curl[n] for f in s["relaxed"]], 5) for n in ("index", "middle"))


def test_three_not_separated_from_relaxed():
    s = make_samples()
    s["three"] = [hf(.98, .98, .98, .93) for _ in range(30)]
    res = cal.compute(s, CUR)
    assert "three" in res.problems
    assert ("gesture", "switcher_gap_on") not in res.values
    assert ("gesture", "switcher_gap_hold") not in res.values
    assert ("gesture", "switcher_pinky_open") in res.values


def test_still_relaxed_capture_does_not_loosen_switcher_below_real_use_noise():
    # มือปกตินิ่งมาก (gap ~ -0.03, ก้อย 0.945) ตามข้อมูลจริง: เกณฑ์ต้องไม่หลวมกว่าพื้น noise ตอนใช้จริง
    s = make_samples()
    s["relaxed"] = [hf(index=0.918, middle=0.95, ring=0.972, pinky=0.945) for _ in range(60)]
    r = cal.compute(s, GestureConfig())
    hold = r.values[("gesture", "switcher_gap_hold")]
    on = r.values[("gesture", "switcher_gap_on")]
    assert hold >= cal.REAL_USE_GAP_P99 and on > hold
    assert r.values[("gesture", "switcher_pinky_open")] <= cal.PINKY_OPEN_MAX


def test_flip_same_side_as_normal_is_problem():
    s = make_samples()
    s["flip"] = [hf(facing=-0.7) for _ in range(30)]
    res = cal.compute(s, CUR)
    assert "flip" in res.problems
    assert ("gesture", "flip_facing_min") not in res.values


def test_relaxed_facing_ambiguous():
    s = make_samples()
    s["relaxed"] = [hf(facing=0.1) for _ in range(30)]
    res = cal.compute(s, CUR)
    assert "relaxed" in res.problems
    assert ("gesture", "normal_facing") not in res.values


def test_click_must_be_above_fist():
    s = make_samples()
    # กำมือแค่ 0.55 แต่พับชี้ได้ 0.5 → click_down (<fist's) ไม่สอดคล้อง
    s["fist"] = [hf(.88, .88, .88, .88) for _ in range(30)]
    s["fold_index"] = [hf(.86, .95) for _ in range(30)]
    s["fold_middle"] = [hf(.93, .86) for _ in range(30)]
    res = cal.compute(s, CUR)
    assert res.problems  # อย่างน้อยหนึ่งท่าถูกรายงาน
    assert ("gesture", "curl_down") not in res.values or \
        res.values[("gesture", "click_curl_down")] > res.values[("gesture", "curl_down")]


def test_pinch_range_check():
    s = make_samples()
    s["pinch"] = [hf(pinch=0.0) for _ in range(30)]  # ปลายนิ้วทับกันเป๊ะ: เกณฑ์ยังอยู่ในช่วง
    res = cal.compute(s, CUR)
    assert res.values[("gesture", "pinch_down")] >= 0.005 or "pinch" in res.problems


def test_percentile():
    assert cal.percentile([1, 2, 3, 4, 5], 50) == 3
    assert cal.percentile([0, 10], 25) == 2.5
    assert cal.percentile([7], 99) == 7


def test_summary_rows():
    res = cal.compute(make_samples(), CUR)
    rows = cal.summary_rows(res, CUR)
    assert [r[0] for r in rows] == [k for _, k in cal.RESULT_KEYS]
    by = {r[0]: r for r in rows}
    assert by["curl_down"][1] == "0.7"
    assert by["normal_facing"][1] == "0"


def test_samples_to_jsonable_is_json_serialisable():
    import json
    out = cal.samples_to_jsonable({"relaxed": [hf()]})
    assert json.loads(json.dumps(out))["relaxed"][0]["curl"]["index"] == 0.93


# ---------------------------------------------------------------- CalibrationSession
def test_session_flow_counts_only_new_seq_with_hand():
    poses = [cal.PoseSpec("a", "A", "x", 3.0), cal.PoseSpec("b", "B", "y", 3.0)]
    s = cal.CalibrationSession(poses, prepare_s=3.0)
    s.restart_prepare(0.0)
    assert not s.feed(1, hf())        # ยังอยู่ช่วงเตรียม ไม่เก็บ
    assert s.countdown(1.0) == pytest.approx(2.0)
    assert not s.tick(2.9) and s.phase == "prepare"
    assert s.tick(3.0) and s.phase == "capture"
    assert s.feed(2, hf())
    assert not s.feed(2, hf())        # seq ซ้ำ
    assert not s.feed(3, None)        # ไม่เห็นมือ
    assert s.feed(4, hf())
    assert s.progress(4.5) == pytest.approx(0.5)
    assert s.tick(6.0) and s.phase == "prepare" and s.current.id == "b"
    assert len(s.samples["a"]) == 2
    s.tick(9.0)
    s.feed(10, hf())
    assert s.tick(12.0) and s.finished
    assert len(s.samples["b"]) == 1


def test_session_redo_returns_to_review():
    poses = [cal.PoseSpec("a", "A", "x", 1.0), cal.PoseSpec("b", "B", "y", 1.0)]
    s = cal.CalibrationSession(poses, prepare_s=1.0)
    s.restart_prepare(0.0)
    for t in (1.0, 2.0, 3.0, 4.0):
        s.tick(t)
    assert s.finished
    s.redo("a", 10.0)
    assert s.phase == "prepare" and s.current.id == "a" and s.samples["a"] == []
    s.tick(11.0)
    s.feed(1, hf())
    s.tick(12.0)
    assert s.finished and len(s.samples["a"]) == 1


def test_session_restart_clears_current_pose():
    s = cal.CalibrationSession([cal.PoseSpec("a", "A", "x", 1.0)], prepare_s=1.0)
    s.restart_prepare(0.0)
    s.tick(1.0)
    s.feed(1, hf())
    s.restart_prepare(1.5)
    assert s.phase == "prepare" and s.samples["a"] == []
