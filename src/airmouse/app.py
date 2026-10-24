"""ประกอบ threads + Qt app และจัดการการเปิด/ปิดโปรแกรม"""
from __future__ import annotations

import argparse
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
from .timing import RateMeter
from .tracker import HandTracker, TrackedHand, ensure_model

log = logging.getLogger(__name__)

CAMERA_OPEN_TIMEOUT_S = 10.0


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


class InferenceThread(threading.Thread):
    """รอเฟรมใหม่จากกล้อง → MediaPipe → features แล้วเก็บผลล่าสุดไว้ให้ UI อ่าน"""

    def __init__(self, camera: Camera, tracker: HandTracker, mirror: bool, debug: bool):
        super().__init__(name="inference", daemon=True)
        self._camera = camera
        self._tracker = tracker
        self._mirror = mirror
        self._debug = debug
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
        stats = _DebugStats() if self._debug else None
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
                if hand is not None:
                    h, w = frame.shape[:2]
                    feats = features.compute(hand.landmarks, hand.world, w / h)
                infer_ms = (time.perf_counter() - t0) * 1000
                self.fps.tick(ts)
                result = FrameResult(seq, frame, ts, hand, feats, infer_ms,
                                     self._camera.fps.rate, self.fps.rate)
                with self._lock:
                    self._latest = result
                if stats is not None:
                    stats.add(ts, None if last_ts is None else ts - last_ts, infer_ms, result)
                last_ts = ts
        except Exception as exc:
            log.exception("inference thread error")
            self.error = str(exc)

    def stop(self) -> None:
        self._stop_event.set()


class _DebugStats:
    """สรุป FPS, dt และเวลา inference ลง log ทุก 1 วินาที (--debug)"""

    def __init__(self):
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
            log.debug(
                "fps cam=%.1f infer=%.1f | dt avg=%.1f max=%.1f ms | infer avg=%.1f max=%.1f ms | hand=%s",
                r.camera_fps, r.infer_fps,
                sum(self._dts) / len(self._dts), max(self._dts),
                sum(self._infer) / len(self._infer), max(self._infer),
                "yes" if r.hand else "no",
            )
        self._reset(ts)


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

    # เปิดกล้องคู่ขนานกับการโหลดโมเดล เพื่อให้เริ่มใช้งานได้เร็ว
    camera = Camera(cfg.camera)
    camera.start()
    tracker = None
    inference = None
    try:
        tracker = HandTracker(model_path, cfg.camera.hand, cfg.camera.mirror,
                              cfg.gesture.min_presence)
        if not camera.wait_opened(CAMERA_OPEN_TIMEOUT_S):
            log.error("เริ่มกล้องไม่สำเร็จ: %s", camera.error or "หมดเวลารอเฟรมแรก")
            return 1

        inference = InferenceThread(camera, tracker, cfg.camera.mirror, args.debug)
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
        camera.stop()
        camera.join(timeout=2)
        if tracker is not None:
            tracker.close()
        log.info("ปิดโปรแกรมเรียบร้อย")
