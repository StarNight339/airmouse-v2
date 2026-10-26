"""แปลงการขยับมือ (anchor ในภาพ) เป็นตำแหน่ง cursor แบบ relative (pure logic)

pipeline: pre-filter (One Euro เบา ๆ) → leash deadzone (รัศมี R หน่วย hand-size)
→ ความเร็วของจุดอ้างอิง → gain ตามความเร็ว → สะสมเป็น px (float, clamp ที่ขอบ)
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Protocol

from .config import MotionConfig
from .filters import OneEuroFilter

log = logging.getLogger(__name__)

V_LO = 0.5            # ความเร็ว (hand-size/s) ที่ต่ำกว่านี้ใช้ gain ต่ำสุด
V_HI = 3.0            # ความเร็วที่สูงกว่านี้ใช้ gain สูงสุด
# dt ที่ใช้จริง (pre-filter, ความเร็ว) clamp ในช่วงนี้ เพื่อกันค่าเพี้ยนเท่านั้น
# (กล้อง 10 fps → dt = 0.1 ต้องใช้ตามจริง)
DT_MIN, DT_MAX = 0.005, 0.2
# เฟรม "มาช้า" = ช้ากว่า dt เฉลี่ย LATE_FACTOR เท่า หรือช้าเกิน LATE_ABS วินาที
# ยังขยับ cursor ตามจริงแต่ไม่นำไปคำนวณความเร็ว (สัมพัทธ์ เพื่อให้ใช้ได้ทุก fps)
LATE_FACTOR = 2.5
LATE_ABS = 0.25
DT_ALPHA = 0.1        # EMA ของ dt
SIZE_ALPHA = 0.2      # EMA ของ hand_size
SPEED_ALPHA = 0.4     # EMA ของความเร็ว
# pre-filter ของ anchor (หน่วยภาพ normalize): ตัด jitter ของ landmark ก่อนเข้า leash
# min_cutoff 1.5 Hz ที่ 30 fps ลด noise เหลือ ~0.37 เท่า; beta ทำให้ตอนขยับเร็ว cutoff
# เพิ่ม (มือเร็ว ~0.5 ภาพ/s → +~3 Hz) lag จึงน้อย
PRE_MIN_CUTOFF = 1.5
PRE_BETA = 6.0


HISTORY_S = 0.5          # เก็บตำแหน่ง output ย้อนหลังเท่านี้ (ใช้ย้อนตำแหน่งคลิก)
BOOST_LEASH = 1.5        # boost_smoothing: คูณรัศมี leash ตอนเริ่ม แล้วลดเชิงเส้นกลับเป็น 1
BOOST_CUTOFF = 0.5       # boost_smoothing: คูณ min_cutoff ของ pre-filter ตอนเริ่ม
# scroll แบบจอยสติ๊ก: ระยะมือจากจุดที่เริ่มท่า scroll = ความเร็ว (ไม่ต้องเลื่อนมือไกลจนหลุดกล้อง)
SCROLL_JOY_DZ = 0.10     # ระยะ (hand-size) รอบจุดเริ่มที่ยังไม่ scroll กันมือสั่น
SCROLL_MAX_RATE = 3000.0  # wheel/s สูงสุด (= 25 notch/s)
SCROLL_CUTOFF = 3.0      # pre-filter ของ scroll (Hz) เบากว่าของ cursor เพราะมือเคลื่อนช้า/จงใจ


class Bounds(Protocol):
    x: float
    y: float
    w: float
    h: float


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
        self._fx = OneEuroFilter(PRE_MIN_CUTOFF, PRE_BETA)
        self._fy = OneEuroFilter(PRE_MIN_CUTOFF, PRE_BETA)
        self._hist: deque[tuple[float, float, float]] = deque()
        self._last_ts: float | None = None
        self._boost_total = 0.0
        self._boost_left = 0.0
        self._sf = OneEuroFilter(SCROLL_CUTOFF, 0.0)
        self.reset_scroll()
        self.reset((bounds.x, bounds.y))

    def reset(self, cursor_xy: tuple[float, float]) -> None:
        self._pos = [float(cursor_xy[0]), float(cursor_xy[1])]
        self._ref: tuple[float, float] | None = None  # จุดอ้างอิง leash (หน่วยภาพ, x คูณ aspect)
        self._size: float | None = None
        self._dt_ema: float | None = None
        self._fx.reset()
        self._fy.reset()
        self.speed = 0.0
        self.gain = self.cfg.accel_low_gain
        self._hist.clear()
        self._last_ts = None
        self._boost_total = self._boost_left = 0.0
        self._fx.min_cutoff = PRE_MIN_CUTOFF
        self._fy.min_cutoff = PRE_MIN_CUTOFF

    # ---- ประวัติตำแหน่ง / ตั้งตำแหน่ง / boost ----
    def _record(self, ts: float | None) -> None:
        if ts is None:
            return
        self._last_ts = ts
        self._hist.append((ts, self._pos[0], self._pos[1]))
        while self._hist and ts - self._hist[0][0] > HISTORY_S:
            self._hist.popleft()

    def history_at(self, t: float) -> tuple[float, float]:
        """ตำแหน่ง output ล่าสุดที่ ts <= t; ถ้า t เก่ากว่า buffer คืนตัวเก่าสุด; ว่างคืนตำแหน่งปัจจุบัน"""
        if not self._hist:
            return (self._pos[0], self._pos[1])
        best = self._hist[0]
        for sample in self._hist:
            if sample[0] <= t:
                best = sample
            else:
                break
        return (best[1], best[2])

    def set_position(self, xy: tuple[float, float]) -> None:
        """ย้ายตำแหน่งสะสมโดยไม่แตะ leash/filter/speed (ใช้หลังคลิกย้อนตำแหน่ง)"""
        b = self.bounds
        self._pos[0] = _clamp(float(xy[0]), b.x, b.x + b.w - 1)
        self._pos[1] = _clamp(float(xy[1]), b.y, b.y + b.h - 1)
        if self._hist:  # แก้ตัวล่าสุดให้ตรงกับตำแหน่งใหม่ ไม่ให้ history_at ย้อนกลับไปค่าเก่า
            ts = self._hist[-1][0]
            self._hist[-1] = (ts, self._pos[0], self._pos[1])

    def boost_smoothing(self, seconds: float) -> None:
        """เพิ่ม smoothing ชั่วคราว (เริ่มลาก): leash ×BOOST_LEASH และ cutoff ×BOOST_CUTOFF
        แล้วค่อยลดเชิงเส้นกลับเป็นปกติตาม dt สะสม; เปลี่ยนแบบต่อเนื่อง ไม่มีก้าวกระโดดตอนจบ"""
        self._boost_total = self._boost_left = max(0.0, seconds)

    def _boost(self) -> float:
        return self._boost_left / self._boost_total if self._boost_total > 0 else 0.0

    # ---- scroll ----
    def reset_scroll(self) -> None:
        self._sf.reset()
        self._s_ref: float | None = None
        self._s_acc = 0.0

    def scroll_delta(self, anchor_y: float, hand_size: float, dt: float) -> int:
        """จอยสติ๊ก: มือสูงกว่าจุดเริ่ม = scroll ขึ้น (บวก), ต่ำกว่า = ลง; ยิ่งห่างยิ่งเร็ว
        ความเร็ว = scroll_speed × (ระยะ − deadzone) wheel/s; เศษสะสมไว้ ไม่ทิ้ง"""
        dt_c = min(DT_MAX, max(DT_MIN, dt))
        y = self._sf(anchor_y, dt_c)
        if self._s_ref is None:
            self._s_ref = y
            return 0
        size = max(hand_size, 1e-6)
        off = (self._s_ref - y) / size  # หน่วย hand-size, บวก = มือยกขึ้น
        mag = abs(off) - SCROLL_JOY_DZ
        if mag <= 0:
            return 0
        rate = min(SCROLL_MAX_RATE, getattr(self.cfg, "scroll_speed", 2400.0) * mag)
        self._s_acc += rate * dt_c * (1 if off > 0 else -1)
        n = int(self._s_acc)  # ตัดเข้าหา 0 เศษคงไว้ใน _s_acc
        self._s_acc -= n
        return n

    def update(self, anchor: tuple[float, float], hand_size: float,
               aspect: float, dt: float, ts: float | None = None) -> tuple[float, float]:
        out = self._update(anchor, hand_size, aspect, dt)
        self._record(ts)
        return out

    def _update(self, anchor: tuple[float, float], hand_size: float,
                aspect: float, dt: float) -> tuple[float, float]:
        cfg, b = self.cfg, self.bounds
        if cfg.mode == "absolute" and not self._warned:
            log.warning("motion.mode=absolute ยังไม่รองรับ ใช้ relative แทน")
            self._warned = True
        x0, x1 = b.x, b.x + b.w - 1
        y0, y1 = b.y, b.y + b.h - 1

        hs = max(hand_size, 1e-6)
        self._size = hs if self._size is None else self._size + SIZE_ALPHA * (hs - self._size)
        size = max(self._size, 1e-6)

        dt_c = min(DT_MAX, max(DT_MIN, dt))
        if self._dt_ema is None:
            self._dt_ema = dt_c
        late = dt > LATE_FACTOR * self._dt_ema or dt > LATE_ABS
        # อัปเดต dt_ema เสมอ (dt_c ถูกจำกัดไว้แล้ว) ไม่งั้นถ้า fps ตกถาวร เช่นห้องมืดลง
        # ทุกเฟรมจะถูกนับว่า "ช้า" ตลอดไป แล้ว speed/gain จะค้าง
        self._dt_ema += DT_ALPHA * (dt_c - self._dt_ema)
        boost = self._boost()
        if self._boost_left > 0:
            self._boost_left = max(0.0, self._boost_left - dt_c)
        cut = PRE_MIN_CUTOFF * (1.0 - (1.0 - BOOST_CUTOFF) * boost)
        self._fx.min_cutoff = self._fy.min_cutoff = cut
        px = self._fx(anchor[0] * aspect, dt_c)
        py = self._fy(anchor[1], dt_c)

        if self._ref is None:  # เฟรมแรกหลัง reset: แค่จำจุดอ้างอิง
            self._ref = (px, py)
            return (self._pos[0], self._pos[1])

        # leash: ภายในรัศมี R (hand-size) ไม่ขยับ; นอกรัศมีลาก ref ตามให้ห่าง p เท่า R
        rx, ry = self._ref
        ex, ey = px - rx, py - ry
        dist = (ex * ex + ey * ey) ** 0.5
        radius = cfg.deadzone * size * (1.0 + (BOOST_LEASH - 1.0) * boost)
        mx = my = 0.0
        if dist > radius:
            f = (dist - radius) / dist
            mx, my = ex * f, ey * f
            self._ref = (rx + mx, ry + my)
        mx, my = mx / size, my / size  # หน่วย hand-size
        move = (mx * mx + my * my) ** 0.5

        # เฟรมที่มาช้าผิดปกติ: ยังขยับตามจริง แต่ไม่ใช้คำนวณความเร็ว
        if not late:
            self.speed += SPEED_ALPHA * (move / dt_c - self.speed)
        self.gain = gain_curve(self.speed, cfg.accel_low_gain, cfg.accel_high_gain)

        k = self.gain * cfg.sensitivity * self.px_per_hand
        self._pos[0] = _clamp(self._pos[0] + mx * k, x0, x1)
        self._pos[1] = _clamp(self._pos[1] + my * k, y0, y1)
        return (self._pos[0], self._pos[1])
