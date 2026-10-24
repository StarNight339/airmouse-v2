"""แปลงการขยับมือ (anchor ในภาพ) เป็นตำแหน่ง cursor แบบ relative (pure logic)"""
from __future__ import annotations

import logging
from typing import Protocol

from .config import MotionConfig
from .filters import OneEuroFilter

log = logging.getLogger(__name__)

V_LO = 0.5            # ความเร็ว (hand-size/s) ที่ต่ำกว่านี้ใช้ gain ต่ำสุด
V_HI = 3.0            # ความเร็วที่สูงกว่านี้ใช้ gain สูงสุด
DT_MIN, DT_MAX = 0.010, 0.050
DT_REF = 1 / 30       # dt อ้างอิงของ deadzone
SIZE_ALPHA = 0.2      # EMA ของ hand_size
SPEED_ALPHA = 0.4     # EMA ของความเร็ว


class Bounds(Protocol):
    x: float
    y: float
    w: float
    h: float


def soft_deadzone(m: float, dz: float) -> float:
    """deadzone นุ่ม ต่อเนื่องระดับ C1: 0 ใต้ dz, โค้งกำลังสองถึง 2dz แล้วเป็นเส้นตรง"""
    if dz <= 0:
        return m
    if m <= dz:
        return 0.0
    if m < 2 * dz:
        return (m - dz) ** 2 / (2 * dz)
    return m - 1.5 * dz


def smoothstep(lo: float, hi: float, v: float) -> float:
    t = min(1.0, max(0.0, (v - lo) / (hi - lo)))
    return t * t * (3 - 2 * t)


def gain_curve(v: float, low: float, high: float) -> float:
    return low + (high - low) * smoothstep(V_LO, V_HI, v)


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


class Motion:
    def __init__(self, cfg: MotionConfig, bounds: Bounds, px_per_hand: float) -> None:
        self.cfg = cfg
        self.bounds = bounds
        self.px_per_hand = px_per_hand
        self.speed = 0.0
        self.gain = cfg.accel_low_gain
        self._warned = False
        self._fx = OneEuroFilter(cfg.min_cutoff, cfg.beta)
        self._fy = OneEuroFilter(cfg.min_cutoff, cfg.beta)
        self.reset((bounds.x, bounds.y))

    def reset(self, cursor_xy: tuple[float, float]) -> None:
        self._pos = [float(cursor_xy[0]), float(cursor_xy[1])]
        self._prev: tuple[float, float] | None = None
        self._size: float | None = None
        self._fx.reset()
        self._fy.reset()
        self._out = (self._pos[0], self._pos[1])
        self.speed = 0.0
        self.gain = self.cfg.accel_low_gain

    def update(self, anchor: tuple[float, float], hand_size: float,
               aspect: float, dt: float) -> tuple[float, float]:
        cfg, b = self.cfg, self.bounds
        if cfg.mode == "absolute" and not self._warned:
            log.warning("motion.mode=absolute ยังไม่รองรับ ใช้ relative แทน")
            self._warned = True
        # ขอบเขตของ cursor (float) ในหน่วย px
        x0, x1 = b.x, b.x + b.w - 1
        y0, y1 = b.y, b.y + b.h - 1

        # ขนาดมือแบบ EMA เพื่อให้ delta เป็นหน่วย hand-size นิ่งขึ้น
        hs = max(hand_size, 1e-6)
        self._size = hs if self._size is None else self._size + SIZE_ALPHA * (hs - self._size)
        size = max(self._size, 1e-6)

        prev, self._prev = self._prev, anchor
        if prev is None:  # เฟรมแรกหลัง reset: แค่จำจุดอ้างอิง
            return self._out

        dx = (anchor[0] - prev[0]) * aspect / size
        dy = (anchor[1] - prev[1]) / size
        m = (dx * dx + dy * dy) ** 0.5

        dt_c = _clamp(dt, DT_MIN, DT_MAX)
        dz = cfg.deadzone * (dt_c / DT_REF)
        m2 = soft_deadzone(m, dz)
        scale = m2 / m if m > 0 else 0.0

        # เฟรมที่มาช้าผิดปกติ: ยังขยับตามจริง แต่ไม่ใช้คำนวณความเร็ว
        if dt <= DT_MAX:
            self.speed += SPEED_ALPHA * (m2 / dt_c - self.speed)
        self.gain = gain_curve(self.speed, cfg.accel_low_gain, cfg.accel_high_gain)

        k = scale * self.gain * cfg.sensitivity * self.px_per_hand
        self._pos[0] = _clamp(self._pos[0] + dx * k, x0, x1)
        self._pos[1] = _clamp(self._pos[1] + dy * k, y0, y1)

        ox = _clamp(self._fx(self._pos[0], dt_c), x0, x1)
        oy = _clamp(self._fy(self._pos[1], dt_c), y0, y1)
        self._out = (ox, oy)
        return self._out
