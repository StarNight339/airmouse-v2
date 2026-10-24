"""ตรวจท่ามือ: toggle, clutch, drag, scroll, click (pure logic)

ลำดับความสำคัญเมื่อท่าชนกัน: drag > clutch > scroll > click
"""
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
    mode: str = "off"                   # "off" | "move" | "clutch" | "drag" | "scroll"
    click_bend_ts: float | None = None  # เวลาที่นิ้วเริ่มพับของคลิก (มีค่าเฉพาะเฟรมที่เกิด click)
    index_progress: float = 0.0         # 0..1 ใกล้ถึงเกณฑ์คลิก
    middle_progress: float = 0.0
    pinch_progress: float = 0.0


class GestureTracker:
    def __init__(self, cfg: GestureConfig) -> None:
        self.cfg = cfg
        self.enabled = False
        self.clutch = False
        self.dragging = False
        self.scrolling = False
        self._palm_n = 0          # จำนวนเฟรมติดกันที่เห็นฝ่ามือ
        self._palm_start: float | None = None
        self._need_release = False  # หลัง toggle ต้องปล่อยฝ่ามือก่อน
        self._lost_since: float | None = None  # เวลาที่มือหายไปครั้งล่าสุด
        self._fist_n = 0          # เฟรมติดกันที่ท่ากำ (เข้า)
        self._rel_n = 0           # เฟรมติดกันที่ท่าปล่อย (ออก)
        self._pinch_n = 0         # เฟรมติดกันที่จีบ (เข้า)
        self._pinch_rel_n = 0     # เฟรมติดกันที่ปล่อยจีบ (ขณะ drag)
        self._scroll_n = 0        # เฟรมติดกันที่เป็นท่า scroll
        self._scroll_off_n = 0    # เฟรมติดกันที่หลุดท่า scroll
        # คลิก: armed = เห็นนิ้วเหยียดแล้ว (ใน mode move) และยังไม่ได้คลิกรอบนี้
        self._idx_armed = False
        self._mid_armed = False
        self._idx_n = 0           # เฟรมติดกันที่ curl < curl_down
        self._mid_n = 0
        self._idx_bend: float | None = None
        self._mid_bend: float | None = None

    def _reset_click(self) -> None:
        self._idx_armed = self._mid_armed = False
        self._idx_n = self._mid_n = 0
        self._idx_bend = self._mid_bend = None

    def _end_actions(self, events: list[str]) -> None:
        """ปิด drag/scroll ทันที (มือหาย/ปิดระบบ) เพื่อไม่ให้ปุ่มค้าง"""
        if self.dragging:
            self.dragging = False
            events.append("drag_end")
        if self.scrolling:
            self.scrolling = False
            events.append("scroll_end")
        self._pinch_n = self._pinch_rel_n = 0
        self._scroll_n = self._scroll_off_n = 0

    def _mode(self) -> str:
        if not self.enabled:
            return "off"
        if self.dragging:
            return "drag"
        if self.clutch:
            return "clutch"
        if self.scrolling:
            return "scroll"
        return "move"

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
        self._end_actions(events)
        self._reset_click()

    def update(self, feats: HandFeatures | None, ts: float) -> GestureState:
        cfg = self.cfg
        events: list[str] = []
        if feats is None:
            self._lost(ts, events)
            return GestureState(self.enabled, False, 0.0, False, False, tuple(events),
                                mode=self._mode())

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

        idx, mid, ring, pinky = four
        pinch = feats.pinch
        span = cfg.curl_up - cfg.curl_down
        idx_prog = _clamp01((cfg.curl_up - idx) / span)
        mid_prog = _clamp01((cfg.curl_up - mid) / span)
        pinch_prog = _clamp01((cfg.pinch_up - pinch) / (cfg.pinch_up - cfg.pinch_down))

        click_ts: float | None = None
        if not self.enabled:
            self._fist_n = self._rel_n = 0
            if self.clutch:  # กันไว้: ปกติเข้าไม่ถึง เพราะฝ่ามือคลาย clutch ก่อนครบเวลา hold
                self.clutch = False
                events.append("clutch_off")
            self._end_actions(events)  # toggle off ระหว่างลาก/scroll → ปล่อยทันที
            self._reset_click()
            fist = False
        else:
            self._update_drag(pinch, events)
            self._update_clutch(four, fist_raw, events)
            fist = self.clutch or fist_raw
            self._update_scroll(idx, mid, ring, pinky, events)
            if self._mode() != "move":
                self._reset_click()
            else:
                click_ts = self._update_clicks(idx, mid, ring, pinch, fist_raw, ts, events)

        return GestureState(self.enabled, self.clutch, progress, open_palm, fist, tuple(events),
                            mode=self._mode(), click_bend_ts=click_ts,
                            index_progress=idx_prog, middle_progress=mid_prog,
                            pinch_progress=pinch_prog)

    def _update_drag(self, pinch: float, events: list[str]) -> None:
        """จีบค้าง = drag (สำคัญสุด); ปล่อยเมื่อ pinch > pinch_up"""
        cfg = self.cfg
        if not self.dragging:
            if self.clutch:
                self._pinch_n = 0
                return
            self._pinch_n = self._pinch_n + 1 if pinch < cfg.pinch_down else 0
            if self._pinch_n >= cfg.confirm_frames:
                if self.scrolling:
                    self.scrolling = False
                    events.append("scroll_end")
                self.dragging = True
                self._pinch_n = self._pinch_rel_n = 0
                self._fist_n = 0
                events.append("drag_start")
        else:
            self._pinch_rel_n = self._pinch_rel_n + 1 if pinch > cfg.pinch_up else 0
            if self._pinch_rel_n >= cfg.confirm_frames:
                self.dragging = False
                self._pinch_rel_n = 0
                self._reset_click()  # ต้องเหยียดนิ้วชี้ใหม่ก่อนคลิก
                events.append("drag_end")

    def _update_clutch(self, four: list[float], fist_raw: bool, events: list[str]) -> None:
        """กำหมัด = clutch (ไม่เริ่มขณะ drag); ปล่อยเมื่อ curl เฉลี่ย > curl_up"""
        cfg = self.cfg
        if self.dragging:
            self._fist_n = self._rel_n = 0
            return
        if not self.clutch:
            self._fist_n = self._fist_n + 1 if fist_raw else 0
            if self._fist_n >= cfg.confirm_frames:
                self.clutch = True
                self._rel_n = 0
                events.append("clutch_on")
                if self.scrolling:
                    self.scrolling = False
                    events.append("scroll_end")
        else:
            mean = sum(four) / len(four)
            self._rel_n = self._rel_n + 1 if mean > cfg.curl_up else 0
            if self._rel_n >= cfg.confirm_frames:
                self.clutch = False
                self._fist_n = 0
                events.append("clutch_off")
                self._reset_click()  # ปล่อย clutch แล้วต้อง re-arm

    def _update_scroll(self, idx: float, mid: float, ring: float, pinky: float,
                       events: list[str]) -> None:
        """ท่า scroll: ชี้+กลางเหยียด นางกับก้อยพับ"""
        cfg = self.cfg
        if self.dragging or self.clutch:
            self._scroll_n = 0
            return
        pose = (idx > cfg.curl_up and mid > cfg.curl_up
                and ring < cfg.curl_down and pinky < cfg.curl_down)
        if not self.scrolling:
            self._scroll_n = self._scroll_n + 1 if pose else 0
            if self._scroll_n >= cfg.confirm_frames:
                self.scrolling = True
                self._scroll_off_n = 0
                events.append("scroll_start")
        else:
            self._scroll_off_n = 0 if pose else self._scroll_off_n + 1
            if self._scroll_off_n >= cfg.confirm_frames:
                self.scrolling = False
                self._scroll_n = 0
                events.append("scroll_end")
                self._reset_click()

    def _update_clicks(self, idx: float, mid: float, ring: float, pinch: float,
                       fist_raw: bool, ts: float, events: list[str]) -> float | None:
        """คลิกเฉพาะ mode move; คืน click_bend_ts เมื่อเกิดคลิก"""
        cfg = self.cfg
        # armed + เวลาที่เริ่มพับ
        if idx > cfg.curl_up:
            self._idx_armed = True
            self._idx_bend = None
        elif self._idx_armed and self._idx_bend is None:
            self._idx_bend = ts
        if mid > cfg.curl_up:
            self._mid_armed = True
            self._mid_bend = None
        elif self._mid_armed and self._mid_bend is None:
            self._mid_bend = ts
        self._idx_n = self._idx_n + 1 if idx < cfg.curl_down else 0
        self._mid_n = self._mid_n + 1 if mid < cfg.curl_down else 0

        ok = pinch > cfg.pinch_up and not fist_raw
        # ซ้าย: นิ้วกลางต้องเหยียดอยู่ กันกำมือที่นิ้วชี้พับนำ
        if ok and self._idx_armed and self._idx_n >= cfg.confirm_frames and mid > cfg.curl_up:
            bend = self._idx_bend if self._idx_bend is not None else ts
            self._idx_armed = False
            self._idx_n = 0
            events.append("click_left")
            return bend
        # ขวา: ชี้ต้องเหยียด และนางต้องไม่ถูกพับตาม (กันกำมือที่นิ้วกลางพับนำ)
        if (ok and self._mid_armed and self._mid_n >= cfg.confirm_frames
                and idx > cfg.curl_up and ring > cfg.curl_down):
            bend = self._mid_bend if self._mid_bend is not None else ts
            self._mid_armed = False
            self._mid_n = 0
            events.append("click_right")
            return bend
        return None
