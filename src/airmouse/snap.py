"""Snap: cursor เกาะปุ่มอัตโนมัติ (แม่เหล็ก + หนืด) — pure logic ไม่แตะ OS

ปุ่ม (target) เป็น object ที่มี left, top, right, bottom (px) และ property center -> (x, y)
"""
from __future__ import annotations

import math
from typing import Sequence

from .config import SnapConfig

DT_MIN, DT_MAX = 0.005, 0.2
HYSTERESIS = 1.5  # คง hover ไว้ตราบที่จุดอยู่ในกรอบปุ่มที่ขยาย capture_px * ค่านี้


def _inside(t, x: float, y: float, margin: float = 0.0) -> bool:
    return (t.left - margin <= x <= t.right + margin
            and t.top - margin <= y <= t.bottom + margin)


def _center_dist2(t, x: float, y: float) -> float:
    cx, cy = t.center
    return (cx - x) ** 2 + (cy - y) ** 2


class Snapper:
    """ปรับตำแหน่ง cursor ที่ Motion คำนวณ ให้เกาะปุ่มเมื่อมือช้า
    - หนืด: บนปุ่มและมือไม่เร็วเกิน escape_speed การขยับเหลือ friction เท่า
    - แม่เหล็ก: มือช้ากว่า slow_speed ดูดเข้ากลางปุ่มแบบ exponential (pull_time_s)
      เฉพาะแกนที่ปุ่มเล็ก (<= search_px): ปุ่มใหญ่ เช่นแท็บ/รายการยาว ดูดตามแกนยาวแล้วน่ารำคาญ
      (หนืดยังทำงานทุกแกน)
    - มือเร็วกว่า escape_speed = สะบัดหลุด ไม่เกาะ
    """

    def __init__(self, cfg: SnapConfig) -> None:
        self.cfg = cfg
        self.hovered = None

    def reset(self) -> None:
        self.hovered = None

    def _select(self, x: float, y: float, targets: Sequence):
        cfg = self.cfg
        cur = self.hovered
        if cur is not None and cur in targets:
            if _inside(cur, x, y, cfg.capture_px * HYSTERESIS):
                if _inside(cur, x, y):
                    return cur
                # อยู่ในโซนคงที่ แต่นอกกรอบจริง: สลับเมื่อกรอบจริงของปุ่มอื่นครอบจุดนี้เท่านั้น
                inside = [t for t in targets if t is not cur and _inside(t, x, y)]
                if inside:
                    return min(inside, key=lambda t: _center_dist2(t, x, y))
                return cur
        cands = [t for t in targets if _inside(t, x, y, cfg.capture_px)]
        if not cands:
            return None
        return min(cands, key=lambda t: _center_dist2(t, x, y))

    def adjust(self, prev_out: tuple[float, float], target: tuple[float, float], speed: float,
               dt: float, targets: Sequence) -> tuple[tuple[float, float], object | None]:
        cfg = self.cfg
        if not cfg.enabled:
            self.hovered = None
            return (target, None)
        x, y = target
        hov = self._select(x, y, targets) if targets else None
        if hov is not None and speed >= cfg.escape_speed:
            hov = None  # สะบัดเร็ว: หลุด ไม่เกาะ
        self.hovered = hov
        if hov is None:
            return (target, None)

        # หนืด: ขยับตามเป้าแค่ส่วนหนึ่ง (ผลอยู่ระหว่าง prev_out กับ target เสมอ → ไม่กระโดด)
        ox = prev_out[0] + (x - prev_out[0]) * cfg.friction
        oy = prev_out[1] + (y - prev_out[1]) * cfg.friction

        # แม่เหล็ก: ดูดเข้ากลางปุ่ม (alpha < 1 จึงไม่เลยกลางปุ่ม)
        if speed < cfg.slow_speed:
            dt_c = min(DT_MAX, max(DT_MIN, dt))
            alpha = 1.0 - math.exp(-dt_c / cfg.pull_time_s)
            cx, cy = hov.center
            if hov.right - hov.left <= cfg.search_px:
                ox += (cx - ox) * alpha
            if hov.bottom - hov.top <= cfg.search_px:
                oy += (cy - oy) * alpha
        return ((ox, oy), hov)
