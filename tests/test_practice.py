"""เทสต์ helper ล้วนของโหมดฝึก (ไม่เปิด widget)"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime

import pytest

from airmouse.ui.practice import (
    ScrollHoldTracker, drag_success, hit_test, make_targets, save_results, summarize,
)


def test_make_targets_counts_and_spacing():
    for seed in range(20):
        ts = make_targets(random.Random(seed), 1920, 1080)
        assert len(ts) == 14
        kinds = [t.kind for t in ts]
        assert kinds.count("click") == 10 and kinds.count("double") == 2
        assert kinds.count("right") == 2
        for t in ts:
            assert 12 <= t.r <= 32
            assert t.r + 80 <= t.x <= 1920 - 80 - t.r
            assert t.y - t.r >= 150 and t.y + t.r <= 1080 - 80
        for a, b in zip(ts, ts[1:]):
            assert math.hypot(a.x - b.x, a.y - b.y) >= 150


def test_make_targets_seedable():
    a = make_targets(random.Random(7), 1600, 900)
    b = make_targets(random.Random(7), 1600, 900)
    assert a == b
    assert a != make_targets(random.Random(8), 1600, 900)


def test_make_targets_small_screen_still_spaced():
    ts = make_targets(random.Random(1), 800, 600)
    assert all(math.hypot(a.x - b.x, a.y - b.y) >= 150 for a, b in zip(ts, ts[1:]))


def test_hit_test():
    assert hit_test(100, 100, 20, 100, 100) == (True, 0.0)
    assert hit_test(100, 100, 20, 120, 100) == (True, 0.0)  # บนขอบ
    hit, d = hit_test(100, 100, 20, 150, 100)
    assert not hit and d == pytest.approx(30)


def test_drag_success_uses_ring_minus_ball():
    ok, d = drag_success(110, 100, 20, 100, 100, 60)
    assert ok and d == pytest.approx(10)
    assert drag_success(140, 100, 20, 100, 100, 60)[0]      # ขอบพอดี (40)
    assert not drag_success(141, 100, 20, 100, 100, 60)[0]  # เลยนิดเดียว


def test_scroll_tracker_hold_completes():
    t = ScrollHoldTracker(0.5)
    assert not t.update(True, 0.0)
    assert not t.update(True, 0.4)
    assert t.update(True, 0.5)
    assert t.overshoots == 0 and t.done


def test_scroll_tracker_counts_overshoot_and_resets_hold():
    t = ScrollHoldTracker(0.5)
    t.update(False, 0.0)
    t.update(True, 0.1)
    t.update(False, 0.3)  # เลยกรอบก่อนครบ = overshoot 1
    assert t.overshoots == 1
    t.update(True, 0.4)
    assert not t.update(True, 0.8)   # นับใหม่จาก 0.4 ยังไม่ถึง 0.5
    t.update(False, 0.85)
    assert t.overshoots == 2
    t.update(True, 1.0)
    assert t.update(True, 1.5)
    assert t.overshoots == 2


def test_summarize():
    trials = [
        {"stage": "click", "hit": False, "time_s": 1.0},
        {"stage": "click", "hit": True, "time_s": 2.0},
        {"stage": "click", "hit": True, "time_s": 4.0},
        {"stage": "drag", "hit": True, "time_s": 3.0},
        {"stage": "scroll", "hit": True, "time_s": 5.0, "overshoots": 2},
        {"stage": "scroll", "hit": True, "time_s": 7.0, "overshoots": 1},
    ]
    s = summarize(trials)
    assert s["click"]["attempts"] == 3 and s["click"]["hits"] == 2
    assert s["click"]["accuracy"] == pytest.approx(66.7)
    assert s["click"]["mean_time_s"] == 3.0 and s["click"]["median_time_s"] == 3.0
    assert s["drag"]["accuracy"] == 100.0
    assert s["scroll"]["overshoots"] == 3 and s["scroll"]["mean_time_s"] == 6.0


def test_summarize_empty_stage():
    s = summarize([])
    assert s["click"]["attempts"] == 0 and s["click"]["accuracy"] is None
    assert s["click"]["mean_time_s"] is None


def test_save_results(tmp_path):
    trials = [{"stage": "click", "hit": True, "time_s": 1.5}]
    path = save_results(tmp_path / "logs", trials, summarize(trials),
                        now=datetime(2026, 10, 27, 20, 30, 15))
    assert path.name == "practice-20261027-203015.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["trials"] == trials and data["summary"]["click"]["hits"] == 1
