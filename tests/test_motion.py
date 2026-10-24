from dataclasses import dataclass

import pytest

from airmouse.config import MotionConfig
from airmouse.motion import V_HI, V_LO, Motion, gain_curve, soft_deadzone

DT = 1 / 30


@dataclass
class B:
    x: float = 0.0
    y: float = 0.0
    w: float = 10000.0
    h: float = 10000.0


def _motion(px_per_hand: float = 1000.0, bounds: B | None = None, **kw) -> Motion:
    m = Motion(MotionConfig(**kw), bounds or B(), px_per_hand)
    m.reset((5000.0, 5000.0))
    return m


def test_soft_deadzone_values_and_continuity():
    dz = 0.01
    assert soft_deadzone(0.0, dz) == 0.0
    assert soft_deadzone(dz, dz) == 0.0
    assert soft_deadzone(0.5 * dz, dz) == 0.0
    assert soft_deadzone(2 * dz, dz) == pytest.approx(0.5 * dz)
    e = 1e-7
    # ค่าต่อเนื่องที่ dz และ 2dz
    assert soft_deadzone(dz + e, dz) == pytest.approx(0.0, abs=1e-8)
    assert soft_deadzone(2 * dz - e, dz) == pytest.approx(soft_deadzone(2 * dz + e, dz), abs=1e-6)
    # ความชันต่อเนื่อง: 0 ที่ dz และ 1 ที่ 2dz
    slope = lambda m: (soft_deadzone(m + e, dz) - soft_deadzone(m - e, dz)) / (2 * e)
    assert slope(dz + 1e-4) == pytest.approx(0.0, abs=0.02)
    assert slope(2 * dz - 1e-4) == pytest.approx(1.0, abs=0.02)
    assert slope(2 * dz + 1e-4) == pytest.approx(1.0, abs=0.02)


def test_soft_deadzone_monotonic():
    vals = [soft_deadzone(i * 0.0005, 0.01) for i in range(200)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))


def test_gain_curve_monotonic_and_bounded():
    low, high = 0.4, 2.5
    vs = [i * 0.1 for i in range(100)]
    gs = [gain_curve(v, low, high) for v in vs]
    assert all(b >= a - 1e-12 for a, b in zip(gs, gs[1:]))
    assert min(gs) == pytest.approx(low) and max(gs) == pytest.approx(high)
    assert gain_curve(V_LO, low, high) == pytest.approx(low)
    assert gain_curve(V_HI, low, high) == pytest.approx(high)


def test_first_update_after_reset_has_no_movement():
    m = _motion()
    assert m.update((0.5, 0.5), 0.2, 1.0, DT) == (5000.0, 5000.0)
    m.update((0.6, 0.5), 0.2, 1.0, DT)
    m.reset((100.0, 200.0))
    assert m.update((0.9, 0.9), 0.2, 1.0, DT) == (100.0, 200.0)


def test_subpixel_motion_accumulates():
    px_per_hand = 10.0
    m = _motion(px_per_hand=px_per_hand)
    step_hs = 0.02  # ใหญ่กว่า deadzone เล็กน้อย (ความเร็ว < V_LO → gain ต่ำสุด)
    size = 0.2
    cfg = m.cfg
    x = 0.1
    m.update((x, 0.5), size, 1.0, DT)
    for _ in range(100):  # ให้ฟิลเตอร์นิ่ง
        x += step_hs * size
        out = m.update((x, 0.5), size, 1.0, DT)
    start = out[0]
    n = 30
    for _ in range(n):
        x += step_hs * size
        out = m.update((x, 0.5), size, 1.0, DT)
    m2 = soft_deadzone(step_hs, cfg.deadzone)
    gain = gain_curve(m2 / DT, cfg.accel_low_gain, cfg.accel_high_gain)
    expected = n * m2 * gain * cfg.sensitivity * px_per_hand
    assert 0.5 < expected < n  # ต่ำกว่า 1 px/เฟรม จริง
    assert out[0] - start == pytest.approx(expected, rel=0.03)
    assert out[1] == pytest.approx(5000.0)


def test_hand_size_compensation():
    def run(size: float, step: float) -> float:
        m = _motion()
        x = 0.2
        m.update((x, 0.5), size, 1.0, DT)
        out = (0.0, 0.0)
        for _ in range(20):
            x += step
            out = m.update((x, 0.5), size, 1.0, DT)
        return out[0]

    assert run(0.4, 0.02) == pytest.approx(run(0.2, 0.01))


def test_clamp_at_edge_has_no_windup():
    b = B(0, 0, 100, 100)
    m = Motion(MotionConfig(), b, 1000.0)
    m.reset((50.0, 50.0))
    x = 0.1
    m.update((x, 0.5), 0.2, 1.0, DT)
    for _ in range(60):
        x += 0.02
        out = m.update((x, 0.5), 0.2, 1.0, DT)
    assert out[0] <= 99.0
    prev = out[0]
    x -= 0.02
    out = m.update((x, 0.5), 0.2, 1.0, DT)
    assert out[0] < prev  # ตอบสนองทันที ไม่ต้องไล่คืนระยะที่เกินขอบ
    assert m._pos[0] < 99.0


def test_late_frame_does_not_spike_speed():
    m = _motion()
    x = 0.1
    m.update((x, 0.5), 0.2, 1.0, DT)
    for _ in range(30):
        x += 0.005
        m.update((x, 0.5), 0.2, 1.0, DT)
    before = m.speed
    x += 0.05
    m.update((x, 0.5), 0.2, 1.0, 0.2)
    assert m.speed == before


def test_slow_and_fast_motion_get_different_gain():
    def run(step: float) -> float:
        m = _motion()
        x = 0.1
        m.update((x, 0.5), 0.2, 1.0, DT)
        for _ in range(60):
            x += step
            m.update((x, 0.5), 0.2, 1.0, DT)
        return m.gain

    slow, fast = run(0.004), run(0.04)
    assert slow == pytest.approx(0.4)
    assert fast == pytest.approx(2.5)
