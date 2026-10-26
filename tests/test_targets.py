"""ทดสอบ pure helper ของ targets (ไม่ต้องใช้ COM)"""
from __future__ import annotations

import sys
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)

from airmouse.platform.win32 import targets as T  # noqa: E402
from airmouse.platform.win32.targets import (  # noqa: E402
    Target, TargetFinder, dedupe, keep_element, ring_points, split_caption_buttons)


def test_target_center_contains():
    t = Target(10, 20, 30, 60, "button")
    assert t.center == (20.0, 40.0)
    assert t.contains(10, 20) and t.contains(30, 60)
    assert not t.contains(9, 40)
    assert t.contains(5, 40, margin=5)
    assert not t.contains(4, 40, margin=5)
    assert t.distance_to(20, 40) == 0.0
    assert t.distance_to(40, 40) == pytest.approx(10.0)


def test_split_caption_buttons():
    ts = split_caption_buttons(1000, 10, 1138, 47)
    assert [t.kind for t in ts] == ["caption_min", "caption_max", "caption_close"]
    assert ts[0].left == 1000 and ts[2].right == pytest.approx(1138)
    assert ts[0].right == pytest.approx(ts[1].left)
    assert all(t.top == 10 and t.bottom == 47 for t in ts)
    assert all(t.right - t.left == pytest.approx(46) for t in ts)


def test_split_caption_buttons_degenerate():
    assert split_caption_buttons(1749, 0, 1749, 37) == []  # แอปวาด caption เอง (กว้าง 0)
    assert split_caption_buttons(0, 0, 100, 2) == []


def test_keep_element():
    ok = dict(control_type=50000, w=40, h=30, enabled=True, offscreen=False, pid=5, own_pid=9)
    assert keep_element(**ok)
    assert not keep_element(**{**ok, "control_type": 50033})  # pane
    assert not keep_element(**{**ok, "enabled": False})
    assert not keep_element(**{**ok, "offscreen": True})
    assert not keep_element(**{**ok, "pid": 9})
    assert not keep_element(**{**ok, "w": 5})
    assert not keep_element(**{**ok, "h": 5})
    assert not keep_element(**{**ok, "w": 401})
    assert not keep_element(**{**ok, "h": 121})
    assert keep_element(**{**ok, "w": 400, "h": 120})
    assert keep_element(**{**ok, "w": 6, "h": 6})


def test_dedupe_keeps_first_and_order():
    a = Target(0, 0, 10, 10, "button", "a")
    a2 = Target(0.2, 0, 10.3, 10, "link", "dup")
    b = Target(20, 0, 30, 10, "tab")
    assert dedupe([a, b, a2]) == [a, b]
    assert dedupe([]) == []


def test_ring_points():
    pts = ring_points(100, 100, 48)
    assert len(pts) == 9 and pts[0] == (100, 100)
    assert (148, 100) in pts and (100, 148) in pts


def test_control_kinds_match_typelib():
    cc = pytest.importorskip("comtypes.client")
    mod = cc.GetModule("UIAutomationCore.dll")
    names = {"Button": "button", "CheckBox": "checkbox", "ComboBox": "combobox",
             "Hyperlink": "link", "ListItem": "listitem", "MenuItem": "menuitem",
             "RadioButton": "radio", "TabItem": "tab", "TreeItem": "treeitem",
             "SplitButton": "splitbutton"}
    for n, kind in names.items():
        assert T.CONTROL_KINDS[getattr(mod, f"UIA_{n}ControlTypeId")] == kind


def test_finder_without_uia_construct_and_stop():
    f = TargetFinder(use_uia=False)
    assert f.targets == []
    f.set_probe(10, 10)
    f.set_active(False)
    assert f.stats["queries"] == 0
    f.stop()  # ไม่เคย start ก็ไม่ error


def test_finder_inactive_makes_no_queries_and_stops():
    f = TargetFinder(use_uia=False)
    f.start()
    time.sleep(0.3)
    assert f.stats["queries"] == 0
    f.stop()
    f.join(2)
    assert not f.is_alive()


def test_choose_child_containing_smallest():
    rects = [(0, 0, 100, 100), (10, 10, 40, 40), (50, 50, 60, 60), (200, 200, 300, 300)]
    assert T.choose_child_containing(rects, (20, 20)) == 1
    assert T.choose_child_containing(rects, (55, 55)) == 2
    assert T.choose_child_containing(rects, (90, 90)) == 0
    assert T.choose_child_containing(rects, (150, 150)) is None
    assert T.choose_child_containing([], (1, 1)) is None


def test_choose_child_containing_ignores_empty_and_ties_pick_last():
    assert T.choose_child_containing([(5, 5, 5, 5), (0, 0, 10, 10)], (5, 5)) == 1
    assert T.choose_child_containing([(0, 0, 10, 10), (0, 0, 10, 10)], (5, 5)) == 1


def test_same_app_by_pid_or_exe(monkeypatch):
    monkeypatch.setattr(T, "_exe_name", lambda pid: {1: "a.exe", 2: "a.exe", 3: "b.exe", 4: ""}[pid])
    assert T._same_app(1, 1)
    assert T._same_app(1, 2)
    assert not T._same_app(1, 3)
    assert not T._same_app(4, 3)
