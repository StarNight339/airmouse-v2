"""Thread อ่านกล้อง: เก็บไว้แค่เฟรมล่าสุดพร้อม timestamp ตอนรับภาพ"""
from __future__ import annotations

import logging
import sys
import threading
import time

import cv2
import numpy as np

from .config import CameraConfig
from .timing import RateMeter

log = logging.getLogger(__name__)

MAX_READ_FAILURES = 30


class CameraError(RuntimeError):
    pass


class Camera(threading.Thread):
    def __init__(self, cfg: CameraConfig):
        super().__init__(name="camera", daemon=True)
        self._cfg = cfg
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._ts = 0.0
        self._seq = 0
        self._stop_event = threading.Event()
        self._opened = threading.Event()
        self.error: str | None = None
        self.fps = RateMeter()
        self.size = (cfg.width, cfg.height)

    def _open(self) -> cv2.VideoCapture:
        # บน Windows ใช้ DirectShow เพราะ MSMF มักเปิดกล้องช้าหลายวินาที
        # (ไม่ขอ MJPG: กล้องเครื่องนี้ไม่สนใจคำขอ แต่ทำให้เปิดช้าขึ้น ~0.7 วิ)
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._cfg.index, backend)
        try:
            if not cap.isOpened():
                raise CameraError(f"เปิดกล้อง index {self._cfg.index} ไม่ได้")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._cfg.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._cfg.height)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            log.info(
                "กล้องตกลงได้ %dx%d @ %.0f fps รูปแบบ %s",
                cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                cap.get(cv2.CAP_PROP_FPS),
                "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).strip("\x00 ") or "?",
            )
        except BaseException:
            cap.release()
            raise
        return cap

    def _publish(self, frame: np.ndarray, ts: float) -> None:
        with self._cond:
            if self._seq == 0:
                self.size = (frame.shape[1], frame.shape[0])
                log.info("กล้องพร้อม %dx%d", *self.size)
                self._opened.set()
            self._frame, self._ts = frame, ts
            self._seq += 1
            self._cond.notify_all()
        self.fps.tick(ts)

    def run(self) -> None:
        cap = None
        try:
            cap = self._open()
            failures = 0
            while not self._stop_event.is_set():
                if not cap.grab():
                    failures += 1
                    if failures >= MAX_READ_FAILURES:
                        raise CameraError("อ่านภาพจากกล้องไม่ได้ (กล้องถูกถอดหรือโปรแกรมอื่นใช้อยู่?)")
                    time.sleep(0.01)
                    continue
                ts = time.monotonic()  # เวลาที่ได้ภาพ ก่อน decode
                ok, frame = cap.retrieve()
                if not ok:
                    continue
                failures = 0
                self._publish(frame, ts)
        except Exception as exc:
            log.exception("camera thread error")
            self.error = str(exc)
        finally:
            if cap is not None:
                cap.release()
            with self._cond:
                self._opened.set()  # ปลดคนที่รอ wait_opened() ทั้งกรณีสำเร็จและล้มเหลว
                self._cond.notify_all()

    def wait_opened(self, timeout: float) -> bool:
        """รอจนได้เฟรมแรก คืน False ถ้าหมดเวลาหรือเปิดไม่ได้ (ดู .error)"""
        return self._opened.wait(timeout) and self.error is None

    def wait_frame(self, last_seq: int, timeout: float) -> tuple[int, np.ndarray, float] | None:
        """รอเฟรมที่ใหม่กว่า last_seq คืน (seq, frame, timestamp) หรือ None ถ้าไม่มีเฟรมใหม่"""
        with self._cond:
            self._cond.wait_for(
                lambda: self._seq != last_seq or self._stop_event.is_set() or self.error,
                timeout,
            )
            if self._seq == last_seq or self._frame is None:
                return None
            return self._seq, self._frame, self._ts

    def stop(self) -> None:
        self._stop_event.set()
        with self._cond:
            self._cond.notify_all()
