"""ตัวกรองสัญญาณ (pure logic): One Euro filter แบบ scalar รับ dt จากผู้เรียก"""
from __future__ import annotations

import math


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """One Euro filter: cutoff ต่ำเมื่อนิ่ง (ลด jitter) และสูงขึ้นเมื่อเคลื่อนเร็ว (ลด lag)"""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float = 1.0) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x: float | None = None
        self._dx = 0.0

    def reset(self) -> None:
        self._x = None
        self._dx = 0.0

    @property
    def value(self) -> float | None:
        return self._x

    @property
    def velocity(self) -> float:
        return self._dx

    def __call__(self, x: float, dt: float) -> float:
        if self._x is None:
            self._x = x
            self._dx = 0.0
            return x
        if dt <= 0:  # dt เพี้ยน: คืนค่าเดิมโดยไม่อัปเดต state
            return self._x
        dx = (x - self._x) / dt
        a_d = _alpha(self.d_cutoff, dt)
        self._dx += a_d * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = _alpha(cutoff, dt)
        self._x += a * (x - self._x)
        return self._x
