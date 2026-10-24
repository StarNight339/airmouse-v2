"""ตัวช่วยวัดอัตรา (FPS) แบบหน้าต่างเวลาเลื่อน"""
from __future__ import annotations

import time
from collections import deque


class RateMeter:
    """เรียก tick() ทุกครั้งที่เกิดเหตุการณ์ แล้วอ่าน rate (ครั้ง/วินาที) จาก thread ไหนก็ได้"""

    def __init__(self, window_s: float = 1.0):
        self._window = window_s
        self._times: deque[float] = deque()
        self.rate = 0.0

    def tick(self, t: float | None = None) -> None:
        t = time.monotonic() if t is None else t
        self._times.append(t)
        while self._times and t - self._times[0] > self._window:
            self._times.popleft()
        span = self._times[-1] - self._times[0]
        self.rate = (len(self._times) - 1) / span if span > 0 else 0.0
