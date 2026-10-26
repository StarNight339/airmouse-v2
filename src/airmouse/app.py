"""ประกอบ threads + Qt app และจัดการการเปิด/ปิดโปรแกรม"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import signal
import threading
import time
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


class Controller:
    """ท่ามือ → motion → mouse thread (อยู่ใน inference thread)

    mouse ใช้ duck typing (MouseThread หรือของปลอมในเทสต์): set_target, set_active,
    resync, user_moved, click_at, button_down, button_up, scroll
    """

    def __init__(self, gestures: GestureTracker, motion: Motion, mouse,
                 click_rollback_s: float = 0.15,
                 double_click: tuple[int, int, int] = (500, 4, 4)):
        self.gestures = gestures
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
        self._last_click = (btn, ts, pos)
        log.info("คลิก%s ที่ (%.0f, %.0f)", "ซ้าย" if btn == "left" else "ขวา", *pos)

    def step(self, feats: HandFeatures | None, ts: float, aspect: float
             ) -> tuple[GestureState, tuple[float, float] | None]:
        g = self.gestures.update(feats, ts)
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
        active = (feats is not None and g.enabled and g.mode not in ("clutch", "flip")
                  and not user_busy)
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
                target = self.motion.update(feats.anchor, feats.hand_size, aspect, dt, ts=ts)
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
        self._was_active = active
        return g, target


class PoseRecorder:
    """บันทึก landmark ทั้ง 21 จุดทุกเฟรมเป็น JSONL (--log-pose) ไว้ replay/จูนท่ามือทีหลัง

    บรรทัดละเฟรม: t, aspect, mirror และถ้าเห็นมือ: hand, score, lm (พิกัดภาพ x,y,z),
    world (เมตร) รวมถึง mode/ev ของ GestureTracker; เฟรมที่ไม่เห็นมือมี hand = null
    """

    def __init__(self, path):
        self.path = path
        # line-buffered: ปิดโปรแกรมแบบไม่ปกติ (kill/crash) ข้อมูลยังอยู่ครบถึงเฟรมล่าสุด
        self._fh = open(path, "w", encoding="utf-8", buffering=1)

    def write(self, ts: float, aspect: float, mirror: bool, hand: TrackedHand | None,
              gesture: GestureState | None) -> None:
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
        self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self._fh.close()


class InferenceThread(threading.Thread):
    """รอเฟรมใหม่จากกล้อง → MediaPipe → features แล้วเก็บผลล่าสุดไว้ให้ UI อ่าน"""

    def __init__(self, camera: Camera, tracker: HandTracker, mirror: bool, debug: bool,
                 controller: Controller | None = None, log_pose: bool = False):
        super().__init__(name="inference", daemon=True)
        self._camera = camera
        self._tracker = tracker
        self._controller = controller
        self._mirror = mirror
        self._debug = debug
        self._log_pose = log_pose
        self._lock = threading.Lock()
        self._latest: FrameResult | None = None
        self._stop_event = threading.Event()
        self.error: str | None = None
        self.fps = RateMeter()

    def latest(self) -> FrameResult | None:
        with self._lock:
            return self._latest

    def run(self) -> None:
        seq = 0
        last_ts: float | None = None
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
                gesture = target = None
                speed = gain = 0.0
                if self._controller is not None:
                    gesture, target = self._controller.step(feats, ts, w / h)
                    speed, gain = self._controller.motion.speed, self._controller.motion.gain
                self.fps.tick(ts)
                result = FrameResult(seq, frame, ts, hand, feats, infer_ms,
                                     self._camera.fps.rate, self.fps.rate,
                                     gesture, target, speed, gain)
                with self._lock:
                    self._latest = result
                if recorder is not None:
                    recorder.write(ts, w / h, self._mirror, hand, gesture)
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
    try:
        tracker = HandTracker(model_path, cfg.camera.hand, cfg.camera.mirror,
                              cfg.gesture.min_presence)
        if not camera.wait_opened(CAMERA_OPEN_TIMEOUT_S):
            log.error("เริ่มกล้องไม่สำเร็จ: %s", camera.error or "หมดเวลารอเฟรมแรก")
            return 1

        mouse = win_mouse.MouseThread()
        mouse.start()
        screen = win_mouse.virtual_screen()
        motion = Motion(cfg.motion, screen, px_per_hand=win_mouse.primary_screen().h)
        controller = Controller(GestureTracker(cfg.gesture), motion, mouse,
                                click_rollback_s=cfg.motion.click_rollback_ms / 1000,
                                double_click=win_mouse.double_click_metrics())
        log.info("จอ (virtual desktop) %s | พลิกมือค้าง %d ms เพื่อเปิด/ปิดการควบคุม",
                 screen, cfg.gesture.toggle_hold_ms)

        inference = InferenceThread(camera, tracker, cfg.camera.mirror, args.debug, controller,
                                    log_pose=args.log_pose)
        inference.start()

        app = QApplication.instance() or QApplication([])
        exit_code = 0

        def watchdog() -> None:
            nonlocal exit_code
            if not inference.is_alive():
                log.error("หยุดทำงาน: %s", inference.error or camera.error or "inference thread จบเอง")
                exit_code = 1
                app.quit()

        # Ctrl+C ใน terminal: Qt ไม่คืน control ให้ Python ถ้าไม่มี timer ปลุกเป็นระยะ
        signal.signal(signal.SIGINT, lambda *_: app.quit())
        timer = QTimer()
        timer.timeout.connect(watchdog)
        timer.start(200)

        view = None
        if args.debug_view:
            from .ui.debug_view import DebugView
            view = DebugView(inference, cfg.gesture)
            view.show()
        else:
            log.info("กำลังติดตามมือ (กด Ctrl+C เพื่อออก)")

        app.exec()
        timer.stop()
        return exit_code
    finally:
        if inference is not None:
            inference.stop()
            inference.join(timeout=2)
        if mouse is not None:
            mouse.stop()
            mouse.join(timeout=2)
        win_mouse.release_all()
        camera.stop()
        camera.join(timeout=2)
        if tracker is not None:
            tracker.close()
        system.shutdown()
        log.info("ปิดโปรแกรมเรียบร้อย")
