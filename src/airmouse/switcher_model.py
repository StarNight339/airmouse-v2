"""โมเดล carousel ของตัวสลับหน้าต่าง (pure logic ไม่แตะ OS/Qt)

รับ switcher_dx จาก GestureState (ระยะเลื่อนมือหน่วย "การ์ด" นับจากตอนเปิด) แล้วคืนตำแหน่งต่อเนื่อง
ให้ UI วาด: ขอบมียางยืด, หยุดนิ่งสั้น ๆ จะ snap เข้าการ์ดที่ใกล้สุด, ตำแหน่งตามเป้าแบบหน่วงเบา ๆ
"""
from __future__ import annotations

import math

RUBBER_GAIN = 0.3        # เลยปลายทางไปเท่าไหร่ ยางยืดเหลือสัดส่วนนี้
RUBBER_MAX = 0.5         # ยืดเลยปลายได้สูงสุด (การ์ด)
SNAP_MOVE = 0.05         # เป้าขยับน้อยกว่านี้ (การ์ด) ถือว่านิ่ง
SNAP_HOLD_S = 0.15       # นิ่งนานเท่านี้ → snap เข้าการ์ด
FOLLOW_TAU_S = 0.08      # ค่าคงที่เวลาของการตามเป้า (exponential ไม่ overshoot)
DT_MIN = 0.005
DT_MAX = 0.2


class Carousel:
    """carousel ฝั่ง UI: ตำแหน่งต่อเนื่อง (หน่วยการ์ด) จาก switcher_dx ของมือ"""

    def __init__(self, count: int, start: int | None = None) -> None:
        self.count = max(0, int(count))
        if start is None:
            start = 1 if self.count >= 2 else 0
        self._base = float(min(max(int(start), 0), max(self.count - 1, 0)))
        self.position: float = self._base   # ตำแหน่งต่อเนื่องที่ UI วาด
        self._dx = 0.0                      # dx ล่าสุด (ใช้ตอน remove)
        self._ref = self._base              # เป้าอ้างอิงสำหรับตรวจว่านิ่ง
        self._still = 0.0

    @property
    def selected(self) -> int:
        if self.count <= 0:
            return 0
        return min(max(int(math.floor(self.position + 0.5)), 0), self.count - 1)

    @property
    def target_index(self) -> int:
        """การ์ดที่มือชี้ "ตอนนี้" = round(clamp(base + dx ล่าสุด)) ไม่ผ่าน rubber band และไม่ผ่านการตามแบบหน่วง
        UI ใช้ค่านี้ตอนย่อ (minimize) และ commit — เรียก update(dx, dt) ด้วย dx ของเฟรมเดียวกันก่อนเสมอ
        (`selected` มาจาก position ที่หน่วง จึงอาจชี้การ์ดข้างเคียงระหว่างสะบัด)"""
        if self.count <= 0:
            return 0
        raw = self._base + self._dx
        return min(max(int(math.floor(raw + 0.5)), 0), self.count - 1)

    def _rubber(self, raw: float) -> float:
        hi = max(self.count - 1, 0)
        if raw < 0:
            return -min(-raw * RUBBER_GAIN, RUBBER_MAX)
        if raw > hi:
            return hi + min((raw - hi) * RUBBER_GAIN, RUBBER_MAX)
        return raw

    def update(self, dx: float, dt: float) -> None:
        """dx = GestureState.switcher_dx (การ์ดที่เลื่อนจากตอนเปิด); dt วินาที (ถูกจำกัด 0.005–0.2)"""
        if not math.isfinite(dt):
            dt = DT_MIN
        dt = min(max(dt, DT_MIN), DT_MAX)
        if math.isfinite(dx):
            self._dx = dx
        if self.count <= 0:
            self.position = 0.0
            return
        target = self._rubber(self._base + self._dx)
        if abs(target - self._ref) < SNAP_MOVE:
            self._still += dt
        else:
            self._ref = target
            self._still = 0.0
        if self._still >= SNAP_HOLD_S:
            hi = self.count - 1
            target = float(min(max(int(math.floor(target + 0.5)), 0), hi))
        alpha = 1.0 - math.exp(-dt / FOLLOW_TAU_S)  # 0 < alpha < 1 → ไม่ overshoot
        self.position += (target - self.position) * alpha

    def remove(self, index: int) -> None:
        """การ์ด index ถูกย่อ/ปิด: ปรับให้การ์ดที่เลือกอยู่ยังดูต่อเนื่อง ไม่กระโดด"""
        if self.count <= 0 or not 0 <= index < self.count:
            return
        sel = self.target_index
        self.count -= 1
        if self.count == 0:
            self.position = 0.0
            self._base = 0.0
            self._ref = 0.0
            self._still = 0.0
            return
        if index < sel:
            new_sel = sel - 1
            self.position -= 1.0       # ดัชนีทั้งหมดเลื่อนลง ภาพต้องเลื่อนตามให้การ์ดเดิมอยู่ที่เดิม
        elif index == sel:
            if sel < self.count:       # การ์ดถัดไปเลื่อนเข้าช่องเดิม: เริ่มจากขวา แล้วไหลเข้ากลาง
                new_sel = sel
                self.position -= 1.0
            else:                      # เป็นใบสุดท้าย: position คงเดิม ใบก่อนหน้าไหลเข้ามาเอง
                new_sel = self.count - 1
        else:
            new_sel = sel
        # (กรณีใบสุดท้าย position อาจเกินปลายไป 1 การ์ด ตั้งใจให้ไหลกลับ — ไม่ clamp)
        # base + dx ปัจจุบัน = การ์ดที่เลือกใหม่ (dx ถัดไปที่ต่อเนื่องจะไม่ทำให้กระโดด)
        self._base = new_sel - self._dx
        self._ref = float(new_sel)
        self._still = 0.0
