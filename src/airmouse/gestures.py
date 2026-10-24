"""ตรวจท่ามือ: เปิด/ปิดระบบด้วยฝ่ามือค้าง และ clutch ด้วยกำหมัด (pure logic)"""
from __future__ import annotations

from dataclasses import dataclass

from .config import GestureConfig
from .features import HandFeatures

FOUR = ("index", "middle", "ring", "pinky")
LOST_RELEASE_S = 0.5  # มือหายนานเท่านี้ → ล้างเงื่อนไข "ต้องปล่อยฝ่ามือ"


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


@dataclass(frozen=True)
class GestureState:
    enabled: bool
    clutch: bool
    toggle_progress: float
    open_palm: bool
    fist: bool
    events: tuple[str, ...] = ()


class GestureTracker:
    def __init__(self, cfg: GestureConfig) -> None:
        self.cfg = cfg
        self.enabled = False
        self.clutch = False
        self._palm_n = 0          # จำนวนเฟรมติดกันที่เห็นฝ่ามือ
        self._palm_start: float | None = None
        self._need_release = False  # หลัง toggle ต้องปล่อยฝ่ามือก่อน
        self._lost_since: float | None = None  # เวลาที่มือหายไปครั้งล่าสุด
        self._fist_n = 0          # เฟรมติดกันที่ท่ากำ (เข้า)
        self._rel_n = 0           # เฟรมติดกันที่ท่าปล่อย (ออก)

    def _lost(self, ts: float, events: list[str]) -> None:
        self._palm_n = 0
        self._palm_start = None
        # _need_release คงไว้ข้ามมือหายสั้น ๆ (dropout 1 เฟรมต้องไม่ทำให้ toggle ซ้ำ)
        if self._lost_since is None:
            self._lost_since = ts
        self._fist_n = 0
        self._rel_n = 0
        if self.clutch:
            self.clutch = False
            events.append("clutch_off")

    def update(self, feats: HandFeatures | None, ts: float) -> GestureState:
        cfg = self.cfg
        events: list[str] = []
        if feats is None:
            self._lost(ts, events)
            return GestureState(self.enabled, False, 0.0, False, False, tuple(events))

        if self._lost_since is not None:
            if ts - self._lost_since >= LOST_RELEASE_S:  # หายนานพอ ถือว่าปล่อยมือแล้ว
                self._need_release = False
            self._lost_since = None
        c = feats.curl
        four = [c[n] for n in FOUR]
        spread_min = getattr(cfg, "palm_spread", 0.5)
        facing_min = getattr(cfg, "palm_facing_min", 0.3)
        fist_raw = all(v < cfg.curl_down for v in four)
        palm_raw = (not fist_raw and all(v > cfg.curl_up for v in c.values())
                    and getattr(feats, "spread", 0.0) > spread_min
                    and getattr(feats, "palm_facing", 0.0) > facing_min)

        # --- ฝ่ามือค้าง → toggle ---
        progress = 0.0
        self._palm_n = self._palm_n + 1 if palm_raw else 0
        open_palm = self._palm_n >= cfg.confirm_frames
        if not palm_raw:
            self._palm_start = None
            self._need_release = False
        elif open_palm and not self._need_release:
            if self._palm_start is None:
                self._palm_start = ts
            if ts < self._palm_start:  # เวลาย้อนหลัง: เริ่มนับใหม่
                self._palm_start = ts
            progress = _clamp01((ts - self._palm_start) * 1000.0 / cfg.toggle_hold_ms)
            if progress >= 1.0:
                self.enabled = not self.enabled
                events.append("toggle_on" if self.enabled else "toggle_off")
                self._need_release = True
                self._palm_start = None
                progress = 0.0

        # --- กำหมัด → clutch (ทำงานเฉพาะตอน enabled) ---
        if not self.enabled:
            self._fist_n = self._rel_n = 0
            if self.clutch:  # กันไว้: ปกติเข้าไม่ถึง เพราะฝ่ามือคลาย clutch ก่อนครบเวลา hold
                self.clutch = False
                events.append("clutch_off")
            fist = False
        else:
            mean = sum(four) / len(four)
            if not self.clutch:
                self._fist_n = self._fist_n + 1 if fist_raw else 0
                if self._fist_n >= cfg.confirm_frames:
                    self.clutch = True
                    self._rel_n = 0
                    events.append("clutch_on")
            else:
                self._rel_n = self._rel_n + 1 if mean > cfg.curl_up else 0
                if self._rel_n >= cfg.confirm_frames:
                    self.clutch = False
                    self._fist_n = 0
                    events.append("clutch_off")
            fist = self.clutch or fist_raw

        return GestureState(self.enabled, self.clutch, progress, open_palm, fist, tuple(events))
