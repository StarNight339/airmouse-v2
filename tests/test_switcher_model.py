import math

import pytest

from airmouse.switcher_model import Carousel

DT = 1 / 30


def _run(c: Carousel, dx: float, seconds: float, dt: float = DT) -> None:
    for _ in range(int(round(seconds / dt))):
        c.update(dx, dt)


def test_starts_on_second_card_or_first_when_alone():
    assert Carousel(5).selected == 1 and Carousel(5).position == 1.0
    assert Carousel(1).selected == 0
    assert Carousel(2).selected == 1
    assert Carousel(5, start=3).selected == 3
    assert Carousel(0).selected == 0


def test_dx_moves_selection():
    c = Carousel(5)
    _run(c, 1.0, 1.0)
    assert c.selected == 2
    _run(c, -1.0, 1.0)
    assert c.selected == 0
    _run(c, 0.4, 1.0)
    assert c.selected == 1


def test_snap_after_hold_rounds_to_nearest_card():
    c = Carousel(5)
    _run(c, 0.7, 0.05)           # ยังขยับอยู่ (ยังไม่ snap): position ไม่ถึง 2
    assert c.position < 1.9
    _run(c, 0.7, 1.0)            # นิ่ง > 0.15 s → snap เข้า 2
    assert c.position == pytest.approx(2.0, abs=0.01)
    c2 = Carousel(5)
    _run(c2, 0.3, 1.0)           # 1.3 → snap เข้า 1
    assert c2.position == pytest.approx(1.0, abs=0.01)


def test_no_snap_while_still_moving():
    c = Carousel(8)
    dx = 0.0
    for _ in range(30):          # เลื่อนช้า ๆ ต่อเนื่อง 0.1 การ์ดต่อเฟรม: ไม่ snap
        dx += 0.1
        c.update(dx, DT)
    assert c.position > 3.2


def test_clamp_and_rubber_band():
    c = Carousel(4)               # 0..3
    _run(c, 10.0, 2.0)
    assert 3.0 < c.position <= 3.5 + 1e-9    # ยางยืดเกินปลายไม่เกิน 0.5
    assert c.selected == 3
    _run(c, -20.0, 2.0)
    assert -0.5 - 1e-9 <= c.position < 0.0
    assert c.selected == 0
    # ยางยืด 0.3x: เลยไป 1 การ์ด = เลยจริง 0.3 (ก่อน snap ผ่านไป; ใช้ขยับต่อเนื่องไม่ให้ snap)
    c2 = Carousel(4, start=3)
    dx = 0.0
    for _ in range(12):
        dx += 0.05
        c2.update(dx, 0.2)       # dt ใหญ่ให้ตามทัน
    assert c2.position == pytest.approx(3.0 + 0.3 * 0.6, abs=0.02)


def test_snap_returns_from_rubber_band_to_edge():
    c = Carousel(3)
    _run(c, 5.0, 0.3)
    _run(c, 5.0, 1.0)             # นิ่งนาน → snap กลับเข้าใบสุดท้ายพอดี
    assert c.position == pytest.approx(2.0, abs=0.01)


def test_never_overshoots_target():
    c = Carousel(6)
    last = c.position
    for _ in range(60):
        c.update(2.0, DT)
        assert last - 1e-9 <= c.position <= 3.0 + 1e-9   # ขึ้นอย่างเดียว ไม่เกินเป้า
        last = c.position
    # dt ใหญ่ก็ไม่เกิน
    c = Carousel(6)
    c.update(3.0, 5.0)
    assert 1.0 < c.position <= 4.0


@pytest.mark.parametrize("dt", [0.0, -1.0, 1e9, float("nan"), float("inf"), 1e-12])
def test_dt_extremes_are_stable(dt):
    c = Carousel(5)
    for _ in range(50):
        c.update(1.0, dt)
        assert math.isfinite(c.position) and 0.99 <= c.position <= 2.0 + 1e-9


def test_non_finite_dx_ignored():
    c = Carousel(5)
    _run(c, 1.0, 1.0)
    c.update(float("nan"), DT)
    c.update(float("inf"), DT)
    assert math.isfinite(c.position) and c.selected == 2


def test_remove_before_selected_keeps_same_card():
    c = Carousel(5, start=3)
    _run(c, 0.0, 0.5)
    c.remove(0)                   # การ์ดที่ 3 เดิมกลายเป็น 2
    assert c.count == 4 and c.selected == 2
    assert c.position == pytest.approx(2.0, abs=0.01)
    c.update(0.0, DT)             # ไม่กระโดดในเฟรมถัดไป
    assert c.position == pytest.approx(2.0, abs=0.05) and c.selected == 2


def test_remove_selected_next_slides_in():
    c = Carousel(5, start=2)
    _run(c, 0.0, 0.5)
    c.remove(2)
    # ไม่ teleport: การ์ดถัดไปไหลเข้ามาจากขวา (position ถอยหนึ่งการ์ดแล้วค่อยตามเป้า 2)
    assert c.count == 4 and c.position == pytest.approx(1.0, abs=0.01)
    assert c.target_index == 2
    c.update(0.0, DT)
    assert 1.0 < c.position < 2.0
    _run(c, 0.0, 0.5)
    assert c.selected == 2 and c.position == pytest.approx(2.0, abs=0.01)


def test_remove_selected_last_goes_to_previous():
    c = Carousel(4, start=3)
    _run(c, 0.0, 0.5)
    c.remove(3)
    # position คงเดิม (3.0) ใบก่อนหน้าไหลเข้ามาเอง
    assert c.count == 3 and c.position == pytest.approx(3.0, abs=0.01)
    assert c.selected == 2 and c.target_index == 2
    c.update(0.0, DT)
    assert 2.0 < c.position < 3.0
    _run(c, 0.0, 0.5)
    assert c.position == pytest.approx(2.0, abs=0.01)


def test_target_index_follows_dx_immediately_without_rubber_band():
    c = Carousel(5)               # base 1
    assert c.target_index == 1
    c.update(1.0, DT)             # position ยังแทบไม่ขยับ แต่ target_index ชี้การ์ดที่มือชี้แล้ว
    assert c.selected == 1 and c.target_index == 2
    c.update(10.0, DT)
    assert c.target_index == 4    # clamp ไม่ใช้ rubber band
    c.update(-10.0, DT)
    assert c.target_index == 0
    assert Carousel(0).target_index == 0


def test_remove_after_selected_changes_nothing_visible():
    c = Carousel(5, start=1)
    _run(c, 0.0, 0.5)
    c.remove(4)
    assert c.count == 4 and c.selected == 1
    assert c.position == pytest.approx(1.0, abs=0.01)


def test_remove_keeps_hand_offset_continuous():
    c = Carousel(5)               # เลือก 1
    _run(c, 1.0, 1.0)             # มือเลื่อน +1 → เลือก 2
    assert c.selected == 2
    c.remove(2)                   # การ์ด 3 เลื่อนเข้าช่อง 2
    c.update(1.0, DT)             # dx เดิม (มือยังไม่ขยับ): เป้ายังเป็นช่อง 2 ไม่กระโดดไปที่อื่น
    assert c.target_index == 2
    _run(c, 1.0, 1.0)
    assert c.selected == 2 and c.position == pytest.approx(2.0, abs=0.05)
    _run(c, 2.0, 1.0)             # มือเลื่อนต่ออีก 1 → การ์ดถัดไป
    assert c.selected == 3


def test_remove_last_remaining_and_invalid_index():
    c = Carousel(1)
    c.remove(5)
    assert c.count == 1
    c.remove(0)
    assert c.count == 0 and c.position == 0.0 and c.selected == 0
    c.update(1.0, DT)             # ว่าง: ไม่พัง
    assert c.position == 0.0
    c.remove(0)
    assert c.count == 0


def test_remove_down_to_empty_in_sequence():
    c = Carousel(3)
    for _ in range(3):
        c.remove(c.selected)
        c.update(0.0, DT)
    assert c.count == 0 and c.selected == 0
