"""ทดสอบ logic ล้วนของ win32 mouse (ไม่เรียก SendInput / ไม่ขยับเคอร์เซอร์จริง)"""
from __future__ import annotations

import ctypes
import logging
import math
import sys
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)

from airmouse.platform.win32 import mouse as m  # noqa: E402
from airmouse.platform.win32.mouse import Rect, interpolate, to_absolute  # noqa: E402


def test_struct_sizes_x64():
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(m.INPUT) == 40
        assert ctypes.sizeof(m.RAWINPUTHEADER) == 24
        assert ctypes.sizeof(m.RAWMOUSE) == 24
        assert ctypes.sizeof(m.RAWINPUTDEVICE) == 16


@pytest.mark.parametrize("rect", [Rect(0, 0, 1920, 1080), Rect(-1920, 0, 3840, 1080)])
def test_to_absolute_round_trips_every_pixel(rect):
    # Windows แปลงกลับด้วย floor(n * size / 65536) -> ต้องได้พิกเซลเดิมทุกพิกเซล
    for axis, (origin, size) in enumerate(((rect.x, rect.w), (rect.y, rect.h))):
        for p in range(origin, origin + size):
            pos = (p, rect.y) if axis == 0 else (rect.x, p)
            n = to_absolute(pos[0], pos[1], rect)[axis]
            assert 0 <= n <= 65535
            assert (n * size) // 65536 + origin == p


def test_to_absolute_negative_origin():
    r = Rect(-1920, 0, 3840, 1080)
    assert to_absolute(-1920, 0, r)[0] < 20
    assert abs(to_absolute(0, 0, r)[0] - 32768) < 20
    assert to_absolute(1919, 0, r)[0] > 65500


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


# ---- parse WM_INPUT ----
def _raw_buf(hdevice, dx=0, dy=0, btn=0, typ=0, extra=0) -> bytes:
    ri = m.RAWINPUT()
    ri.header.dwType = typ
    ri.header.dwSize = ctypes.sizeof(m.RAWINPUT)
    ri.header.hDevice = hdevice
    ri.mouse.lLastX = dx
    ri.mouse.lLastY = dy
    ri.mouse.usButtonFlags = btn
    ri.mouse.ulExtraInformation = extra
    return bytes(ri)


def test_raw_user_vs_ours():
    f = m.is_user_mouse_input
    assert f(_raw_buf(0x1234, dx=3))
    assert f(_raw_buf(0x1234, dy=-1))
    assert f(_raw_buf(0x1234, btn=1))
    assert f(_raw_buf(0, dx=5, extra=77))  # touchpad/RDP: hDevice NULL แต่ไม่ใช่ magic = ผู้ใช้
    assert not f(_raw_buf(0, dx=50, dy=50, extra=m.INJECT_MAGIC))  # ของเรา
    assert not f(_raw_buf(0x1234, dx=3, extra=m.INJECT_MAGIC))
    assert not f(_raw_buf(0x1234))  # ไม่มีการขยับ
    assert not f(_raw_buf(0x1234, dx=3, typ=1))  # ไม่ใช่เมาส์
    assert not f(b"\x00" * 10)  # สั้นเกิน


def test_send_tags_injected_input(monkeypatch):
    seen = {}

    class FakeUser32:
        @staticmethod
        def SendInput(n, ptr, size):
            seen["extra"] = ptr._obj.mi.dwExtraInfo
            return 1

    monkeypatch.setattr(m, "_user32", FakeUser32)
    assert m._send(m.MOUSEEVENTF_MOVE)
    assert seen["extra"] == m.INJECT_MAGIC


def test_cursor_read_failure_never_raises(monkeypatch):
    def boom():
        raise OSError("secure desktop")

    monkeypatch.setattr(m, "get_cursor", boom)
    th = m.MouseThread()  # __init__ ไม่ raise
    th.set_target(100.4, 200.6, 0.03)
    assert th.resync() == (100.0, 201.0)  # ใช้ตำแหน่งภายในเดิม


def test_fallback_detects_user_motion_right_after_resync(rig):
    th, st = rig
    th._fallback = True
    th.set_active(True)
    th.resync()  # cursor (500,500) seed _recent
    st["cursor"] = (800, 800)
    th.set_target(500, 500, 0.03)
    th._tick(_now())
    th._tick(_now())
    assert th.user_moved


# ---- stats / set_target ----
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


def test_set_target_clamps_duration():
    th = m.MouseThread()
    th.set_target(10.0, 10.0, 5.0)
    assert th._seg_dur == m.MAX_SEGMENT_S
    th.set_target(20.0, 20.0, 0.0)
    assert th._seg_dur == m.MIN_SEGMENT_S
    assert th._seg_target == (20.0, 20.0)
    assert isinstance(th.screen, Rect)


# ---- drive _tick โดย mock syscalls ----
@pytest.fixture
def rig(monkeypatch):
    state = {"cursor": (500, 500), "sent": [], "ok": True}
    monkeypatch.setattr(m, "get_cursor", lambda: state["cursor"])

    def fake_move(x, y, rect=None):
        if not state["ok"]:
            return False
        state["sent"].append((x, y))
        return True

    monkeypatch.setattr(m, "move_abs", fake_move)
    th = m.MouseThread()
    return th, state


def _now():
    return time.perf_counter() + 10  # หลังจบทุก segment


def test_tick_inactive_sends_nothing(rig):
    th, st = rig
    th.set_target(700, 700, 0.03)
    th._tick(_now())
    assert st["sent"] == []


def test_tick_sends_then_not_again_when_unchanged(rig):
    th, st = rig
    th.set_active(True)
    th.set_target(700, 650, 0.03)
    th._tick(_now())
    th._tick(_now())
    assert st["sent"] == [(700, 650)]


def test_resync_then_tick_sends_nothing_until_target_changes(rig):
    th, st = rig
    th.set_active(True)
    st["cursor"] = (321, 123)  # ผู้ใช้ขยับเมาส์ไปตำแหน่งนี้
    assert th.resync() == (321.0, 123.0)
    th._tick(_now())
    assert st["sent"] == []  # H1: ต้องไม่ดึงกลับ
    th.set_target(400, 200, 0.03)
    th._tick(_now())
    assert st["sent"] == [(400, 200)]


def test_user_input_stops_sending_until_resync(rig):
    th, st = rig
    th.set_active(True)
    th.set_target(700, 650, 0.03)
    th._on_user_input()
    assert th.user_moved
    th._tick(_now())
    assert st["sent"] == []
    th.resync()
    assert not th.user_moved
    th.set_target(710, 660, 0.03)
    th._tick(_now())
    assert st["sent"] == [(710, 660)]


def test_send_failure_backs_off(rig):
    th, st = rig
    th.set_active(True)
    th.set_target(700, 650, 0.03)
    st["ok"] = False
    t = _now()
    th._tick(t)
    st["ok"] = True
    th._tick(t + 0.01)  # ยังอยู่ในช่วง backoff
    assert st["sent"] == []
    th._tick(t + m.SEND_BACKOFF_S + 0.01)
    assert st["sent"] == [(700, 650)]


def test_fallback_needs_persistent_deviation(rig):
    th, st = rig
    th._fallback = True
    th.set_active(True)
    th.set_target(700, 650, 0.03)
    th._tick(_now())  # ส่ง (700,650)
    st["cursor"] = (700, 650)
    th.set_target(710, 650, 0.03)
    th._tick(_now())
    assert not th.user_moved
    st["cursor"] = (900, 900)  # ผู้ใช้ขยับ: ครั้งแรกยังไม่ตัดสิน
    th.set_target(720, 650, 0.03)
    th._tick(_now())
    assert not th.user_moved
    th.set_target(730, 650, 0.03)
    th._tick(_now())
    assert th.user_moved


def test_rate_limited_log(caplog):
    t = [0.0]
    rl = m.RateLimitedLog(logging.getLogger("rl-test"), interval=5.0, clock=lambda: t[0])
    with caplog.at_level(logging.WARNING, logger="rl-test"):
        for _ in range(100):
            rl.warning("boom")
            t[0] += 0.005
        assert len(caplog.records) == 1
        t[0] = 6.0
        rl.warning("boom")
    assert len(caplog.records) == 2
    assert "99" in caplog.records[1].getMessage()


def test_send_failure_log_is_rate_limited(monkeypatch, caplog):
    t = [0.0]
    monkeypatch.setattr(m, "_send_fail_log",
                        m.RateLimitedLog(m.log, interval=5.0, clock=lambda: t[0]))

    class FakeUser32:
        @staticmethod
        def SendInput(*a):
            return 0

    monkeypatch.setattr(m, "_user32", FakeUser32)
    with caplog.at_level(logging.WARNING, logger=m.log.name):
        for _ in range(50):
            assert m._send(m.MOUSEEVENTF_MOVE) is False
    assert len(caplog.records) == 1
    assert math.isfinite(t[0])


# ---- คิวคำสั่ง: click / drag / scroll ----
@pytest.fixture
def qrig(rig, monkeypatch):
    th, st = rig
    events = []
    st["events"] = events
    monkeypatch.setattr(m, "_pressed", set())

    def fake_move(x, y, rect=None):
        events.append(("move", x, y))
        return True

    def fake_send(flags, dx=0, dy=0, data=0):
        events.append(("send", flags, data))
        return True

    monkeypatch.setattr(m, "move_abs", fake_move)
    monkeypatch.setattr(m, "_send", fake_send)
    return th, st, events


def test_click_at_order_and_no_user_moved(qrig):
    th, st, ev = qrig
    th.set_active(True)
    th.click_at(300.4, 200.6, "left")
    th._tick(_now())
    assert ev == [("move", 300, 201), ("send", m.MOUSEEVENTF_LEFTDOWN, 0),
                  ("send", m.MOUSEEVENTF_LEFTUP, 0)]
    assert not th.user_moved
    assert th._last_sent == (300, 201)
    th._tick(_now())  # target = จุดคลิกแล้ว ไม่ส่ง move ซ้ำ
    assert len(ev) == 3


def test_inactive_drops_click_scroll_down_but_runs_up(qrig):
    th, st, ev = qrig
    th.set_active(True)
    th.button_down("right")  # กดค้างไว้ตอน active
    th._tick(_now())
    th.set_active(False)
    ev.clear()
    th.click_at(10, 10)
    th.scroll(120)
    th.button_down("left")
    th.button_up("right")
    th._tick(_now())
    assert ev == [("send", m.MOUSEEVENTF_RIGHTUP, 0)]
    assert th.held_buttons == frozenset()


def test_drag_hold_and_user_moved_releases(qrig):
    th, st, ev = qrig
    th.set_active(True)
    th.button_down("left")
    th._tick(_now())
    assert th.held_buttons == frozenset({"left"})
    th._on_user_input()
    th._tick(_now())
    assert ("send", m.MOUSEEVENTF_LEFTUP, 0) in ev
    assert th.held_buttons == frozenset()


def test_pending_up_runs_even_when_user_moved(qrig):
    th, st, ev = qrig
    th.set_active(True)
    th.button_down("left")
    th._tick(_now())
    th._on_user_input()
    th.button_up("left")
    th.button_down("left")  # ถูกทิ้ง
    th._tick(_now())
    ups = [e for e in ev if e == ("send", m.MOUSEEVENTF_LEFTUP, 0)]
    assert ups == [("send", m.MOUSEEVENTF_LEFTUP, 0)]  # ปล่อยครั้งเดียว ไม่ซ้ำ
    assert th.held_buttons == frozenset()


def test_up_for_button_not_held_sends_nothing(qrig):
    # up ซ้ำอาจไปยกเลิกปุ่มที่ผู้ใช้กดเมาส์จริงอยู่ (เช่นตอนผู้ใช้เข้ามาควบคุมแทน)
    th, st, ev = qrig
    th.set_active(True)
    th.button_up("left")
    th._tick(_now())
    assert ev == []


def test_drain_on_exit_sends_pending_up_and_releases(qrig):
    th, st, ev = qrig
    th.set_active(True)
    th.button_down("left")
    th._tick(_now())
    ev.clear()
    th.click_at(5, 5)  # ถูกทิ้งตอน drain
    th.button_up("left")
    th._drain_on_exit()
    assert ev == [("send", m.MOUSEEVENTF_LEFTUP, 0)]
    assert th.held_buttons == frozenset()


def test_scroll_zero_skipped_and_nonzero_sent(qrig):
    th, st, ev = qrig
    th.set_active(True)
    th.scroll(0)
    th.scroll(-120)
    th._tick(_now())
    assert ev == [("send", m.MOUSEEVENTF_WHEEL, -120)]


def test_bad_button_rejected(qrig):
    th, _, _ = qrig
    with pytest.raises(ValueError):
        th.button_down("side")


def test_double_click_metrics_fallback(monkeypatch):
    class Bad:
        @staticmethod
        def GetDoubleClickTime():
            raise OSError("nope")

    monkeypatch.setattr(m, "_user32", Bad)
    assert m.double_click_metrics() == (500, 4, 4)


def test_double_click_metrics_real_sane():
    t, w, h = m.double_click_metrics()
    assert t > 0 and w > 0 and h > 0
