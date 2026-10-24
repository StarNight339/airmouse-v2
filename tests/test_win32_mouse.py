"""ทดสอบ logic ล้วนของ win32 mouse (ไม่เรียก SendInput / ไม่ขยับเคอร์เซอร์จริง)"""
from __future__ import annotations

import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)

from airmouse.platform.win32 import mouse as m  # noqa: E402
from airmouse.platform.win32.mouse import Rect, interpolate, to_absolute  # noqa: E402


def test_sizeof_input_x64():
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(m.INPUT) == 40


def test_to_absolute_single_monitor_corners():
    r = Rect(0, 0, 1920, 1080)
    assert to_absolute(0, 0, r) == (0, 0)
    assert to_absolute(1919, 1079, r) == (65535, 65535)


def test_to_absolute_negative_origin():
    r = Rect(-1920, 0, 3840, 1080)
    assert to_absolute(-1920, 0, r)[0] == 0
    assert to_absolute(1919, 0, r)[0] == 65535
    assert abs(to_absolute(0, 0, r)[0] - 32776) <= 2


def test_to_absolute_clamps():
    r = Rect(0, 0, 1920, 1080)
    assert to_absolute(-500, -500, r) == (0, 0)
    assert to_absolute(99999, 99999, r) == (65535, 65535)


def test_to_absolute_subpixel_monotonic():
    r = Rect(0, 0, 1920, 1080)
    a = to_absolute(100.0, 50.0, r)
    b = to_absolute(100.5, 50.5, r)
    c = to_absolute(101.0, 51.0, r)
    assert a[0] < b[0] < c[0]
    assert a[1] < b[1] < c[1]


def test_interpolate_basic():
    s, t = (0.0, 0.0), (100.0, 50.0)
    assert interpolate(s, t, 10.0, 1.0, 10.0) == s
    assert interpolate(s, t, 10.0, 1.0, 10.5) == pytest.approx((50.0, 25.0))
    assert interpolate(s, t, 10.0, 1.0, 11.0) == t
    assert interpolate(s, t, 10.0, 1.0, 99.0) == t
    assert interpolate(s, t, 10.0, 1.0, 5.0) == s  # ก่อนเริ่ม


def test_interpolate_bad_duration():
    s, t = (0.0, 0.0), (10.0, 10.0)
    assert interpolate(s, t, 0.0, 0.0, 0.0) == t
    assert interpolate(s, t, 0.0, -1.0, 5.0) == t


def test_stats_math_and_reset():
    th = m.MouseThread(hz=100)  # ไม่ start thread
    for dt in (0.010, 0.010, 0.030):  # 0.030 > 2*period -> late
        th._record(dt)
    s = th.stats
    assert s["ticks"] == 3
    assert s["late"] == 1
    assert s["dt_max_ms"] == pytest.approx(30.0)
    assert s["dt_avg_ms"] == pytest.approx(50.0 / 3)
    s2 = th.stats
    assert s2["ticks"] == 0 and s2["late"] == 0 and s2["dt_max_ms"] == 0.0


def test_set_target_clamps_duration_and_starts_from_current():
    th = m.MouseThread()
    th.set_target(10.0, 10.0, 5.0)
    assert th._seg_dur == m.MAX_SEGMENT_S
    th.set_target(20.0, 20.0, 0.0)
    assert th._seg_dur == m.MIN_SEGMENT_S
    assert th._seg_target == (20.0, 20.0)
    assert not th.user_moved
