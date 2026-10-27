"""ทดสอบ card_layout / fit_rect (pure) — ไม่สร้าง QApplication"""
from __future__ import annotations

import pytest

from airmouse.ui.switcher import card_layout, fit_rect

ARGS = dict(strip_w=1000.0, strip_h=300.0, card_w=260.0, card_h=170.0, gap=40.0)


def lay(count, pos, **kw):
    a = dict(ARGS)
    a.update(kw)
    return card_layout(count, pos, **a)


def test_empty_and_single():
    assert lay(0, 0.0) == []
    (x, y, w, h, op), = lay(1, 0.0)
    assert x + w / 2 == pytest.approx(500.0)
    assert y + h / 2 == pytest.approx(150.0)
    assert w == pytest.approx(260 * 1.25) and op == 1.0


def test_centered_is_largest():
    cards = lay(5, 2.0)
    sizes = [c[2] for c in cards]
    assert sizes[2] == max(sizes)
    assert cards[2][0] + cards[2][2] / 2 == pytest.approx(500.0)
    assert sizes[0] == pytest.approx(260.0) and sizes[4] == pytest.approx(260.0)


def test_spacing_and_fractional():
    cards = lay(5, 2.0)
    c1 = cards[1][0] + cards[1][2] / 2
    c3 = cards[3][0] + cards[3][2] / 2
    assert c3 - c1 == pytest.approx(2 * 300.0)
    half = lay(5, 2.5)
    # ครึ่งทางระหว่าง 2 กับ 3: การ์ด 2 และ 3 ขนาดเท่ากัน อยู่ symmetric รอบกลาง
    assert half[2][2] == pytest.approx(half[3][2])
    assert half[2][2] < cards[2][2]
    mid = (half[2][0] + half[2][2] / 2 + half[3][0] + half[3][2] / 2) / 2
    assert mid == pytest.approx(500.0)


def test_far_cards_fade():
    cards = lay(10, 0.0)
    assert cards[0][4] == 1.0 and cards[2][4] == 1.0
    assert 0.0 < cards[3][4] < 1.0
    assert cards[4][4] == 0.0 and cards[9][4] == 0.0


def test_x_overlap():
    from airmouse.ui.switcher import x_overlap
    a = (0.0, 0.0, 10.0, 5.0, 1.0)
    assert x_overlap(a, (5.0, 0.0, 10.0, 5.0, 1.0))
    assert not x_overlap(a, (10.0, 0.0, 10.0, 5.0, 1.0))  # ขอบชน = ไม่ซ้อน
    assert not x_overlap(a, (20.0, 0.0, 5.0, 5.0, 1.0))


def test_fit_rect_aspect():
    x, y, w, h = fit_rect(1920, 1080, 0, 0, 200, 200)
    assert w == pytest.approx(200) and h == pytest.approx(200 * 1080 / 1920)
    assert y == pytest.approx((200 - h) / 2)
    assert fit_rect(0, 0, 1, 2, 3, 4) == (1, 2, 3, 4)
