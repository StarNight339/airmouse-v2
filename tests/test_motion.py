import random
from dataclasses import dataclass

import pytest

from airmouse.config import MotionConfig
from airmouse.motion import HISTORY_S, V_HI, V_LO, Motion, gain_curve

DT = 1 / 30
R = 0.065         # รัศมี leash ที่ใช้ทดสอบ (hand-size)
SIZE = 0.18
ASPECT = 4 / 3
PPH = 1080.0


@dataclass
class B:
    x: float = 0.0
    y: float = 0.0
    w: float = 10000.0
    h: float = 10000.0


def _motion(px_per_hand: float = PPH, bounds: B | None = None, **kw) -> Motion:
    kw.setdefault("deadzone", R)
    m = Motion(MotionConfig(**kw), bounds or B(), px_per_hand)
    m.reset((5000.0, 5000.0))
    return m


FPS = [10, 15, 30]


def _noisy_still(sigma: float, seed: int, fps: int = 30, seconds: float = 9.0):
    rng = random.Random(seed)
    m = _motion()
    xs, ys = [], []
    for _ in range(int(seconds * fps)):
        out = m.update((0.5 + rng.gauss(0, sigma), 0.5 + rng.gauss(0, sigma)), SIZE, ASPECT, 1 / fps)
        xs.append(out[0])
        ys.append(out[1])
    return xs, ys


def _drive(dist_hs: float, dur: float, seed: int = 1, sigma: float = 0.002, fps: int = 30):
    """ขยับมือเป็นเส้นตรงในแนว x ระยะ dist_hs (hand-size) ใน dur วินาที พร้อม noise"""
    rng = random.Random(seed)
    m = _motion()
    n = int(dur * fps)
    total = dist_hs * SIZE / ASPECT
    out = (5000.0, 5000.0)
    gmax = 0.0
    for i in range(n + fps):
        t = min(1.0, i / n)
        out = m.update((0.3 + total * t + rng.gauss(0, sigma), 0.5 + rng.gauss(0, sigma)),
                       SIZE, ASPECT, 1 / fps)
        gmax = max(gmax, m.gain)
    return out[0] - 5000.0, out[1] - 5000.0, gmax


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


@pytest.mark.parametrize("fps", FPS)
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_still_hand_noise_is_rejected(seed, fps):
    xs, ys = _noisy_still(0.002, seed, fps)
    assert max(xs) - min(xs) < 4 and max(ys) - min(ys) < 4
    assert abs(xs[-1] - xs[0]) < 4 and abs(ys[-1] - ys[0]) < 4  # ไม่ drift


@pytest.mark.parametrize("fps", FPS)
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_still_hand_larger_noise_is_small(seed, fps):
    xs, ys = _noisy_still(0.003, seed, fps)
    assert max(xs) - min(xs) < 15 and max(ys) - min(ys) < 15
    assert abs(xs[-1] - xs[0]) < 15 and abs(ys[-1] - ys[0]) < 15


@pytest.mark.parametrize("fps", FPS)
def test_slow_deliberate_move_moves_cursor(fps):
    dx, dy, _ = _drive(0.21, 1.0, fps=fps)  # ~2 cm
    assert dx >= 30
    assert abs(dy) < 0.2 * dx


@pytest.mark.parametrize("fps", FPS)
def test_fast_move_travels_far(fps):
    dx, _, gmax = _drive(0.526, 0.3, fps=fps)  # ~5 cm / 0.3 s
    assert dx > 200
    if fps >= 30:
        assert gmax > 1.2


@pytest.mark.parametrize("fps", FPS)
def test_very_fast_move_reaches_high_gain_at_any_fps(fps):
    # regression: เดิมทุกเฟรมที่ 10-15 fps ถูกนับว่า "ช้า" gain ค้างที่ต่ำสุด
    dx, _, gmax = _drive(1.58, 0.3, fps=fps)  # ~15 cm / 0.3 s
    assert gmax >= 2.0
    assert dx >= 2500


def test_late_frame_is_relative_to_frame_rate():
    m = _motion()
    x = 0.1
    m.update((x, 0.5), 0.2, 1.0, 0.1)
    for _ in range(20):
        x += 0.01
        m.update((x, 0.5), 0.2, 1.0, 0.1)
    assert m.speed > 0  # 10 fps ปกติ ไม่ถูกนับเป็น late
    before = m.speed
    x += 0.05
    m.update((x, 0.5), 0.2, 1.0, 0.5)  # ช้ากว่าปกติ 5 เท่า
    assert m.speed == before


def test_sustained_fps_drop_does_not_freeze_gain():
    # 30 fps นิ่งๆ 2 วิ แล้วกล้องตกเหลือ 10 fps ถาวร (ห้องมืดลง) โดยไม่มี reset
    m = _motion()
    for _ in range(60):
        m.update((0.3, 0.5), SIZE, ASPECT, 1 / 30)
    x = 0.3
    for _ in range(10):  # ขยับเร็ว ~ 15 cm / 0.3 s ซ้ำๆ ที่ 10 fps
        x += 0.07  # 15 cm ≈ 1.58 hand-size ≈ 0.21 ของภาพ ใน 3 เฟรม
        m.update((x, 0.5), SIZE, ASPECT, 0.1)
    assert m.gain >= 2.0


def test_motion_direction_follows_hand_both_axes():
    m = _motion()
    x = y = 0.5
    m.update((x, y), SIZE, ASPECT, DT)
    for _ in range(30):
        x -= 0.01
        y += 0.01
        out = m.update((x, y), SIZE, ASPECT, DT)
    assert out[0] < 5000 and out[1] > 5000


def test_leash_start_is_continuous():
    # เคลื่อนช้า ๆ ผ่านขอบ leash: ต้องไม่มีก้าวกระโดดของ cursor
    m = _motion(accel_low_gain=1.0, accel_high_gain=1.0)
    x = 0.3
    m.update((x, 0.5), SIZE, ASPECT, DT)
    prev = 5000.0
    for _ in range(60):
        x += 0.001
        out = m.update((x, 0.5), SIZE, ASPECT, DT)
        assert out[0] - prev < 10.0  # steady state ~8 px/เฟรม
        prev = out[0]


def test_subpixel_motion_accumulates():
    px_per_hand = 10.0
    m = _motion(px_per_hand=px_per_hand, accel_low_gain=1.0, accel_high_gain=1.0)
    step = 0.01 * SIZE / ASPECT  # 0.01 hand-size ต่อเฟรม (ช้ากว่า V_LO แต่เกิน leash เมื่อสะสม)
    x = 0.1
    m.update((x, 0.5), SIZE, ASPECT, DT)
    for _ in range(300):  # ให้ pre-filter และ leash นิ่ง
        x += step
        out = m.update((x, 0.5), SIZE, ASPECT, DT)
    start = out[0]
    n = 60
    for _ in range(n):
        x += step
        out = m.update((x, 0.5), SIZE, ASPECT, DT)
    expected = n * 0.01 * px_per_hand
    assert 0.5 < expected < n  # ต่ำกว่า 1 px/เฟรม จริง
    assert out[0] - start == pytest.approx(expected, rel=0.03)
    assert out[1] == pytest.approx(5000.0)


def test_hand_size_compensation():
    def run(size: float, step: float) -> float:
        m = _motion()
        x = 0.2
        m.update((x, 0.5), size, 1.0, DT)
        out = (0.0, 0.0)
        for _ in range(40):
            x += step
            out = m.update((x, 0.5), size, 1.0, DT)
        return out[0]

    assert run(0.4, 0.02) == pytest.approx(run(0.2, 0.01), rel=0.02)


def test_clamp_at_edge_has_no_windup():
    b = B(0, 0, 100, 100)
    m = Motion(MotionConfig(deadzone=R), b, 1000.0)
    m.reset((50.0, 50.0))
    x = 0.1
    m.update((x, 0.5), 0.2, 1.0, DT)
    for _ in range(60):
        x += 0.02
        out = m.update((x, 0.5), 0.2, 1.0, DT)
    assert out[0] == 99.0
    prev = out[0]
    # หันกลับ: pre-filter มี lag เล็กน้อย แต่ตำแหน่งต้องเริ่มลดลงภายในไม่กี่เฟรม (ไม่ใช่ต้องไล่คืนระยะที่เกิน)
    for _ in range(4):
        x -= 0.05
        out = m.update((x, 0.5), 0.2, 1.0, DT)
    assert out[0] < prev


def test_late_frame_does_not_spike_speed():
    m = _motion()
    x = 0.1
    m.update((x, 0.5), 0.2, 1.0, DT)
    for _ in range(40):
        x += 0.01
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
        for _ in range(90):
            x += step
            m.update((x, 0.5), 0.2, 1.0, DT)
        return m.gain

    assert run(0.004) == pytest.approx(0.4, abs=0.03)
    assert run(0.04) == pytest.approx(2.5)


# ===================== Phase 3: history / set_position / boost / scroll =====================
def _moving(m: Motion, n: int, fps: int = 30, step: float = 0.01, x: float = 0.1, t0: float = 0.0):
    """ขยับมือไปทางขวา n เฟรม พร้อม ts; คืน (x, ts, [(ts, out)])"""
    out_log = []
    ts = t0
    for _ in range(n):
        ts += 1 / fps
        x += step
        out = m.update((x, 0.5), SIZE, ASPECT, 1 / fps, ts=ts)
        out_log.append((ts, out))
    return x, ts, out_log


def test_history_at_returns_past_positions():
    m = _motion()
    m.update((0.1, 0.5), SIZE, ASPECT, DT, ts=0.0)
    _, _, log = _moving(m, 20)
    ts_last, out_last = log[-1]
    assert m.history_at(ts_last) == out_last
    assert m.history_at(ts_last + 5) == out_last          # อนาคต → ล่าสุด
    ts_mid, out_mid = log[10]
    assert m.history_at(ts_mid) == out_mid
    assert m.history_at(ts_mid + 0.5 * DT) == out_mid     # ระหว่างเฟรม → ตัวก่อนหน้า
    assert m.history_at(-10.0) == m._hist[0][1:]          # เก่ากว่า buffer → เก่าสุด
    assert out_mid[0] < out_last[0]


def test_history_is_trimmed_by_time():
    m = _motion()
    m.update((0.1, 0.5), SIZE, ASPECT, DT, ts=0.0)
    _moving(m, 60)  # 2 s
    assert m._hist[-1][0] - m._hist[0][0] <= HISTORY_S + 1e-9
    assert len(m._hist) <= int(HISTORY_S * 30) + 3


def test_history_empty_returns_current_position():
    m = _motion()
    assert m.history_at(1.0) == (5000.0, 5000.0)
    m.update((0.1, 0.5), SIZE, ASPECT, DT)  # ไม่ส่ง ts → ไม่บันทึก
    assert m.history_at(1.0) == (5000.0, 5000.0)


def test_set_position_no_jump_and_keeps_motion_state():
    m = _motion()
    m.update((0.1, 0.5), SIZE, ASPECT, DT, ts=0.0)
    x, ts, log = _moving(m, 30)
    speed, gain = m.speed, m.gain
    m.set_position((4000.0, 4500.0))
    assert (m.speed, m.gain) == (speed, gain)
    assert m.history_at(ts) == (4000.0, 4500.0)
    # เฟรมถัดไปขยับต่อจากตำแหน่งใหม่ด้วยก้าวปกติ ไม่กระโดด
    prev_step = log[-1][1][0] - log[-2][1][0]
    _, _, log2 = _moving(m, 1, x=x, t0=ts)
    out = log2[0][1]
    assert out[0] - 4000.0 == pytest.approx(prev_step, rel=0.3)
    assert out[1] == pytest.approx(4500.0, abs=1e-6)
    # clamp
    m.set_position((-50.0, 1e9))
    assert m._pos == [0.0, 9999.0]


def test_boost_smoothing_has_no_jump_and_ends():
    m = _motion(accel_low_gain=1.0, accel_high_gain=1.0)
    m.update((0.1, 0.5), SIZE, ASPECT, DT, ts=0.0)
    x, ts, _ = _moving(m, 30)
    m.boost_smoothing(0.3)
    steps = []
    prev = m._pos[0]
    for _ in range(30):  # 1 s ที่ความเร็วคงที่ ผ่านช่วง boost จนจบ
        ts += DT
        x += 0.01
        out = m.update((x, 0.5), SIZE, ASPECT, DT, ts=ts)
        steps.append(out[0] - prev)
        prev = out[0]
    assert m._boost_left == 0.0
    assert all(s >= 0 for s in steps)
    # ไม่มีก้าวกระโดดเกิน ~2 เท่าของก้าวคงที่ปลายช่วง
    steady = steps[-1]
    assert steady > 0 and max(steps) < 2.5 * steady
    # ขณะ boost leash ใหญ่ขึ้น → ก้าวต้นช่วงไม่มากกว่าปกติ
    assert steps[0] <= steady * 1.2


def test_boost_smoothing_suppresses_small_motion_more():
    def wander(boost: bool) -> float:
        rng = random.Random(3)
        m = _motion()
        m.update((0.5, 0.5), SIZE, ASPECT, DT, ts=0.0)
        if boost:
            m.boost_smoothing(10.0)
        xs = []
        ts = 0.0
        for _ in range(150):
            ts += DT
            xs.append(m.update((0.5 + rng.gauss(0, 0.004), 0.5 + rng.gauss(0, 0.004)),
                               SIZE, ASPECT, DT, ts=ts)[0])
        return max(xs) - min(xs)

    assert wander(True) <= wander(False)


def test_scroll_first_call_zero_and_direction():
    m = _motion()
    assert m.scroll_delta(0.5, SIZE, DT) == 0
    y, total = 0.5, 0
    for _ in range(30):  # มือขึ้น (y ลด) = บวก
        y -= 0.01
        total += m.scroll_delta(y, SIZE, DT)
    assert total > 0
    m.reset_scroll()
    assert m.scroll_delta(y, SIZE, DT) == 0
    total = 0
    for _ in range(30):  # มือลง = ลบ
        y += 0.01
        total += m.scroll_delta(y, SIZE, DT)
    assert total < 0


def test_scroll_remainder_is_accumulated_not_lost():
    m = _motion()
    speed = getattr(m.cfg, "scroll_speed", 600.0)
    m.scroll_delta(0.9, SIZE, DT)
    y, total = 0.9, 0
    n = 120
    step = 0.0007  # ช้า: ต่อเฟรมได้ไม่ถึง 1 wheel unit หลังหัก deadzone
    for _ in range(n):
        y -= step
        total += m.scroll_delta(y, SIZE, DT)
    # ผลรวมเข้าใกล้ (ระยะทั้งหมด − deadzone − lag ของ filter) × speed / size ภายใน 1-2 unit
    assert isinstance(total, int)
    moved = n * step
    ideal = (moved / SIZE) * speed
    assert 0 < total <= ideal
    assert total >= ideal - speed * (0.03 + 0.2 * step * 30 / SIZE) - 2  # หัก leash + lag


def test_scroll_ignores_jitter():
    rng = random.Random(7)
    m = _motion()
    m.scroll_delta(0.5, SIZE, DT)
    total = 0
    for _ in range(300):
        total += abs(m.scroll_delta(0.5 + rng.gauss(0, 0.002), SIZE, DT))
    assert total == 0


@pytest.mark.parametrize("fps", FPS)
def test_scroll_works_at_low_fps(fps):
    m = _motion()
    m.scroll_delta(0.8, SIZE, 1 / fps)
    y, total = 0.8, 0
    for _ in range(fps):  # ขยับ 0.3 ภาพใน 1 s
        y -= 0.3 / fps
        total += m.scroll_delta(y, SIZE, 1 / fps)
    assert total > 200
