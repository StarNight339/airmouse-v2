import math
from dataclasses import dataclass

import pytest

from airmouse.config import SnapConfig
from airmouse.snap import Snapper


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


def _snapper(**kw) -> Snapper:
    return Snapper(SnapConfig(**kw))


def test_no_targets_unchanged():
    s = _snapper()
    assert s.adjust((10.0, 10.0), (12.5, 11.5), 0.3, 0.033, []) == ((12.5, 11.5), None)
    assert s.hovered is None


def test_target_far_away_unchanged():
    s = _snapper()
    assert s.adjust((10.0, 10.0), (12.0, 10.0), 0.3, 0.033, [BTN]) == ((12.0, 10.0), None)


def test_disabled_passthrough():
    s = _snapper(enabled=False)
    out, hov = s.adjust((120.0, 110.0), (121.0, 111.0), 0.1, 0.033, [BTN])
    assert out == (121.0, 111.0) and hov is None


@pytest.mark.parametrize("fps", FPS)
def test_slow_approach_converges_to_center(fps):
    s = _snapper()
    dt = 1 / fps
    out = (90.0, 125.0)  # ในโซน capture ของปุ่ม
    n = int(round(5 * 0.12 / dt)) + 1
    for _ in range(n):
        raw = (out[0] + 0.3, out[1])  # มือแทบนิ่ง ขยับนิดเดียว
        out, hov = s.adjust(out, raw, 0.3, dt, [BTN])
        assert hov is BTN
    assert math.dist(out, BTN.center) < 1.5


@pytest.mark.parametrize("fps", FPS)
def test_no_overshoot_and_bounded_step_on_enter(fps):
    s = _snapper()
    dt = 1 / fps
    out = (60.0, 115.0)
    xs = []
    max_step = 0.0
    for _ in range(6 * fps):
        raw = (out[0] + 1.0, out[1])  # เดินเข้าหาปุ่มช้า ๆ
        new, hov = s.adjust(out, raw, 0.5, dt, [BTN])
        max_step = max(max_step, math.dist(new, out))
        out = new
        xs.append(out[0])
    assert max(xs) <= BTN.center[0] + 3.0  # ไม่เลยกลางปุ่ม
    assert max_step < 0.6 * 54 + 2  # alpha สูงสุด (10 fps) x ระยะ capture-zone ถึงกลาง


def test_friction_reduces_motion_while_hovered():
    s = _snapper()
    # speed อยู่ระหว่าง slow_speed กับ escape_speed: มีแต่หนืด ไม่ดูด
    out, hov = s.adjust((130.0, 115.0), (140.0, 115.0), 1.2, 0.033, [BTN])
    assert hov is BTN
    assert out == pytest.approx((130.0 + 10.0 * 0.35, 115.0))


def test_fast_motion_escapes_and_clears_hover():
    s = _snapper()
    s.adjust((130.0, 115.0), (131.0, 115.0), 0.3, 0.033, [BTN])
    assert s.hovered is BTN
    out, hov = s.adjust((131.0, 115.0), (150.0, 115.0), 2.5, 0.033, [BTN])
    assert out == (150.0, 115.0) and hov is None and s.hovered is None


def test_fast_pass_through_never_hovers():
    s = _snapper()
    out = (60.0, 115.0)
    for _ in range(20):
        raw = (out[0] + 15.0, out[1])
        new, hov = s.adjust(out, raw, 3.0, 0.033, [BTN])
        assert hov is None and new == raw
        out = new


def test_leaving_hysteresis_zone_drops_hover():
    s = _snapper()
    s.adjust((130.0, 115.0), (131.0, 115.0), 0.3, 0.033, [BTN])
    # ใน capture*1.5 = 36 px ของกรอบ: ยังคงอยู่
    assert s.adjust((168.0, 115.0), (170.0, 115.0), 1.0, 0.033, [BTN])[1] is BTN
    out, hov = s.adjust((170.0, 115.0), (180.0, 115.0), 1.0, 0.033, [BTN])
    assert hov is None and out == (180.0, 115.0)


def test_hysteresis_between_adjacent_buttons_no_ping_pong():
    a = T(100, 100, 160, 130)
    b = T(166, 100, 226, 130)  # ช่องว่าง 6 px
    s = _snapper()
    s.adjust((140.0, 115.0), (145.0, 115.0), 0.3, 0.033, [a, b])
    assert s.hovered is a
    seen = set()
    out = (162.0, 115.0)
    for i in range(40):  # แกว่งอยู่ในช่องว่างและขอบ
        x = 161.0 + (i % 5)  # 161..165
        out, hov = s.adjust(out, (x, 115.0), 0.3, 0.033, [a, b])
        seen.add(id(hov))
    assert seen == {id(a)}
    # ข้ามเข้ากรอบจริงของ b → สลับครั้งเดียว
    _, hov = s.adjust(out, (170.0, 115.0), 0.3, 0.033, [a, b])
    assert hov is b
    # กลับมาในช่องว่าง ยังคง b (ไม่เด้งกลับ)
    for x in (165.0, 163.0, 164.0, 165.0):
        _, hov = s.adjust(out, (x, 115.0), 0.3, 0.033, [a, b])
        assert hov is b


def test_nearest_center_chosen_on_entry():
    a = T(100, 100, 160, 130)
    b = T(170, 100, 230, 130)
    s = _snapper()
    _, hov = s.adjust((168.0, 115.0), (168.0, 115.0), 0.3, 0.033, [a, b])
    assert hov is b  # ใกล้กลาง b มากกว่า


def test_large_target_is_not_pulled_to_center():
    big = T(0, 0, 400, 120)
    s = _snapper()
    out = (50.0, 20.0)
    for _ in range(60):
        out, hov = s.adjust(out, out, 0.2, 0.033, [big])
        assert hov is big
    assert out == pytest.approx((50.0, 20.0))  # ปุ่มใหญ่: ไม่ดูดเลยทั้งสองแกน


def test_wide_thin_target_pulled_only_across_short_axis():
    bar = T(0, 0, 400, 30)  # center y = 15
    s = _snapper()
    out = (50.0, 2.0)
    for _ in range(60):
        out, hov = s.adjust(out, out, 0.2, 0.033, [bar])
    assert out[0] == pytest.approx(50.0)  # ไม่ไถไปตามแกนยาว
    assert out[1] == pytest.approx(15.0, abs=0.2)


def test_friction_still_applies_on_large_target():
    big = T(0, 0, 400, 120)
    s = _snapper()
    out, hov = s.adjust((50.0, 50.0), (60.0, 50.0), 1.5, 0.033, [big])
    assert hov is big and out == pytest.approx((53.5, 50.0))


def test_dt_extremes_are_safe():
    s = _snapper()
    for dt in (0.0, -1.0, 5.0):
        out, hov = s.adjust((120.0, 110.0), (121.0, 110.0), 0.1, dt, [BTN])
        assert all(math.isfinite(v) for v in out)
        assert BTN.center[0] <= out[0] <= 121.0  # ระหว่างกลางปุ่มกับเป้า ไม่เลย


def test_reset_clears_hover():
    s = _snapper()
    s.adjust((130.0, 115.0), (131.0, 115.0), 0.3, 0.033, [BTN])
    assert s.hovered is BTN
    s.reset()
    assert s.hovered is None


def test_hovered_target_removed_from_list_is_dropped():
    s = _snapper()
    s.adjust((130.0, 115.0), (131.0, 115.0), 0.3, 0.033, [BTN])
    out, hov = s.adjust((131.0, 115.0), (132.0, 115.0), 0.3, 0.033, [])
    assert hov is None and out == (132.0, 115.0)
