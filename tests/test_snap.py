import math
from dataclasses import dataclass

import pytest

from airmouse.config import SnapConfig
from airmouse.snap import HYSTERESIS, Snapper


@dataclass(frozen=True)
class T:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)


BTN = T(100, 100, 140, 130)  # 40x30 (เล็กพอให้ดูดทั้งสองแกน) center (120, 115)
FPS = [10, 30]
REACH = SnapConfig().capture_px * HYSTERESIS


def _snapper(**kw) -> Snapper:
    return Snapper(SnapConfig(**kw))


def _hold(s: Snapper, v, speed, dt, targets, n):
    """ป้อน virtual เดิมซ้ำ (มือนิ่ง) n เฟรม คืนผลเฟรมสุดท้าย"""
    res = None
    for _ in range(n):
        res = s.adjust(v, v, speed, dt, targets)
        v = res[0]
    return res


def test_no_targets_unchanged():
    s = _snapper()
    assert s.adjust((10.0, 10.0), (12.5, 11.5), 0.3, 0.033, []) == \
        ((12.5, 11.5), (12.5, 11.5), None)
    assert s.hovered is None


def test_target_far_away_unchanged():
    s = _snapper()
    assert s.adjust((10.0, 10.0), (12.0, 10.0), 0.3, 0.033, [BTN]) == \
        ((12.0, 10.0), (12.0, 10.0), None)


def test_disabled_passthrough():
    s = _snapper(enabled=False)
    v, out, hov = s.adjust((120.0, 110.0), (121.0, 111.0), 0.1, 0.033, [BTN])
    assert v == out == (121.0, 111.0) and hov is None


@pytest.mark.parametrize("fps", FPS)
def test_still_hand_near_center_shown_at_center(fps):
    s = _snapper()
    v, out, hov = _hold(s, (112.0, 119.0), 0.1, 1 / fps, [BTN], 2 * fps)
    assert hov is BTN and v == (112.0, 119.0)  # virtual ไม่ถูกดูด
    assert math.dist(out, BTN.center) < 2.0       # แต่ที่แสดงอยู่กลางปุ่ม


def test_pull_fades_to_zero_at_hover_edge():
    s = _snapper()
    _hold(s, (135.0, 115.0), 0.1, 0.033, [BTN], 10)  # เกาะปุ่มก่อน
    edge_x = BTN.right + REACH - 0.5  # ยังอยู่ในโซน hover (hysteresis)
    v, out, hov = _hold(s, (edge_x, 115.0), 0.1, 0.033, [BTN], 60)
    assert hov is BTN and out[0] == pytest.approx(edge_x, abs=0.5)


@pytest.mark.parametrize("fps", FPS)
def test_slow_sweep_shown_monotonic_and_ramps_in(fps):
    s = _snapper()
    dt = 1 / fps
    v = (60.0, 115.0)
    prev_out, max_step = v, 0.0
    for _ in range(8 * fps):
        target = (v[0] + 40.0 * dt, v[1])  # มือขวาช้า ๆ
        v, out, _ = s.adjust(v, target, 0.3, dt, [BTN])
        assert out[0] >= prev_out[0] - 0.5  # ไม่ถอยสวนทิศมือ
        max_step = max(max_step, math.dist(out, prev_out))
        prev_out = out
    assert v[0] > BTN.right + REACH and s.hovered is None  # virtual เดินผ่านปุ่มไปได้
    assert max_step < 20.0  # offset ค่อย ๆ เพิ่ม ไม่กระโดดตอนเข้า/ออกปุ่ม


def test_virtual_never_pulled_only_friction():
    s = _snapper()
    v, out, hov = s.adjust((130.0, 115.0), (140.0, 115.0), 0.3, 0.033, [BTN])
    assert hov is BTN and v == pytest.approx((130.0 + 10.0 * 0.35, 115.0))
    assert out[0] < v[0]  # ดึงเข้ากลาง (120) เฉพาะที่แสดง


def test_no_pull_between_slow_and_escape_speed():
    s = _snapper()
    v, out, hov = s.adjust((130.0, 115.0), (140.0, 115.0), 1.2, 0.033, [BTN])
    assert hov is BTN and v == out == pytest.approx((130.0 + 10.0 * 0.35, 115.0))


def test_offset_releases_smoothly_when_speeding_up():
    s = _snapper()
    v, out, _ = _hold(s, (110.0, 115.0), 0.1, 0.033, [BTN], 30)
    pulled = out[0] - v[0]
    assert pulled > 5.0
    v2, out2, hov = s.adjust(v, v, 1.2, 0.033, [BTN])  # เร็วขึ้น: เลิกดูดแบบค่อย ๆ
    assert hov is BTN and 0.0 < out2[0] - v2[0] < pulled


def test_fast_motion_escapes_and_clears_hover():
    s = _snapper()
    s.adjust((130.0, 115.0), (131.0, 115.0), 0.3, 0.033, [BTN])
    assert s.hovered is BTN
    v, out, hov = s.adjust((131.0, 115.0), (150.0, 115.0), 2.5, 0.033, [BTN])
    assert v == (150.0, 115.0) and hov is None and s.hovered is None


def test_fast_pass_through_never_hovers():
    s = _snapper()
    v = (60.0, 115.0)
    for _ in range(20):
        raw = (v[0] + 15.0, v[1])
        v, out, hov = s.adjust(v, raw, 3.0, 0.033, [BTN])
        assert hov is None and v == out == raw


def test_leaving_hysteresis_zone_drops_hover():
    s = _snapper()
    s.adjust((130.0, 115.0), (131.0, 115.0), 0.3, 0.033, [BTN])
    # ใน capture*1.5 = 36 px ของกรอบ: ยังคงอยู่
    assert s.adjust((168.0, 115.0), (170.0, 115.0), 1.0, 0.033, [BTN])[2] is BTN
    v, out, hov = s.adjust((170.0, 115.0), (180.0, 115.0), 1.0, 0.033, [BTN])
    assert hov is None and v == (180.0, 115.0)


def test_hysteresis_between_adjacent_buttons_no_ping_pong():
    a = T(100, 100, 160, 130)
    b = T(166, 100, 226, 130)  # ช่องว่าง 6 px
    s = _snapper()
    s.adjust((140.0, 115.0), (145.0, 115.0), 0.3, 0.033, [a, b])
    assert s.hovered is a
    seen = set()
    v = (162.0, 115.0)
    for i in range(40):  # แกว่งอยู่ในช่องว่างและขอบ
        x = 161.0 + (i % 5)  # 161..165
        v, _, hov = s.adjust(v, (x, 115.0), 0.3, 0.033, [a, b])
        seen.add(id(hov))
    assert seen == {id(a)}
    # ข้ามเข้ากรอบจริงของ b → สลับครั้งเดียว
    _, _, hov = s.adjust(v, (170.0, 115.0), 0.3, 0.033, [a, b])
    assert hov is b
    # กลับมาในช่องว่าง ยังคง b (ไม่เด้งกลับ)
    for x in (165.0, 163.0, 164.0, 165.0):
        _, _, hov = s.adjust(v, (x, 115.0), 0.3, 0.033, [a, b])
        assert hov is b


def test_nearest_center_chosen_on_entry():
    a = T(100, 100, 160, 130)
    b = T(170, 100, 230, 130)
    s = _snapper()
    _, _, hov = s.adjust((168.0, 115.0), (168.0, 115.0), 0.3, 0.033, [a, b])
    assert hov is b  # ใกล้กลาง b มากกว่า


def test_large_target_is_not_pulled_to_center():
    big = T(0, 0, 400, 120)
    s = _snapper()
    v, out, hov = _hold(s, (50.0, 20.0), 0.2, 0.033, [big], 60)
    assert hov is big
    assert out == pytest.approx((50.0, 20.0))  # ปุ่มใหญ่: ไม่ดูดเลยทั้งสองแกน


def test_wide_thin_target_pulled_only_across_short_axis():
    bar = T(0, 0, 400, 30)  # center y = 15
    s = _snapper()
    v, out, _ = _hold(s, (50.0, 2.0), 0.2, 0.033, [bar], 60)
    assert out[0] == pytest.approx(50.0)  # ไม่ไถไปตามแกนยาว
    assert abs(out[1] - 15.0) < abs(2.0 - 15.0) / 2  # แกนสั้นถูกดึงเข้ากลาง


def test_friction_still_applies_on_large_target():
    big = T(0, 0, 400, 120)
    s = _snapper()
    v, out, hov = s.adjust((50.0, 50.0), (60.0, 50.0), 1.5, 0.033, [big])
    assert hov is big and v == out == pytest.approx((53.5, 50.0))


def test_dt_extremes_are_safe():
    for dt in (0.0, -1.0, 5.0):
        s = _snapper()
        v, out, hov = s.adjust((120.0, 110.0), (121.0, 110.0), 0.1, dt, [BTN])
        assert all(math.isfinite(c) for c in v + out)
        assert BTN.center[0] <= out[0] <= v[0]  # ดึงเข้าหากลางปุ่ม ไม่เลย


def test_reset_clears_hover_and_offset():
    s = _snapper()
    _hold(s, (110.0, 115.0), 0.1, 0.033, [BTN], 10)
    assert s.hovered is BTN and s.offset != (0.0, 0.0)
    s.reset()
    assert s.hovered is None and s.offset == (0.0, 0.0)


def test_hovered_target_removed_from_list_is_dropped():
    s = _snapper()
    s.adjust((130.0, 115.0), (131.0, 115.0), 0.3, 0.033, [BTN])
    v, out, hov = s.adjust((131.0, 115.0), (132.0, 115.0), 0.3, 0.033, [])
    assert hov is None and v == (132.0, 115.0)
