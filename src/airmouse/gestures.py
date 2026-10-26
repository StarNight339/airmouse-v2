"""ตรวจท่ามือ: toggle (พลิกมือ), clutch, drag, scroll, click (pure logic)

ลำดับความสำคัญเมื่อท่าชนกัน: flip (ปลอดภัยสุด: ตัด drag/scroll) > drag > clutch > scroll > click
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from .config import GestureConfig
from .features import HandFeatures

FOUR = ("index", "middle", "ring", "pinky")
LOST_RELEASE_S = 0.5   # มือหายนานเท่านี้ → ล้างเงื่อนไข "ต้องพลิกกลับก่อน toggle ซ้ำ"
LEARN_WINDOW_S = 2.0   # เรียนรู้ด้านปกติจากเฟรมในช่วง ~2 วินาทีล่าสุด
LEARN_MIN_SAMPLES = 10  # ต้องมีตัวอย่างอย่างน้อยเท่านี้ก่อน commit
LEARN_MIN_SPAN_S = 1.5  # ต้องสังเกตมาอย่างน้อยเท่านี้ (กันพลิกค้างสั้น ๆ ตอนเริ่มถูกนับเป็นด้านปกติ)
LEARN_AGREE = 0.8      # ต้องเห็นด้านเดียวกันอย่างน้อยเท่านี้ของตัวอย่าง
LEARN_MIN_FACING = 0.4  # เฟรมที่ |facing| ต่ำกว่านี้ไม่ใช้เรียนรู้
CLICK_GUARD_S = 0.1   # หน่วงคลิกเท่านี้ ถ้านิ้วนาง/ก้อยพับตาม (กำมือ) ระหว่างนั้น → ยกเลิก
# เฟรมถัดไปที่ 10 fps มาถึง ~96 ms (< 100 ms) — ยอมให้เร็วกว่ากำหนดได้นิดหน่อย
# ไม่งั้นคลิกเลื่อนไปอีก 1 เฟรมเต็ม (~0.1 s)
CLICK_GUARD_SLACK_S = 0.02
CLICK_STALE_S = 0.3   # เฟรมถัดไปมาช้ากว่ากำหนดเกินนี้ (กล้องค้าง) → ทิ้งคลิกที่รอ; 10 fps ปกติช้า ~0.1 s
CLICK_RP_GUARD = 0.88  # ค่าเฉลี่ย curl นาง+ก้อยต่ำกว่านี้ระหว่างหน่วง = กำลังกำมือ
CLICK_PINCH_MARGIN = 0.003  # คลิกต้อง pinch > pinch_down + margin (ไม่ใช่ pinch_up)
SCROLL_HOLD_MIDDLE = 0.55    # ค้าง scroll: นิ้วกลางต่ำได้ถึงเท่านี้
SCROLL_HOLD_RP_MARGIN = 0.07  # ค้าง scroll: นาง/ก้อยเหยียดขึ้นได้เกิน scroll_curl_max เท่านี้
PINCH_MEDIAN_N = 5     # median ของระยะจีบกี่เฟรม (~0.3 s ที่ 16 fps) ตัดค่ากระโดดเฟรมเดียว


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


def _sign(v: float) -> int:
    return 1 if v > 0 else -1 if v < 0 else 0


# ---- predicates ของท่าดิบ (ไม่มี confirm/hysteresis/arming) ใช้ร่วมกับ debug view ----
def _rp_mean(c: dict[str, float]) -> float:
    return (c["ring"] + c["pinky"]) / 2


def is_flip(facing: float, normal_sign: int, cfg: GestureConfig) -> bool:
    return (normal_sign != 0 and _sign(facing) == -normal_sign
            and abs(facing) > cfg.flip_facing_min)


def is_pinch(pinch: float, cfg: GestureConfig) -> bool:
    """จีบ = โป้ง–ชี้ใกล้กัน (GestureTracker ส่งค่าที่ผ่าน median filter แล้ว)"""
    return pinch < cfg.pinch_down


def is_fist(c: dict[str, float], cfg: GestureConfig) -> bool:
    return all(c[n] < cfg.curl_down for n in FOUR)


def is_scroll_pose(c: dict[str, float], cfg: GestureConfig) -> bool:
    return (c["index"] > cfg.scroll_extend_min and c["middle"] > cfg.scroll_extend_min
            and _rp_mean(c) < cfg.scroll_curl_max)


def is_scroll_hold(c: dict[str, float], cfg: GestureConfig) -> bool:
    """เกณฑ์ "ยังอยู่ในท่า scroll" (hysteresis): ชี้ยังเหยียดพอ, กลางงอตามได้, นาง/ก้อยยังพับ"""
    return (c["index"] > cfg.curl_down and c["middle"] > SCROLL_HOLD_MIDDLE
            and _rp_mean(c) < cfg.scroll_curl_max + SCROLL_HOLD_RP_MARGIN)


def is_click_left(c: dict[str, float], pinch: float, cfg: GestureConfig) -> bool:
    """ชี้พับนำ (ชี้ <= กลาง; กลางพับตามได้) โดยนางกับก้อยยังเหยียด (ไม่ใช่กำมือ)"""
    return (c["index"] < cfg.click_curl_down and c["index"] <= c["middle"]
            and _rp_mean(c) > cfg.scroll_curl_max
            and pinch > cfg.pinch_down + CLICK_PINCH_MARGIN)


def is_click_right(c: dict[str, float], pinch: float, cfg: GestureConfig) -> bool:
    return (c["middle"] < cfg.click_curl_down and c["index"] > cfg.curl_up
            and _rp_mean(c) > cfg.scroll_curl_max
            and pinch > cfg.pinch_down + CLICK_PINCH_MARGIN)


def raw_pose(feats: HandFeatures, cfg: GestureConfig, normal_sign: int) -> str:
    """ท่าดิบของเฟรมนี้ ใช้ predicate ชุดเดียวกับ GestureTracker"""
    c = feats.curl
    if is_flip(getattr(feats, "palm_facing", 0.0), normal_sign, cfg):
        return "flip"
    if is_pinch(feats.pinch, cfg):
        return "pinch"
    if is_fist(c, cfg):
        return "fist"
    if is_scroll_pose(c, cfg):
        return "scroll"
    if is_click_left(c, feats.pinch, cfg):
        return "click_left"
    if is_click_right(c, feats.pinch, cfg):
        return "click_right"
    return "move"


@dataclass(frozen=True)
class GestureState:
    enabled: bool
    clutch: bool
    toggle_progress: float             # ความคืบหน้าของการพลิกมือค้าง 0..1
    open_palm: bool                    # เก็บไว้เพื่อความเข้ากันได้: = flipped
    fist: bool
    events: tuple[str, ...] = ()
    mode: str = "off"                  # "off" | "move" | "flip" | "clutch" | "drag" | "scroll"
    click_bend_ts: float | None = None  # เวลาที่นิ้วเริ่มพับของคลิก (มีค่าเฉพาะเฟรมที่เกิด click)
    index_progress: float = 0.0        # 0..1 ใกล้ถึงเกณฑ์คลิก
    middle_progress: float = 0.0
    pinch_progress: float = 0.0
    flipped: bool = False              # พลิกมือ (ยืนยัน confirm_frames แล้ว)
    normal_sign: int = 0               # เครื่องหมาย facing ด้านปกติ (0 = ยังไม่เรียนรู้)
    learning: bool = False             # กำลังเรียนรู้ด้านปกติ (toggle ยังไม่ทำงาน)


class GestureTracker:
    def __init__(self, cfg: GestureConfig) -> None:
        self.cfg = cfg
        self.enabled = False
        self.clutch = False
        self.dragging = False
        self.scrolling = False
        # --- พลิกมือ / toggle ---
        fixed = getattr(cfg, "normal_facing", 0)
        self.normal_sign = fixed if fixed in (-1, 1) else 0  # ตั้งใน config = ไม่ต้องเรียนรู้
        self._learn: deque[tuple[float, int]] = deque()
        self._learn_t0: float | None = None
        self._flip_raw = False
        self._flip_n = 0
        self._flip_start: float | None = None
        self._need_release = False  # หลัง toggle ต้องพลิกกลับก่อน
        self._lost_since: float | None = None  # เวลาที่มือหายไปครั้งล่าสุด
        self._seen = False        # เคยเห็นมือแล้วหรือยัง
        # --- clutch / drag / scroll ---
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
        self._pending: tuple[str, float, float] | None = None  # (event, bend_ts, deadline)
        self._pinch_hist: deque[float] = deque(maxlen=PINCH_MEDIAN_N)
        # คำขอ toggle จากเธรดอื่น (เช่นเมนู tray) — ประมวลผลต้นรอบ update ถัดไป
        self._req_lock = threading.Lock()
        self._req_toggle = False

    @property
    def learning(self) -> bool:
        return self.normal_sign == 0

    def _reset_click(self) -> None:
        self._idx_armed = self._mid_armed = False
        self._idx_n = self._mid_n = 0
        self._idx_bend = self._mid_bend = None
        self._pending = None

    def _end_actions(self, events: list[str]) -> None:
        """ปิด drag/scroll ทันที (มือหาย/ปิดระบบ/พลิกมือ) เพื่อไม่ให้ปุ่มค้าง"""
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
        if self._flip_raw:
            return "flip"
        if self.dragging:
            return "drag"
        if self.clutch:
            return "clutch"
        if self.scrolling:
            return "scroll"
        return "move"

    def _lost(self, ts: float, events: list[str]) -> None:
        self._pinch_hist.clear()
        # _need_release คงไว้ข้ามมือหายสั้น ๆ (dropout 1 เฟรมต้องไม่ทำให้ toggle ซ้ำ)
        if self._lost_since is None:
            self._lost_since = ts
        self._flip_raw = False
        self._flip_n = 0
        self._flip_start = None
        self._fist_n = 0
        self._rel_n = 0
        if self.clutch:
            self.clutch = False
            events.append("clutch_off")
        self._end_actions(events)
        self._reset_click()

    def _learn_sign(self, facing: float, ts: float) -> None:
        """เรียนรู้ด้านปกติ: หน้าต่าง ~2 s ของเฟรมที่ |facing| > 0.4 ต้องเห็นด้านเดียวกัน >= 80%
        (กันกรณีผู้ใช้พลิกมือตั้งแต่เริ่ม) commit แล้วไม่เรียนรู้ใหม่ตลอด session"""
        if self.normal_sign != 0 or abs(facing) <= LEARN_MIN_FACING:
            return
        if self._learn_t0 is None:
            self._learn_t0 = ts
        win = self._learn
        win.append((ts, _sign(facing)))
        while win and ts - win[0][0] > LEARN_WINDOW_S:
            win.popleft()
        n = len(win)
        if n < LEARN_MIN_SAMPLES or ts - self._learn_t0 < LEARN_MIN_SPAN_S:
            return
        pos = sum(1 for _, sg in win if sg > 0)
        if pos / n >= LEARN_AGREE:
            self.normal_sign = 1
        elif (n - pos) / n >= LEARN_AGREE:
            self.normal_sign = -1

    # ---- ควบคุมจากภายนอก (lifecycle / tray) ----
    def _stop_control(self, events: list[str]) -> None:
        """ปิดทุกอย่างที่ค้าง: drag/scroll/clutch และสถานะคลิก (รวมคลิกที่รอยืนยัน)"""
        self._end_actions(events)
        if self.clutch:
            self.clutch = False
            events.append("clutch_off")
        self._fist_n = self._rel_n = 0
        self._reset_click()

    def _set_enabled(self, value: bool, events: list[str]) -> None:
        if value == self.enabled:
            return
        if not value:
            self._stop_control(events)
        self.enabled = value
        events.append("toggle_on" if value else "toggle_off")
        # กำลังพลิกมืออยู่ตอนถูกสลับ → ต้องพลิกกลับก่อน ไม่งั้นจะ toggle ซ้ำทันที
        self._flip_start = None
        if self._flip_raw:
            self._need_release = True

    def disable(self, events: list[str] | None = None) -> list[str]:
        """ปิดการควบคุม (เช่นเข้า low power) เรียกจากเธรดเดียวกับ update; คืน/ต่อ events"""
        events = [] if events is None else events
        if self.enabled:
            self._set_enabled(False, events)
        else:  # ปิดอยู่แล้ว: เก็บกวาดของค้างเฉย ๆ ไม่ส่ง toggle_off
            self._stop_control(events)
        return events

    def request_toggle(self) -> None:
        """thread-safe: ขอสลับ enabled ใช้ที่ต้นรอบ update ถัดไป"""
        with self._req_lock:
            self._req_toggle = True

    def update(self, feats: HandFeatures | None, ts: float) -> GestureState:
        cfg = self.cfg
        events: list[str] = []
        with self._req_lock:
            requested, self._req_toggle = self._req_toggle, False
        if requested:
            self._set_enabled(not self.enabled, events)
        if feats is None:
            self._lost(ts, events)
            return GestureState(self.enabled, False, 0.0, False, False, tuple(events),
                                mode=self._mode(), normal_sign=self.normal_sign,
                                learning=self.learning)

        if self._lost_since is not None:
            gone = ts - self._lost_since
            if gone >= LOST_RELEASE_S:  # หายนานพอ ถือว่าพลิกกลับแล้ว
                self._need_release = False
            self._lost_since = None
        self._seen = True

        facing = getattr(feats, "palm_facing", 0.0)
        self._learn_sign(facing, ts)

        c = feats.curl
        four = [c[n] for n in FOUR]
        fist_raw = is_fist(c, cfg)

        # --- พลิกมือค้าง → toggle (ทำงานเมื่อเรียนรู้ด้านปกติแล้วเท่านั้น) ---
        flip_raw = is_flip(facing, self.normal_sign, cfg)
        self._flip_raw = flip_raw
        self._flip_n = self._flip_n + 1 if flip_raw else 0
        flipped = self._flip_n >= cfg.confirm_frames
        progress = 0.0
        if not flip_raw:
            self._flip_start = None
            self._need_release = False
        elif flipped and not self._need_release:
            if self._flip_start is None or ts < self._flip_start:  # เวลาย้อนหลัง: เริ่มนับใหม่
                self._flip_start = ts
            progress = _clamp01((ts - self._flip_start) * 1000.0 / cfg.toggle_hold_ms)
            if progress >= 1.0:
                self.enabled = not self.enabled
                events.append("toggle_on" if self.enabled else "toggle_off")
                self._need_release = True
                self._flip_start = None
                progress = 0.0

        idx, mid, ring, pinky = four
        # ระยะจีบกระโดด 1–2 เฟรม (3–7 cm) ระหว่างจีบค้างบ่อย → ใช้ median ของหลายเฟรม
        self._pinch_hist.append(feats.pinch)
        pinch = sorted(self._pinch_hist)[len(self._pinch_hist) // 2]
        span = cfg.click_curl_up - cfg.click_curl_down
        idx_prog = _clamp01((cfg.click_curl_up - idx) / span)
        mid_prog = _clamp01((cfg.click_curl_up - mid) / span)
        pinch_prog = _clamp01((cfg.pinch_up - pinch) / (cfg.pinch_up - cfg.pinch_down))

        click_ts: float | None = None
        if not self.enabled:
            self._fist_n = self._rel_n = 0
            if self.clutch:
                self.clutch = False
                events.append("clutch_off")
            self._end_actions(events)  # toggle off ระหว่างลาก/scroll → ปล่อยทันที
            self._reset_click()
            fist = False
        elif flip_raw:
            # กำลังพลิกข้อมือ: anchor เลื่อนเพราะการหมุน → ตัด drag/scroll, ไม่เริ่มอะไรใหม่, ไม่คลิก
            self._end_actions(events)
            self._fist_n = self._rel_n = 0
            self._reset_click()
            fist = self.clutch
        else:
            self._update_drag(pinch, events)
            self._update_clutch(c, fist_raw, events)
            fist = self.clutch or fist_raw
            self._update_scroll(c, events)
            if self._mode() != "move":
                self._reset_click()
            else:
                # คลิกใช้ระยะดิบ (ชุดเดียวกับ raw_pose): เริ่มจีบต้องยกเลิกคลิกที่รออยู่ทันที
                click_ts = self._update_clicks(c, feats.pinch, ts, events)

        return GestureState(self.enabled, self.clutch, progress, flipped, fist, tuple(events),
                            mode=self._mode(), click_bend_ts=click_ts,
                            index_progress=idx_prog, middle_progress=mid_prog,
                            pinch_progress=pinch_prog, flipped=flipped,
                            normal_sign=self.normal_sign, learning=self.learning)

    def _update_drag(self, pinch: float, events: list[str]) -> None:
        """จีบค้าง = drag (สำคัญสุด); ปล่อยเมื่อ pinch > pinch_up"""
        cfg = self.cfg
        if not self.dragging:
            if self.clutch:
                self._pinch_n = 0
                return
            self._pinch_n = self._pinch_n + 1 if is_pinch(pinch, cfg) else 0
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

    def _update_clutch(self, c: dict[str, float], fist_raw: bool, events: list[str]) -> None:
        """กำหมัด (ทั้ง 4 นิ้ว < curl_down) = clutch (ไม่เริ่มขณะ drag);
        ออกเมื่อชี้และกลางเหยียด (> curl_up) ค้าง confirm_frames (นางกับก้อยจะยังพับอยู่ก็ได้)"""
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
            out = c["index"] > cfg.curl_up and c["middle"] > cfg.curl_up
            self._rel_n = self._rel_n + 1 if out else 0
            if self._rel_n >= cfg.confirm_frames:
                self.clutch = False
                self._fist_n = 0
                events.append("clutch_off")
                self._reset_click()  # ปล่อย clutch แล้วต้อง re-arm

    def _update_scroll(self, c: dict[str, float], events: list[str]) -> None:
        """ท่า scroll: ชี้+กลาง > scroll_extend_min และค่าเฉลี่ยนางกับก้อย < scroll_curl_max"""
        cfg = self.cfg
        if self.dragging or self.clutch:
            self._scroll_n = 0
            return
        pose = is_scroll_pose(c, cfg)
        if not self.scrolling:
            self._scroll_n = self._scroll_n + 1 if pose else 0
            if self._scroll_n >= cfg.confirm_frames:
                self.scrolling = True
                self._scroll_off_n = 0
                events.append("scroll_start")
        else:
            # ระหว่าง scroll นิ้วกลางมักงอตามลงไป 0.6–0.7 → ใช้เกณฑ์ค้างท่าที่หลวมกว่าเกณฑ์เข้า
            hold = is_scroll_hold(c, cfg)
            self._scroll_off_n = 0 if hold else self._scroll_off_n + 1
            if self._scroll_off_n >= cfg.confirm_frames:
                self.scrolling = False
                self._scroll_n = 0
                events.append("scroll_end")
                self._reset_click()

    def _update_clicks(self, c: dict[str, float], pinch: float, ts: float,
                       events: list[str]) -> float | None:
        """คลิกเฉพาะ mode move; คืน click_bend_ts เมื่อเกิดคลิก
        เงื่อนไขท่าอยู่ใน is_click_left / is_click_right (ชุดเดียวกับ raw_pose)"""
        cfg = self.cfg
        idx, mid = c["index"], c["middle"]
        # armed + เวลาที่เริ่มพับ (เกณฑ์คลิกแยกจากเกณฑ์กำมือ: พับนิ้วแค่ ~0.77 ก็พอ ไม่ปวดมือ)
        if idx > cfg.click_curl_up:
            self._idx_armed = True
            self._idx_bend = None
        elif self._idx_armed and self._idx_bend is None:
            self._idx_bend = ts
        if mid > cfg.click_curl_up:
            self._mid_armed = True
            self._mid_bend = None
        elif self._mid_armed and self._mid_bend is None:
            self._mid_bend = ts
        left = is_click_left(c, pinch, cfg)
        right = is_click_right(c, pinch, cfg)
        self._idx_n = self._idx_n + 1 if left else 0
        self._mid_n = self._mid_n + 1 if right else 0

        # คลิกที่ยืนยันแล้วรอ CLICK_GUARD_S ก่อนส่ง: กำมือที่นิ้วชี้นำจะมีนาง/ก้อยพับตามมา
        if self._pending is not None:
            name, bend, deadline = self._pending
            if (_rp_mean(c) < CLICK_RP_GUARD or pinch <= cfg.pinch_down + CLICK_PINCH_MARGIN
                    or ts - deadline > CLICK_STALE_S):
                # กลายเป็นกำมือ, เริ่มจีบ (กำลังจะลาก) หรือเฟรมขาดหาย (กล้องค้าง) → ยกเลิก
                self._pending = None
            elif ts >= deadline - CLICK_GUARD_SLACK_S:
                self._pending = None
                events.append(name)
                return bend
            return None
        if self._idx_armed and self._idx_n >= cfg.confirm_frames:
            bend = self._idx_bend if self._idx_bend is not None else ts
            self._idx_armed = False
            self._idx_n = 0
            self._pending = ("click_left", bend, ts + CLICK_GUARD_S)
        elif self._mid_armed and self._mid_n >= cfg.confirm_frames:
            bend = self._mid_bend if self._mid_bend is not None else ts
            self._mid_armed = False
            self._mid_n = 0
            self._pending = ("click_right", bend, ts + CLICK_GUARD_S)
        return None
