"""ประกอบ threads + Qt app และจัดการการเปิด/ปิดโปรแกรม"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import config as config_mod
from . import features
from .camera import Camera
from .features import HandFeatures
from .gestures import GestureState, GestureTracker
from .lifecycle import LifeState, Lifecycle
from .motion import Motion
from .timing import RateMeter
from .tracker import HandTracker, TrackedHand, ensure_model

log = logging.getLogger(__name__)

CAMERA_OPEN_TIMEOUT_S = 10.0
DEFAULT_DT = 1 / 30
USER_GRACE_S = 0.4  # หลังผู้ใช้ขยับเมาส์จริง รอเท่านี้ก่อนให้มือควบคุมต่อ
DRAG_SMOOTH_S = 0.15  # ช่วงเพิ่ม smoothing ตอนเริ่มลาก กันของที่ลากเด้ง
DOUBLE_CLICK_SNAP_PX = 16


@dataclass(frozen=True)
class FrameResult:
    seq: int
    frame: np.ndarray  # BGR (กลับด้านแล้วถ้า mirror) ตรงกับพิกัด landmark
    ts: float
    hand: TrackedHand | None
    features: HandFeatures | None
    infer_ms: float
    camera_fps: float
    infer_fps: float
    gesture: GestureState | None = None
    target: tuple[float, float] | None = None  # ตำแหน่ง cursor เป้าหมาย (None = ไม่ได้ควบคุม)
    speed: float = 0.0
    gain: float = 0.0
    life: LifeState | None = None
    snap: object | None = None  # ปุ่มที่ cursor กำลังเกาะ (Target) หรือ None


class Controller:
    """ท่ามือ → motion → mouse thread (อยู่ใน inference thread)

    mouse ใช้ duck typing (MouseThread หรือของปลอมในเทสต์): set_target, set_active,
    resync, user_moved, click_at, button_down, button_up, scroll
    """

    def __init__(self, gestures: GestureTracker, motion: Motion, mouse,
                 click_rollback_s: float = 0.15,
                 double_click: tuple[int, int, int] = (500, 4, 4),
                 snapper=None, finder=None):
        self.gestures = gestures
        # snap (เกาะปุ่ม): snapper = ตรรกะแม่เหล็ก/หนืด, finder = thread หาปุ่มรอบ cursor
        self.snapper = snapper
        self.finder = finder
        self.hovered = None  # ปุ่มที่ cursor กำลังเกาะ (สำหรับ overlay/debug)
        self._snap_prev: tuple[float, float] | None = None
        self.motion = motion
        self.mouse = mouse
        self._rollback_s = click_rollback_s
        self._dc_s = double_click[0] / 1000
        # รัศมีที่ถือว่าเป็นดับเบิลคลิก: มือสั่นระหว่างสองคลิกเกินค่า Windows (4 px) ได้ง่าย
        self._dc_dx = max(double_click[1], DOUBLE_CLICK_SNAP_PX)
        self._dc_dy = max(double_click[2], DOUBLE_CLICK_SNAP_PX)
        self._was_active = False
        self._need_reset = False
        self._dragging = False  # เรากดปุ่มซ้ายค้างอยู่ (ลาก)
        self._last_click: tuple[str, float, tuple[float, float]] | None = None
        self._last_ts: float | None = None
        self._user_ts = float("-inf")  # เวลาล่าสุดที่เห็นผู้ใช้ใช้เมาส์/ทัชแพดจริง

    def _release_drag(self) -> None:
        if self._dragging:
            self.mouse.button_up("left")
            self._dragging = False
            log.info("ปล่อยการลาก")

    def _click(self, btn: str, bend_ts: float | None, ts: float) -> None:
        # ย้อนไปตำแหน่งก่อนเริ่มพับนิ้ว (การพับนิ้วทำให้มือขยับเล็กน้อย)
        t = ts - self._rollback_s if bend_ts is None else max(bend_ts, ts - self._rollback_s)
        pos = self.motion.history_at(t)
        last = self._last_click
        if (last is not None and last[0] == btn and ts - last[1] <= self._dc_s
                and abs(pos[0] - last[2][0]) <= self._dc_dx
                and abs(pos[1] - last[2][1]) <= self._dc_dy):
            pos = last[2]  # จุดเดียวกับครั้งก่อน → Windows นับเป็นดับเบิลคลิก
        self.mouse.click_at(pos[0], pos[1], btn)
        self.motion.set_position(pos)
        if self._snap_prev is not None:
            # virtual ย้ายมาที่จุดคลิก (ตำแหน่งที่แสดง) แล้ว → ทิ้ง offset ไม่งั้น cursor ดีดกลับ
            self._snap_prev = pos
            self.snapper.clear_offset()
        self._last_click = (btn, ts, pos)
        log.info("คลิก%s ที่ (%.0f, %.0f)", "ซ้าย" if btn == "left" else "ขวา", *pos)

    def step(self, feats: HandFeatures | None, ts: float, aspect: float
             ) -> tuple[GestureState, tuple[float, float] | None]:
        g = self.gestures.update(feats, ts, aspect)
        dt = DEFAULT_DT if self._last_ts is None else ts - self._last_ts
        self._last_ts = ts
        # จอถูกเสียบ/ถอดหรือเปลี่ยนความละเอียดได้ระหว่างใช้งาน
        screen = getattr(self.mouse, "screen", None)
        if screen is not None:
            self.motion.bounds = screen
        if self.mouse.user_moved:
            self._user_ts = ts
            self.mouse.resync()
        # ผู้ใช้เพิ่งใช้เมาส์จริง: พักการควบคุมด้วยมือสักครู่ ไม่แย่งกัน
        user_busy = ts - self._user_ts < USER_GRACE_S
        # flip: กำลังพลิกมือ (เปิด/ปิด) → cursor ต้องไม่ตามการหมุนข้อมือ; พลิกกลับแล้ว
        # _was_active เป็น False จึง reset จากตำแหน่ง cursor ไม่กระโดด
        # switcher: มือใช้เลื่อนการ์ด cursor นิ่งอยู่ (ปิด switcher แล้วเริ่มจากตำแหน่ง cursor จริง)
        active = (feats is not None and g.enabled
                  and g.mode not in ("clutch", "flip", "switcher") and not user_busy)
        events = set(g.events)
        target = None
        if not active or "drag_end" in events:
            # มือหาย, ปิดการควบคุม, ผู้ใช้จับเมาส์ หรือปล่อยจีบ → ห้ามมีปุ่มค้าง
            self._release_drag()
        if active:
            # ตั้ง active ก่อนใส่คำสั่ง ไม่งั้น mouse thread อาจทิ้งคลิกของเฟรมแรกที่ active
            self.mouse.set_active(True)
            if "scroll_start" in events:
                self.motion.reset_scroll()
            if g.mode == "scroll":
                # scroll: cursor อยู่กับที่ ขยับมือขึ้นลงเป็น wheel
                delta = self.motion.scroll_delta(feats.anchor[1], feats.hand_size, dt)
                if delta:
                    self.mouse.scroll(delta)
                self._need_reset = True
            else:
                # เริ่มควบคุมใหม่ (toggle, ปล่อย clutch, มือกลับมา, ผู้ใช้เลิกใช้เมาส์,
                # จบ scroll) → ตั้งต้นจากตำแหน่ง cursor ปัจจุบัน ไม่ให้กระโดด
                if not self._was_active or self._need_reset:
                    self.motion.reset(self.mouse.resync())
                    self._need_reset = False
                    self._snap_prev = None
                    if self.snapper is not None:
                        self.snapper.reset()  # offset เก่าห้ามติดมากับตำแหน่งตั้งต้นใหม่
                target = self.motion.update(feats.anchor, feats.hand_size, aspect, dt, ts=ts)
                target = self._snap(g.mode, target, dt)
                self.mouse.set_target(target[0], target[1], dt)
                if "drag_start" in events and not self._dragging:
                    self.motion.boost_smoothing(DRAG_SMOOTH_S)
                    self.mouse.button_down("left")
                    self._dragging = True
                    log.info("เริ่มลาก")
                if "click_left" in events:
                    self._click("left", g.click_bend_ts, ts)
                if "click_right" in events:
                    self._click("right", g.click_bend_ts, ts)
        if not active:
            self.mouse.set_active(False)  # หลังใส่ button_up แล้ว (up รันเสมอแม้ inactive)
        if target is None:
            self._snap_off()
        self._was_active = active
        return g, target

    def _snap(self, mode: str, target: tuple[float, float], dt: float) -> tuple[float, float]:
        """เกาะปุ่มเฉพาะโหมด move; ไม่ทำตอนลาก (ต้องลากได้อิสระ)"""
        if self.snapper is None or mode != "move" or not self.snapper.cfg.enabled:
            if self.snapper is not None and self._snap_prev is not None:
                # เลิก snap (เริ่มลาก/ปิดเมนู) ตอน cursor ยังถูกดึงอยู่: ฝัง offset เข้า motion
                # ครั้งเดียว ไม่งั้น cursor กระโดดกลับไปที่ virtual (ลากเริ่มนอกปุ่ม)
                ox, oy = self.snapper.offset
                if ox or oy:
                    target = (target[0] + ox, target[1] + oy)
                    self.motion.set_position(target)
            self._snap_off()
            return target
        targets = self.finder.targets if self.finder is not None else []
        prev = self._snap_prev if self._snap_prev is not None else target
        virtual, out, self.hovered = self.snapper.adjust(prev, target, self.motion.speed,
                                                         dt, targets)
        # ป้อนกลับเข้า motion แค่ virtual (หนืด) ห้ามป้อน out ไม่งั้นแรงดูดสะสมจนค้างที่ปุ่ม
        if virtual != target:
            self.motion.set_position(virtual)
        if out != virtual:
            self.motion.set_shown(out)
        self._snap_prev = virtual
        if self.finder is not None:
            # หาปุ่มรอบ virtual: ตำแหน่งเดียวกับที่ snapper ใช้เลือกปุ่ม
            self.finder.set_probe(*virtual)
            self.finder.set_active(True)
        return out

    @property
    def snap_virtual(self) -> tuple[float, float] | None:
        """ตำแหน่ง virtual (ตามมือ ไม่รวมแรงดูด) ของเฟรมล่าสุด; None ถ้าไม่ได้ snap"""
        return self._snap_prev

    def _snap_off(self) -> None:
        if self.hovered is not None or self._snap_prev is not None:
            if self.snapper is not None:
                self.snapper.reset()
            self.hovered = None
            self._snap_prev = None
        if self.finder is not None:
            self.finder.set_active(False)


def _cursor_record(controller: Controller | None, target: tuple[float, float] | None,
                   speed: float) -> dict | None:
    """ข้อมูล cursor/snap ของเฟรมนี้สำหรับ pose log (ไว้จูน snap จากข้อมูลจริง)"""
    if controller is None or target is None:
        return None
    rec: dict = {"cur": [round(target[0], 1), round(target[1], 1)], "spd": round(speed, 3)}
    if controller.snap_virtual is not None:
        rec["virt"] = [round(v, 1) for v in controller.snap_virtual]
    hov = controller.hovered
    if hov is not None:
        rec["snap"] = [hov.left, hov.top, hov.right, hov.bottom]
    return rec


class PoseRecorder:
    """บันทึก landmark ทั้ง 21 จุดทุกเฟรมเป็น JSONL (--log-pose) ไว้ replay/จูนท่ามือทีหลัง

    บรรทัดละเฟรม: t, aspect, mirror และถ้าเห็นมือ: hand, score, lm (พิกัดภาพ x,y,z),
    world (เมตร) รวมถึง mode/ev ของ GestureTracker; เฟรมที่ไม่เห็นมือมี hand = null
    เฟรมที่คุม cursor: cur (ตำแหน่งที่แสดง), spd (ความเร็วมือ hand-size/s) และตอน snap:
    virt (ตำแหน่ง virtual), snap (กรอบปุ่มที่เกาะ left, top, right, bottom)
    เฟรมที่ switcher เปิด: sw (switcher_dx หน่วยการ์ด)
    """

    def __init__(self, path):
        self.path = path
        # line-buffered: ปิดโปรแกรมแบบไม่ปกติ (kill/crash) ข้อมูลยังอยู่ครบถึงเฟรมล่าสุด
        self._fh = open(path, "w", encoding="utf-8", buffering=1)

    def write(self, ts: float, aspect: float, mirror: bool, hand: TrackedHand | None,
              gesture: GestureState | None, cursor: dict | None = None) -> None:
        rec: dict = {"t": round(ts, 4), "aspect": round(aspect, 4), "mirror": mirror,
                     "hand": None}
        if hand is not None:
            rec.update(
                hand=hand.handedness, score=round(hand.score, 3),
                lm=[[round(v, 4) for v in p] for p in hand.landmarks],
                world=[[round(v, 5) for v in p] for p in hand.world],
            )
        if gesture is not None:
            rec.update(mode=gesture.mode, ev=list(gesture.events))
        if cursor:
            rec.update(cursor)
        self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self._fh.close()


class InferenceThread(threading.Thread):
    """รอเฟรมใหม่จากกล้อง → MediaPipe → features แล้วเก็บผลล่าสุดไว้ให้ UI อ่าน"""

    def __init__(self, camera: Camera, tracker: HandTracker, mirror: bool, debug: bool,
                 controller: Controller | None = None, log_pose: bool = False,
                 lifecycle: Lifecycle | None = None, low_power_fps: float = 6.0):
        super().__init__(name="inference", daemon=True)
        self._camera = camera
        self._tracker = tracker
        self._controller = controller
        self._mirror = mirror
        self._debug = debug
        self._log_pose = log_pose
        self._lifecycle = lifecycle
        self._low_power_dt = 1.0 / max(low_power_fps, 0.5)
        self._events: deque[str] = deque(maxlen=200)  # event ให้ main thread (เสียง/tray)
        # event ของ switcher พร้อม switcher_dx ของเฟรมเดียวกัน (ย่อ/เลือกต้องใช้ dx เฟรมนั้น)
        self._sw_events: deque[tuple[str, float]] = deque(maxlen=50)
        self._lock = threading.Lock()
        self._latest: FrameResult | None = None
        self._stop_event = threading.Event()
        self._paused = threading.Event()  # set = ไม่ส่งท่ามือเข้า Controller (ยังคำนวณ feats ให้ UI)
        self.error: str | None = None
        self.fps = RateMeter()

    def latest(self) -> FrameResult | None:
        with self._lock:
            return self._latest

    def drain_events(self) -> list[str]:
        """event ที่เกิดตั้งแต่เรียกครั้งก่อน (gesture + lifecycle) สำหรับ Qt main thread"""
        out = []
        while self._events:
            out.append(self._events.popleft())
        return out

    def set_paused(self, paused: bool) -> None:
        """thread-safe: พักการควบคุมด้วยมือ (ระหว่าง calibrate) ผล latest ยังมี feats ตามปกติ"""
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def drain_switcher_events(self) -> list[tuple[str, float]]:
        """event switcher_* พร้อม switcher_dx ของเฟรมที่เกิด สำหรับ Qt main thread"""
        out = []
        while self._sw_events:
            out.append(self._sw_events.popleft())
        return out

    def _emit(self, events, dx: float = 0.0) -> None:  # noqa: ANN001
        self._events.extend(events)
        for ev in events:
            if ev.startswith("switcher_"):
                self._sw_events.append((ev, dx))

    def run(self) -> None:
        seq = 0
        last_ts: float | None = None
        low_power = False
        was_paused = False
        last_processed = float("-inf")
        stats = _DebugStats(self._controller) if self._debug else None
        recorder = None
        if self._log_pose:
            config_mod.LOGS_DIR.mkdir(exist_ok=True)
            recorder = PoseRecorder(
                config_mod.LOGS_DIR / f"airmouse-{datetime.now():%Y%m%d-%H%M%S}-pose.jsonl")
            log.info("บันทึกตำแหน่งนิ้วทุกเฟรม: %s", recorder.path)
        try:
            while not self._stop_event.is_set():
                if self._camera.error:
                    raise RuntimeError(f"กล้อง: {self._camera.error}")
                got = self._camera.wait_frame(seq, timeout=0.5)
                if got is None:
                    continue
                seq, frame, ts = got
                # ประหยัดพลังงาน: ข้ามเฟรมให้ inference เหลือ ~low_power_fps (ยังตรวจหามืออยู่)
                if low_power and ts - last_processed < self._low_power_dt:
                    continue
                last_processed = ts
                if self._mirror:
                    frame = cv2.flip(frame, 1)
                t0 = time.perf_counter()
                hand = self._tracker.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), ts)
                feats = None
                h, w = frame.shape[:2]
                if hand is not None:
                    feats = features.compute(hand.landmarks, hand.world, w / h,
                                             hand.handedness, self._mirror)
                infer_ms = (time.perf_counter() - t0) * 1000
                gesture = target = life = None
                speed = gain = 0.0
                paused = self._paused.is_set()
                if self._controller is not None and paused and not was_paused:
                    # เริ่มพัก (เช่น calibrate): ปิดการควบคุม + ปล่อยปุ่ม/ลากที่ค้างครั้งเดียว
                    self._emit(self._controller.gestures.disable())
                    self._controller.step(None, ts, w / h)
                was_paused = paused
                if self._controller is not None and not paused:
                    gesture, target = self._controller.step(feats, ts, w / h)
                    speed, gain = self._controller.motion.speed, self._controller.motion.gain
                    self._emit(gesture.events, gesture.switcher_dx)
                if self._lifecycle is not None:
                    life = self._lifecycle.update(hand is not None, ts)
                    low_power = life.state == "low_power"
                    if "low_power" in life.events and self._controller is not None:
                        # กลับมาจากประหยัดพลังงานต้องพลิกมือเปิดใหม่ (PLAN ข้อ 5)
                        self._emit(self._controller.gestures.disable())
                        log.info("ไม่เห็นมือ %.0f วิ → ประหยัดพลังงาน", life.idle_s)
                    if "exit" in life.events:
                        log.info("ไม่เห็นมือ %.1f นาที → ปิดโปรแกรม", life.idle_s / 60)
                        if self._controller is not None:  # ระหว่างรอปิด ห้ามเปิดควบคุมใหม่
                            # ส่งต่อเฉพาะ switcher_cancel (ปิด UI) ไม่เล่นเสียง toggle_off ซ้อนเสียงปิด
                            self._emit([e for e in self._controller.gestures.disable()
                                        if e.startswith("switcher_")])
                    self._events.extend(life.events)
                self.fps.tick(ts)
                snap = self._controller.hovered if self._controller is not None else None
                result = FrameResult(seq, frame, ts, hand, feats, infer_ms,
                                     self._camera.fps.rate, self.fps.rate,
                                     gesture, target, speed, gain, life, snap)
                with self._lock:
                    self._latest = result
                if recorder is not None:
                    extra = _cursor_record(self._controller, target, speed)
                    if gesture is not None and gesture.mode == "switcher":
                        extra = {"sw": round(gesture.switcher_dx, 3)}  # ไว้จูน switcher_step
                    recorder.write(ts, w / h, self._mirror, hand, gesture, extra)
                    if feats is not None:
                        log.debug("pose t=%.3f %s", ts, _describe_pose(feats, gesture))
                if stats is not None:
                    stats.add(ts, None if last_ts is None else ts - last_ts, infer_ms, result)
                last_ts = ts
        except Exception as exc:
            log.exception("inference thread error")
            self.error = str(exc)
        finally:
            if recorder is not None:
                recorder.close()

    def stop(self) -> None:
        self._stop_event.set()


class _DebugStats:
    """สรุป FPS, dt และเวลา inference ลง log ทุก 1 วินาที (--debug)"""

    def __init__(self, controller: Controller | None = None):
        self._controller = controller
        self._reset(time.monotonic())

    def _reset(self, t: float) -> None:
        self._start = t
        self._dts: list[float] = []
        self._infer: list[float] = []

    def add(self, ts: float, dt: float | None, infer_ms: float, r: FrameResult) -> None:
        if dt is not None:
            self._dts.append(dt * 1000)
        self._infer.append(infer_ms)
        if ts - self._start < 1.0:
            return
        if self._dts:
            mouse = ""
            if self._controller is not None:
                ms = self._controller.mouse.stats
                mouse = " | mouse dt avg=%.1f max=%.1f ms late=%d" % (
                    ms["dt_avg_ms"], ms["dt_max_ms"], ms["late"])
                finder = self._controller.finder
                if finder is not None:
                    fs = finder.stats
                    if fs["queries"]:
                        mouse += " | snap q=%d avg=%.1f max=%.1f ms" % (
                            fs["queries"], fs["avg_ms"], fs["max_ms"])
            g = r.gesture
            state = "-" if g is None else g.mode
            log.debug(
                "fps cam=%.1f infer=%.1f | dt avg=%.1f max=%.1f ms | infer avg=%.1f max=%.1f ms"
                " | hand=%s state=%s%s",
                r.camera_fps, r.infer_fps,
                sum(self._dts) / len(self._dts), max(self._dts),
                sum(self._infer) / len(self._infer), max(self._infer),
                "yes" if r.hand else "no", state, mouse,
            )
            if r.features is not None:
                log.debug("hand %s", _describe_pose(r.features, r.gesture))
        self._reset(ts)


def _describe_pose(f: HandFeatures, g: GestureState | None) -> str:
    """ค่าของมือในบรรทัดเดียว (key=value) ไว้จูน threshold และ replay จาก log"""
    curls = " ".join(f"{k[:3]}={v:.2f}" for k, v in f.curl.items())
    out = "%s pinch=%.1fcm facing=%+.2f ax=%.3f ay=%.3f hs=%.3f" % (
        curls, f.pinch * 100, f.palm_facing, f.anchor[0], f.anchor[1], f.hand_size)
    if g is not None:
        out += " mode=%s ns=%+d flip=%.2f" % (g.mode, getattr(g, "normal_sign", 0),
                                               g.toggle_progress)
        if g.events:
            out += " ev=" + ",".join(g.events)
    return out


def _setup_logging(debug: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers[0].setLevel(logging.INFO)
    if debug:
        config_mod.LOGS_DIR.mkdir(exist_ok=True)
        path = config_mod.LOGS_DIR / f"airmouse-{datetime.now():%Y%m%d-%H%M%S}.log"
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        handlers.append(fh)
        print(f"debug log: {path}", flush=True)
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # comtypes log ทุก AddRef/Release ที่ DEBUG (หลายพันบรรทัด/นาที) → ปิดไว้
    logging.getLogger("comtypes").setLevel(logging.WARNING)


EXIT_NOTIFY_MS = 2500  # แสดงแจ้งเตือน "ปิดแล้ว" ก่อนออกจริง


SWITCHER_STALE_S = 1.0  # switcher เปิดอยู่แต่ไม่มีผลเฟรมใหม่นานเท่านี้ (กล้องค้าง) → ปิดเอง


class _SwitcherUI:
    """ฝั่ง Qt ของ Swipe Switcher: รายการหน้าต่าง + Carousel + SwitcherWindow (main thread เท่านั้น)

    gestures บอกแค่ event กับ switcher_dx; ที่นี่ตัดสินว่าการ์ดไหนถูกเลือก/ย่อ แล้วสั่ง Windows
    """

    def __init__(self, preview: bool, win32=None, window=None) -> None:  # noqa: ANN001
        # win32/window ส่งของปลอมได้ในเทสต์ (ห้ามเปิดหน้าต่าง/สลับ foreground จริง)
        from .switcher_model import Carousel

        if win32 is None:
            from .platform.win32 import windows as win32
        if window is None:
            from .ui.switcher import SwitcherWindow
            window = SwitcherWindow(preview=preview)
        self._win32 = win32
        self._carousel_cls = Carousel
        self._window = window
        self._carousel = None
        self._wins: list = []
        self._last_t: float | None = None
        self._seq: int | None = None
        self._seq_t = 0.0

    @property
    def is_open(self) -> bool:
        return self._carousel is not None

    def handle(self, ev: str, dx: float, now: float) -> None:
        if ev == "switcher_open":
            self._open(now)
            return
        if self._carousel is None:
            return
        # ใช้ dx ของเฟรมที่เกิด event และ target_index (ไม่ใช่ตำแหน่งที่ยัง smooth อยู่)
        self._carousel.update(dx, 0.0)
        idx = self._carousel.target_index
        if ev == "switcher_minimize":
            hwnd = self._wins[idx].hwnd
            self._win32.minimize(hwnd)
            del self._wins[idx]
            self._carousel.remove(idx)
            self._window.animate_remove(idx)
            log.info("switcher: ย่อ %s", hwnd)
            if not self._wins:
                self.close()
        elif ev == "switcher_commit":
            info = self._wins[idx]
            self.close()  # ปิด thumbnail ก่อนสลับ
            log.info("switcher: สลับไป %s (%s)", info.title, info.exe)
            # activate อาจรอ (Alt + ตรวจผล ~30 ms) → ไม่ทำบน UI thread
            threading.Thread(target=self._win32.activate, args=(info.hwnd,),
                             name="activate", daemon=True).start()
        elif ev == "switcher_cancel":
            self.close()

    def _open(self, now: float) -> None:
        self.close()
        try:
            wins = self._win32.list_switch_windows(os.getpid())
        except Exception:  # noqa: BLE001
            log.exception("switcher: อ่านรายการหน้าต่างไม่ได้")
            return
        if len(wins) < 2:
            log.info("switcher: มีหน้าต่างให้สลับ %d บาน ไม่เปิด", len(wins))
            return
        self._wins = wins
        self._carousel = self._carousel_cls(len(wins))
        self._last_t = now
        self._seq_t = now
        self._window.open(wins, self._carousel.position)
        self._window.set_position(self._carousel.position, self._carousel.selected)
        log.info("switcher: เปิด %d หน้าต่าง", len(wins))

    def tick(self, dx: float, seq: int | None, now: float) -> None:
        if self._carousel is None:
            return
        if seq != self._seq:
            self._seq, self._seq_t = seq, now
        elif now - self._seq_t > SWITCHER_STALE_S:
            log.warning("switcher: ไม่มีเฟรมใหม่ %.1f วิ → ปิด", now - self._seq_t)
            self.close()
            return
        dt = now - (self._last_t if self._last_t is not None else now)
        self._last_t = now
        self._carousel.update(dx, dt)
        self._window.set_position(self._carousel.position, self._carousel.selected)

    def close(self) -> None:
        if self._carousel is not None or self._window.is_open:
            self._window.close_switcher()
        self._carousel = None
        self._wins = []


class _AppUI:
    """ส่วน Qt main thread: tray, overlay, เสียง, หน้าต่าง debug, auto-exit

    อ่านผลล่าสุดและ event จาก InferenceThread ด้วย QTimer (ไม่แตะ Qt จาก thread อื่น)
    """

    def __init__(self, app: QApplication, cfg: config_mod.Config, args: argparse.Namespace,
                 inference: InferenceThread, gestures: GestureTracker):
        # gestures ใช้แค่ request_toggle (thread-safe); snap สลับผ่าน cfg.snap.enabled (bool)
        from .sound import Sounds
        from .ui.cursor_overlay import CursorOverlay
        from .ui.tray import Tray

        self._app = app
        self._cfg = cfg
        self._args = args
        self._inference = inference
        self._gestures = gestures
        self.exit_code = 0
        self._exiting = False
        self._status = ""
        self._view = None
        self._sounds = Sounds(cfg.app.sounds)
        self._overlay = CursorOverlay() if cfg.app.overlay else None
        self._switcher = _SwitcherUI(cfg.switcher.preview) if cfg.switcher.enabled else None

        self._tray = Tray()
        self._tray.toggle_requested.connect(gestures.request_toggle)
        self._tray.debug_requested.connect(self._show_debug)
        self._tray.open_config_requested.connect(self._open_config)
        self._tray.quit_requested.connect(app.quit)
        self._tray.calibrate_requested.connect(self._show_calibrate)
        self._tray.practice_requested.connect(self._show_practice)
        self._calibrate = None
        self._practice = None
        self._tray.set_snap_checked(cfg.snap.enabled)
        self._tray.snap_toggled.connect(self._set_snap)
        self._tray.set_status("Air Mouse: ปิดการควบคุม (พลิกมือค้างเพื่อเปิด)", False)
        self._tray.show()

        if args.debug_view:
            self._show_debug()
            # เปิดมาพร้อม --debug-view: ปิดหน้าต่าง = ออกจากโปรแกรม (เหมือนเดิม)
            self._view.closed.connect(app.quit)

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        if args.calibrate:
            self._show_calibrate()
        elif args.practice:
            self._show_practice()
        elif not getattr(getattr(cfg, "meta", None), "calibrated", "x"):
            # ยังไม่เคย calibrate: เกณฑ์ใน config มาจากมือคนอื่น อาจใช้ได้ไม่ดี
            self._tray.notify("Air Mouse", "ยังไม่ได้ calibrate ท่ามือ: คลิกขวาที่ไอคอน → Calibrate…",
                              6000)

    def _show_calibrate(self) -> None:
        if self._calibrate is None:
            from .ui.calibrate import CalibrateWindow
            self._inference.set_paused(True)  # ทำท่าตามขั้นตอนต้องไม่คลิก/ลากของจริง
            self._calibrate = CalibrateWindow(self._inference, self._cfg, self._args.config)
            self._calibrate.saved.connect(
                lambda values: log.info("calibrate: บันทึก %d ค่าลง %s", len(values),
                                        self._args.config))
            self._calibrate.closed.connect(self._calibrate_closed)
        self._calibrate.show()
        self._calibrate.raise_()
        self._calibrate.activateWindow()

    def _calibrate_closed(self) -> None:
        self._calibrate = None
        self._inference.set_paused(False)  # ต้องพลิกมือเปิดการควบคุมใหม่ (disable ไปแล้ว)

    def _show_practice(self) -> None:
        if self._practice is None:
            from .ui.practice import PracticeWindow
            self._practice = PracticeWindow(config_mod.LOGS_DIR)
            self._practice.closed.connect(self._practice_closed)
        self._practice.showFullScreen()
        self._practice.raise_()
        self._practice.activateWindow()

    def _practice_closed(self) -> None:
        self._practice = None

    def _show_debug(self) -> None:
        if self._view is None:
            from .ui.debug_view import DebugView
            self._view = DebugView(self._inference, self._cfg.gesture)
        self._view.show()
        self._view.raise_()
        self._view.activateWindow()

    def _set_snap(self, on: bool) -> None:
        self._cfg.snap.enabled = on  # Snapper อ่าน cfg เดียวกันทุกเฟรม
        log.info("Snap เกาะปุ่ม: %s", "เปิด" if on else "ปิด")

    def _open_config(self) -> None:
        try:
            os.startfile(self._args.config)  # type: ignore[attr-defined]  # Windows เท่านั้น
        except OSError:
            log.exception("เปิด config ไม่ได้")

    def _tick(self) -> None:
        for ev in self._inference.drain_events():
            self._sounds.play(ev)
            if ev == "exit" and not self._exiting:
                self._exiting = True
                self._tray.notify("Air Mouse ปิดแล้ว",
                                  f"ไม่เห็นมือครบ {self._cfg.app.auto_exit_minutes:g} นาที")
                QTimer.singleShot(EXIT_NOTIFY_MS, self._app.quit)
        r = self._inference.latest()
        g = r.gesture if r is not None else None
        sw_open = False
        if self._switcher is not None:
            now = time.monotonic()
            for ev, dx in self._inference.drain_switcher_events():
                self._switcher.handle(ev, dx, now)
            self._switcher.tick(g.switcher_dx if g is not None else 0.0,
                                r.seq if r is not None else None, now)
            sw_open = self._switcher.is_open
        self._update_status(r, g)
        if self._overlay is not None:
            from .ui.cursor_overlay import overlay_progress
            # switcher เปิด: ซ่อนวง cursor (PLAN ข้อ 6)
            visible = (g is not None and r.hand is not None and not self._exiting
                       and not sw_open)
            self._overlay.set_state(g.mode if g else "off",
                                    overlay_progress(g) if g else 0.0, visible,
                                    snapped=r is not None and r.snap is not None)

    def _update_status(self, r, g) -> None:  # noqa: ANN001
        if g is None:
            return
        life = r.life.state if r.life is not None else "active"
        if life == "low_power":
            text = "Air Mouse: ประหยัดพลังงาน (ไม่เห็นมือ)"
        elif g.enabled:
            text = "Air Mouse: กำลังควบคุม"
        else:
            text = "Air Mouse: ปิดการควบคุม (พลิกมือค้างเพื่อเปิด)"
        if text != self._status:
            self._status = text
            self._tray.set_status(text, g.enabled)

    def close(self) -> None:
        self._timer.stop()
        if self._switcher is not None:
            self._switcher.close()  # unregister DWM thumbnail ทั้งหมดก่อนออก
        if self._overlay is not None:
            self._overlay.hide()
        self._tray.hide()


def run(args: argparse.Namespace) -> int:
    _setup_logging(args.debug)
    cfg = config_mod.load(args.config)
    model_path = ensure_model(config_mod.MODELS_DIR)

    from .platform.win32 import mouse as win_mouse
    from .platform.win32 import system

    # ต้องตั้ง DPI awareness ก่อนสร้าง QApplication และปล่อยเมาส์/คืน timer เสมอแม้ crash
    system.init()
    atexit.register(system.shutdown)
    atexit.register(win_mouse.release_all)

    # เปิดกล้องคู่ขนานกับการโหลดโมเดล เพื่อให้เริ่มใช้งานได้เร็ว
    camera = Camera(cfg.camera)
    camera.start()
    tracker = None
    inference = None
    mouse = None
    finder = None
    try:
        tracker = HandTracker(model_path, cfg.camera.hand, cfg.camera.mirror,
                              cfg.gesture.min_presence)
        if not camera.wait_opened(CAMERA_OPEN_TIMEOUT_S):
            log.error("เริ่มกล้องไม่สำเร็จ: %s", camera.error or "หมดเวลารอเฟรมแรก")
            return 1

        mouse = win_mouse.MouseThread()
        mouse.start()
        from .platform.win32.targets import TargetFinder
        from .snap import Snapper
        finder = TargetFinder(search_px=cfg.snap.search_px, use_uia=cfg.snap.uia,
                              capture_px=cfg.snap.capture_px)
        finder.start()
        screen = win_mouse.virtual_screen()
        motion = Motion(cfg.motion, screen, px_per_hand=win_mouse.primary_screen().h)
        gestures = GestureTracker(cfg.gesture, switcher_enabled=cfg.switcher.enabled)
        controller = Controller(gestures, motion, mouse,
                                click_rollback_s=cfg.motion.click_rollback_ms / 1000,
                                double_click=win_mouse.double_click_metrics(),
                                snapper=Snapper(cfg.snap), finder=finder)
        log.info("จอ (virtual desktop) %s | พลิกมือค้าง %d ms เพื่อเปิด/ปิดการควบคุม",
                 screen, cfg.gesture.toggle_hold_ms)

        # ปิดหน้าต่าง terminal = Windows ฆ่าโปรเซสใน ~5 วิ → ปล่อยปุ่มเมาส์ก่อน (ห้ามแตะ Qt)
        # รอ mouse thread จบก่อน (คำสั่ง down ที่ค้างในคิวต้องไม่ถูกส่งหลัง release_all)
        system.install_console_close_handler(
            lambda: (mouse.stop(), mouse.join(0.5), win_mouse.release_all()))

        lifecycle = Lifecycle(cfg.app, stay=args.stay)
        inference = InferenceThread(camera, tracker, cfg.camera.mirror, args.debug, controller,
                                    log_pose=args.log_pose, lifecycle=lifecycle,
                                    low_power_fps=cfg.app.low_power_fps)
        inference.start()

        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)  # อยู่ใน tray; ปิดหน้าต่าง debug ไม่ได้แปลว่าออก
        ui = _AppUI(app, cfg, args, inference, controller.gestures)

        def watchdog() -> None:
            if not inference.is_alive():
                log.error("หยุดทำงาน: %s", inference.error or camera.error or "inference thread จบเอง")
                ui.exit_code = 1
                app.quit()

        # Ctrl+C ใน terminal: Qt ไม่คืน control ให้ Python ถ้าไม่มี timer ปลุกเป็นระยะ
        signal.signal(signal.SIGINT, lambda *_: app.quit())
        timer = QTimer()
        timer.timeout.connect(watchdog)
        timer.start(200)
        log.info("กำลังติดตามมือ (ไอคอนอยู่ใน system tray, กด Ctrl+C หรือเมนู ออก เพื่อปิด)")

        app.exec()
        timer.stop()
        ui.close()
        return ui.exit_code
    finally:
        if inference is not None:
            inference.stop()
            inference.join(timeout=2)
        if mouse is not None:
            mouse.stop()
            mouse.join(timeout=2)
        win_mouse.release_all()
        if finder is not None:
            finder.stop()
            finder.join(timeout=1)
        camera.stop()
        camera.join(timeout=2)
        if tracker is not None:
            tracker.close()
        system.shutdown()
        log.info("ปิดโปรแกรมเรียบร้อย")
