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


def _axis_pull(c: float, half: float, v: float, reach: float) -> float:
    """offset ดูดตามแกนเดียว: แสดงที่ c + d*|d|/r แทน c + d (d = v - c, r = half + reach)
    กลางปุ่มดูดแรง แล้วจางเป็น 0 พอดีที่ขอบโซน hover → ปล่อยปุ่มแล้วไม่กระโดด
    และ d*|d|/r เพิ่มตาม d เสมอ cursor จึงไม่วิ่งสวนทิศมือ"""
    r = half + reach
    d = v - c
    if abs(d) >= r:
        return 0.0
    return d * abs(d) / r - d


class Snapper:
    """ปรับตำแหน่ง cursor ที่ Motion คำนวณ ให้เกาะปุ่มเมื่อมือช้า

    แยกสองตำแหน่ง (ห้ามป้อนแรงดูดกลับเข้า Motion ไม่งั้นแรงดูด+หนืดเข้าสมดุลค้างที่ปุ่ม):
    - virtual: ตำแหน่งตามมือที่ผ่านแค่หนืด → ป้อนกลับเข้า Motion ได้ (หนืดแค่ลดขนาดการขยับ)
    - out = virtual + offset ดูด → ส่งให้เมาส์เท่านั้น offset ขึ้นกับ virtual อย่างเดียว
      มือขยับต่อเนื่องจึงพา virtual ออกนอกปุ่มได้เสมอ

    - หนืด: บนปุ่มและมือไม่เร็วเกิน escape_speed การขยับเหลือ friction เท่า
    - แม่เหล็ก: มือช้ากว่า slow_speed ดึงที่แสดงเข้ากลางปุ่ม (ดู _axis_pull) offset ค่อย ๆ
      เปลี่ยนแบบ exponential (pull_time_s) ทั้งตอนเริ่มดูด เปลี่ยนปุ่ม และปล่อย
      เฉพาะแกนที่ปุ่มเล็ก (<= search_px): ปุ่มใหญ่ เช่นแท็บ/รายการยาว ดูดตามแกนยาวแล้วน่ารำคาญ
      (หนืดยังทำงานทุกแกน)
    - มือเร็วกว่า escape_speed = สะบัดหลุด ไม่เกาะ
    """

    def __init__(self, cfg: SnapConfig) -> None:
        self.cfg = cfg
        self.hovered = None
        self.offset: tuple[float, float] = (0.0, 0.0)  # out - virtual ล่าสุด

    def reset(self) -> None:
        self.hovered = None
        self.offset = (0.0, 0.0)

    def clear_offset(self) -> None:
        """ทิ้ง offset (หลังย้าย virtual ไปที่ cursor ที่แสดงอยู่แล้ว เช่นหลังคลิก)"""
        self.offset = (0.0, 0.0)

    def _pull(self, hov, vx: float, vy: float) -> tuple[float, float]:
        cfg = self.cfg
        reach = cfg.capture_px * HYSTERESIS  # = ระยะที่ _select ยังคง hover
        cx, cy = hov.center
        w, h = hov.right - hov.left, hov.bottom - hov.top
        px = _axis_pull(cx, w / 2, vx, reach) if w <= cfg.search_px else 0.0
        py = _axis_pull(cy, h / 2, vy, reach) if h <= cfg.search_px else 0.0
        return (px, py)

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

    def adjust(self, prev_virtual: tuple[float, float], target: tuple[float, float],
               speed: float, dt: float, targets: Sequence
               ) -> tuple[tuple[float, float], tuple[float, float], object | None]:
        """target = ตำแหน่งจาก Motion (เดินต่อจาก prev_virtual) → (virtual, out, ปุ่มที่เกาะ)"""
        cfg = self.cfg
        if not cfg.enabled:
            self.reset()
            return (target, target, None)
        x, y = target
        hov = self._select(x, y, targets) if targets else None
        if hov is not None and speed >= cfg.escape_speed:
            hov = None  # สะบัดเร็ว: หลุด ไม่เกาะ
        self.hovered = hov

        want = (0.0, 0.0)
        if hov is None:
            vx, vy = x, y
        else:
            # หนืด: ขยับตามเป้าแค่ส่วนหนึ่ง (อยู่ระหว่าง prev_virtual กับ target เสมอ)
            vx = prev_virtual[0] + (x - prev_virtual[0]) * cfg.friction
            vy = prev_virtual[1] + (y - prev_virtual[1]) * cfg.friction
            if speed < cfg.slow_speed:
                want = self._pull(hov, vx, vy)

        # offset ค่อย ๆ เข้าหาค่าที่ต้องการ: เริ่มดูด/เปลี่ยนปุ่ม/ปล่อยปุ่ม ไม่มีก้าวกระโดด
        dt_c = min(DT_MAX, max(DT_MIN, dt))
        alpha = 1.0 - math.exp(-dt_c / cfg.pull_time_s)
        ox = self.offset[0] + (want[0] - self.offset[0]) * alpha
        oy = self.offset[1] + (want[1] - self.offset[1]) * alpha
        self.offset = (ox, oy)
        return ((vx, vy), (vx + ox, vy + oy), hov)
