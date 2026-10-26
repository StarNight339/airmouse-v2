"""วงจรชีวิตของโปรแกรม: active → paused → low_power → exit (pure logic ไม่แตะ OS)"""
from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig


@dataclass(frozen=True)
class LifeState:
    state: str                       # "active" | "paused" | "low_power" | "exit"
    events: tuple[str, ...] = ()     # "paused" "low_power" "exit" "resumed" "resumed_from_low_power"
    idle_s: float = 0.0              # วินาทีที่ไม่เห็นมือ


class Lifecycle:
    """ติดตามว่ามือหายไปนานแค่ไหน
    - เฟรมแรกที่ไม่มีมือ → paused; นานเกิน low_power_after_s → low_power; นานเกิน
      auto_exit_minutes → exit (ปิดถ้า auto_exit_minutes <= 0 หรือ stay=True)
    - มือกลับมา → active พร้อม event "resumed" (และ "resumed_from_low_power" ถ้ามาจาก low power
      ผู้เรียกต้องให้การควบคุมยังปิดอยู่ ต้อง toggle ใหม่)
    - exit เป็นสถานะสุดท้าย ไม่กลับแม้มือปรากฏ
    """

    def __init__(self, cfg: AppConfig, stay: bool = False) -> None:
        self.cfg = cfg
        self.stay = stay
        self._state = "active"
        self._last_present: float | None = None
        self._last_ts: float | None = None
        self._idle = 0.0

    @property
    def state(self) -> str:
        return self._state

    def _exit_after(self) -> float | None:
        minutes = self.cfg.auto_exit_minutes
        if self.stay or minutes <= 0:
            return None
        return minutes * 60.0

    def update(self, hand_present: bool, ts: float) -> LifeState:
        if self._state == "exit":
            return LifeState("exit", (), self._idle)
        if self._last_ts is not None and ts < self._last_ts:
            ts = self._last_ts  # เวลาย้อนหลัง: ถือว่าไม่ขยับ
        self._last_ts = ts
        events: list[str] = []

        if hand_present:
            if self._state in ("paused", "low_power"):
                if self._state == "low_power":
                    events.append("resumed")
                    events.append("resumed_from_low_power")
                else:
                    events.append("resumed")
            self._state = "active"
            self._last_present = ts
            self._idle = 0.0
            return LifeState("active", tuple(events), 0.0)

        if self._last_present is None:  # ยังไม่เคยเห็นมือ: นับจากเฟรมแรก
            self._last_present = ts
        self._idle = ts - self._last_present
        if self._state == "active":
            self._state = "paused"
            events.append("paused")
        if self._state == "paused" and self._idle >= self.cfg.low_power_after_s:
            self._state = "low_power"
            events.append("low_power")
        limit = self._exit_after()
        if limit is not None and self._idle >= limit:
            self._state = "exit"
            events.append("exit")
        return LifeState(self._state, tuple(events), self._idle)
