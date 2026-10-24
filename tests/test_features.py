import math

import pytest

from airmouse import features


def _hand(curled: set[str] = frozenset(), pinch_gap: float | None = None):
    """มือจำลองแบบ 3D: นิ้วเหยียดเป็นเส้นตรง นิ้วพับงอ 90° ทุกข้อ"""
    pts = [(0.0, 0.0, 0.0)] * 21
    pts[0] = (0.0, 0.0, 0.0)
    for k, (name, joints) in enumerate(features.FINGERS.items()):
        base = (k * 0.02 - 0.04, -0.08, 0.0)
        seg = 0.03
        if name in curled:
            # โคน → ขึ้น → ไปข้างหน้า → ลง (ปลายนิ้ววนกลับมาใกล้โคน)
            steps = [(0, -seg, 0), (0, 0, -seg), (0, seg, 0)]
        else:
            steps = [(0, -seg, 0)] * 3
        p = base
        pts[joints[0]] = p
        for j, s in zip(joints[1:], steps):
            p = (p[0] + s[0], p[1] + s[1], p[2] + s[2])
            pts[j] = p
    if pinch_gap is not None:
        ix, iy, iz = pts[features.INDEX_TIP]
        pts[features.THUMB_TIP] = (ix + pinch_gap, iy, iz)
    return pts


def test_straight_finger_curl_is_one():
    w = _hand()
    for joints in features.FINGERS.values():
        assert features.curl_ratio(w, joints) == pytest.approx(1.0)


def test_curled_finger_curl_is_low():
    w = _hand(curled={"index"})
    assert features.curl_ratio(w, features.FINGERS["index"]) == pytest.approx(1 / 3)
    assert features.curl_ratio(w, features.FINGERS["middle"]) == pytest.approx(1.0)


def test_curl_is_scale_invariant():
    w = _hand(curled={"ring"})
    big = [(x * 3, y * 3, z * 3) for x, y, z in w]
    joints = features.FINGERS["ring"]
    assert features.curl_ratio(big, joints) == pytest.approx(features.curl_ratio(w, joints))


def test_degenerate_finger_does_not_divide_by_zero():
    w = [(0.0, 0.0, 0.0)] * 21
    assert features.curl_ratio(w, features.FINGERS["index"]) == 0.0


def test_compute_pinch_anchor_and_hand_size():
    world = _hand(pinch_gap=0.02)
    image = [(0.5, 0.5, 0.0)] * 21
    image[0] = (0.5, 0.8, 0.0)
    image[9] = (0.5, 0.5, 0.0)
    for i, x in zip(features.ANCHOR_IDS, (0.4, 0.5, 0.6, 0.7)):
        image[i] = (x, 0.5, 0.0)

    f = features.compute(image, world, aspect=4 / 3)

    assert f.pinch == pytest.approx(0.02)
    assert f.anchor == pytest.approx((0.55, 0.5))
    assert f.hand_size == pytest.approx(0.3)
    assert set(f.curl) == set(features.FINGERS)


def test_spread_together_vs_apart():
    together = _hand()
    apart = list(together)
    for idx, dx in ((8, -0.05), (12, -0.02), (16, 0.04), (20, 0.1)):
        x, y, z = apart[idx]
        apart[idx] = (x + dx, y, z)
    s_t = features.spread_ratio(together)
    s_a = features.spread_ratio(apart)
    assert s_t == pytest.approx(0.02 / 0.06)
    assert s_a > 1.0 > s_t


def test_spread_degenerate_is_zero():
    assert features.spread_ratio([(0.0, 0.0, 0.0)] * 21) == 0.0


def _palm_image():
    # มือขวา ฝ่ามือเข้ากล้อง ภาพ mirror: ข้อมือล่าง, นิ้วชี้โคนซ้ายบน, นิ้วก้อยโคนขวาบน
    image = [(0.5, 0.5, 0.0)] * 21
    image[0] = (0.5, 0.8, 0.0)
    image[5] = (0.4, 0.5, 0.0)
    image[17] = (0.6, 0.5, 0.0)
    return image


def test_palm_facing_sign_conventions():
    world = _hand()
    img = _palm_image()
    for hand, mirrored, sign in (("right", True, 1), ("left", True, -1),
                                 ("right", False, -1), ("left", False, 1)):
        f = features.compute(img, world, 1.0, handedness=hand, mirrored=mirrored)
        assert f.palm_facing * sign > 0.5, (hand, mirrored)
    assert -1.0 <= f.palm_facing <= 1.0


def test_palm_facing_flips_when_back_of_hand():
    img = _palm_image()
    img[5], img[17] = img[17], img[5]  # สลับซ้าย/ขวา = หันหลังมือเข้ากล้อง
    f = features.compute(img, _hand(), 1.0)
    assert f.palm_facing < -0.5


def test_hand_size_corrects_for_aspect():
    # ระยะแนวนอน 0.3 ของความกว้างภาพ 4:3 = 0.4 ของความสูงภาพ
    image = [(0.0, 0.0, 0.0)] * 21
    image[0] = (0.2, 0.5, 0.0)
    image[9] = (0.5, 0.5, 0.0)
    f = features.compute(image, _hand(), aspect=4 / 3)
    assert f.hand_size == pytest.approx(0.4)
    assert math.isfinite(f.pinch)
