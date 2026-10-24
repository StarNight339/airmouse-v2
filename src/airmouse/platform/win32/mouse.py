"""ควบคุมเมาส์ผ่าน SendInput (ctypes) + MouseThread ที่ interpolate ที่ ~200 Hz

พิกัดทั้งหมดที่รับเข้ามาเป็นพิกเซลของ virtual desktop (อาจติดลบถ้ามีจอซ้ายของจอหลัก)
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ---- ค่าคงที่ Win32 ----
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

WHEEL_DELTA = 120

# ช่วง frame_interval ที่ยอมรับ: กล้องในที่มืดอาจเหลือ ~10 fps (0.1 s)
MIN_SEGMENT_S = 0.010
MAX_SEGMENT_S = 0.100
# ระยะ (px) ที่เคอร์เซอร์ต่างจากที่ thread ส่งไปล่าสุด ถึงถือว่าผู้ใช้ขยับเมาส์จริง
USER_MOVE_THRESHOLD_PX = 3


# ---- struct (layout ถูกต้องสำหรับ x64: sizeof(INPUT) == 40) ----
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


def virtual_screen() -> Rect:
    """สี่เหลี่ยมที่ครอบทุกจอ (พิกัดอาจติดลบ)"""
    g = _user32.GetSystemMetrics
    return Rect(g(SM_XVIRTUALSCREEN), g(SM_YVIRTUALSCREEN),
                g(SM_CXVIRTUALSCREEN), g(SM_CYVIRTUALSCREEN))


def primary_screen() -> Rect:
    """จอหลัก (เริ่มที่ 0,0) — motion ใช้ความสูงเป็น px_per_hand"""
    g = _user32.GetSystemMetrics
    return Rect(0, 0, g(SM_CXSCREEN), g(SM_CYSCREEN))


def to_absolute(x: float, y: float, rect: Rect) -> tuple[int, int]:
    """พิกเซล virtual desktop -> พิกัด normalized 0..65535 สำหรับ ABSOLUTE|VIRTUALDESK

    ใช้ n = round((x - rect.x) * 65535 / (rect.w - 1)) ไม่ใช่ ... / rect.w:
    พิกเซลสุดท้าย (rect.w-1) ต้องไปที่ 65535 พอดี และพิกเซลแรกที่ 0 ส่วนตอนที่ Windows
    แปลงกลับ (n * w / 65536) แล้วจะตกที่พิกเซลเดิม — ตัวหารแบบ w จะทำให้พิกเซลขอบขวา/ล่างหลุด
    ค่านอกช่วงถูก clamp ที่ 0..65535
    """
    def conv(v: float, origin: int, size: int) -> int:
        span = max(size - 1, 1)
        n = round((v - origin) * 65535 / span)
        return 0 if n < 0 else 65535 if n > 65535 else int(n)

    return conv(x, rect.x, rect.w), conv(y, rect.y, rect.h)


def get_cursor() -> tuple[int, int]:
    pt = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(pt)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(pt.x), int(pt.y)


def _send(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> bool:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    n = _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if n != 1:
        # มักเกิดจาก UIPI: หน้าต่างที่รันแบบ admin บล็อก input จากโปรเซสปกติ
        log.warning("SendInput ล้มเหลว (flags=0x%X, err=%d)", flags, ctypes.get_last_error())
        return False
    return True


def move_abs(x: float, y: float, rect: Rect | None = None) -> bool:
    """ย้ายเคอร์เซอร์ไปพิกัด virtual-desktop (x, y) คืน True ถ้าส่งสำเร็จ"""
    r = rect or virtual_screen()
    nx, ny = to_absolute(x, y, r)
    return _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)


_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}
_pressed: set[str] = set()  # ปุ่มที่ module นี้กดค้างอยู่
_pressed_lock = threading.Lock()


def button_down(btn: str) -> bool:
    down, _ = _BUTTON_FLAGS[btn]
    with _pressed_lock:
        ok = _send(down)
        if ok:
            _pressed.add(btn)
    return ok


def button_up(btn: str) -> bool:
    _, up = _BUTTON_FLAGS[btn]
    with _pressed_lock:
        ok = _send(up)
        if ok:
            _pressed.discard(btn)
    return ok


def click(btn: str = "left") -> None:
    button_down(btn)
    button_up(btn)


def scroll(delta: int) -> bool:
    """เลื่อนล้อ: delta เป็นหน่วยดิบของ mouseData (WHEEL_DELTA=120 ต่อหนึ่งรอยบาก,
    บวก = เลื่อนขึ้น/ออกห่างผู้ใช้, ลบ = เลื่อนลง)"""
    return _send(MOUSEEVENTF_WHEEL, data=int(delta))


def release_all() -> None:
    """ปล่อยเฉพาะปุ่มที่ module นี้กดค้างไว้"""
    with _pressed_lock:
        btns = list(_pressed)
    for b in btns:
        button_up(b)


def interpolate(start: tuple[float, float], target: tuple[float, float],
                t_start: float, duration: float, now: float) -> tuple[float, float]:
    """เส้นตรงจาก start ไป target ตามเวลา; หลังจบ (หรือ duration<=0) ค้างที่ target"""
    if duration <= 0:
        return target
    t = (now - t_start) / duration
    if t >= 1.0:
        return target
    if t <= 0.0:
        return start
    return (start[0] + (target[0] - start[0]) * t,
            start[1] + (target[1] - start[1]) * t)


class MouseThread(threading.Thread):
    """thread ส่งเมาส์ความถี่สูง: รับ target จาก inference thread แล้ว interpolate ให้เนียน"""

    def __init__(self, hz: float = 200) -> None:
        super().__init__(name="mouse", daemon=True)
        self._period = 1.0 / hz
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._rect = virtual_screen()
        p = get_cursor()
        self._active = False
        self._user_moved = False
        self._seg_start: tuple[float, float] = (float(p[0]), float(p[1]))
        self._seg_target = self._seg_start
        self._seg_t0 = time.perf_counter()
        self._seg_dur = 0.0
        self._last_sent: tuple[int, int] | None = None
        # ตำแหน่งจริงของเคอร์เซอร์หลังส่งล่าสุด (OS อาจ clamp ที่ขอบ/ช่องว่างระหว่างจอ)
        self._last_actual: tuple[int, int] = p
        self._sent_since_resync = False
        self._dt_sum = 0.0
        self._dt_max = 0.0
        self._ticks = 0
        self._late = 0

    # ---- API จาก thread อื่น ----
    def set_target(self, x: float, y: float, frame_interval: float) -> None:
        now = time.perf_counter()
        dur = min(max(frame_interval, MIN_SEGMENT_S), MAX_SEGMENT_S)
        with self._lock:
            cur = interpolate(self._seg_start, self._seg_target,
                              self._seg_t0, self._seg_dur, now)
            self._seg_start = cur  # เริ่ม segment ใหม่จากตำแหน่งปัจจุบัน ไม่กระโดด
            self._seg_target = (float(x), float(y))
            self._seg_t0 = now
            self._seg_dur = dur

    def set_active(self, active: bool) -> None:
        with self._lock:
            self._active = bool(active)

    def resync(self) -> tuple[float, float]:
        p = get_cursor()
        with self._lock:
            self._seg_start = self._seg_target = (float(p[0]), float(p[1]))
            self._seg_dur = 0.0
            self._seg_t0 = time.perf_counter()
            self._last_sent = None
            self._last_actual = p
            self._sent_since_resync = False
            self._user_moved = False
        return float(p[0]), float(p[1])

    @property
    def user_moved(self) -> bool:
        with self._lock:
            return self._user_moved

    @property
    def stats(self) -> dict[str, float]:
        """snapshot แล้วรีเซ็ต: {'dt_avg_ms','dt_max_ms','late','ticks'}"""
        with self._lock:
            n = self._ticks
            out = {
                "dt_avg_ms": (self._dt_sum / n * 1000.0) if n else 0.0,
                "dt_max_ms": self._dt_max * 1000.0,
                "late": self._late,
                "ticks": n,
            }
            self._dt_sum = self._dt_max = 0.0
            self._ticks = self._late = 0
        return out

    def stop(self) -> None:
        self._stop_evt.set()

    # ---- loop ----
    def _record(self, dt: float) -> None:
        with self._lock:
            self._ticks += 1
            self._dt_sum += dt
            self._dt_max = max(self._dt_max, dt)
            if dt > 2 * self._period:
                self._late += 1

    def _tick(self, now: float) -> None:
        with self._lock:
            if not self._active or self._user_moved:
                return
            if self._sent_since_resync:
                # ผู้ใช้ขยับเมาส์จริง = เคอร์เซอร์ห่างจาก "ตำแหน่งจริงหลังส่งล่าสุด" > threshold
                # เทียบกับตำแหน่งที่อ่านกลับ (ไม่ใช่ที่ขอส่ง) เพื่อไม่ให้การที่ OS clamp
                # เคอร์เซอร์ที่ขอบจอ/ช่องว่างระหว่างจอ ถูกนับเป็น user move
                cx, cy = get_cursor()
                ax, ay = self._last_actual
                if abs(cx - ax) > USER_MOVE_THRESHOLD_PX or abs(cy - ay) > USER_MOVE_THRESHOLD_PX:
                    self._user_moved = True
                    return
            px, py = interpolate(self._seg_start, self._seg_target,
                                 self._seg_t0, self._seg_dur, now)
            r = (round(px), round(py))
            if r == self._last_sent:
                return
            if move_abs(r[0], r[1], self._rect):
                self._last_sent = r
                self._last_actual = get_cursor()
                self._sent_since_resync = True

    def run(self) -> None:
        period = self._period
        prev = deadline = time.perf_counter()
        try:
            while not self._stop_evt.is_set():
                deadline += period
                now = time.perf_counter()
                remaining = deadline - now
                if remaining > 0:
                    time.sleep(remaining)
                now = time.perf_counter()
                if now - deadline > period:
                    deadline = now  # ช้าเกิน 1 คาบ: ข้ามไปข้างหน้า ไม่ยิงรัว
                self._record(now - prev)
                prev = now
                try:
                    self._tick(now)
                except Exception:  # noqa: BLE001
                    log.exception("mouse tick ผิดพลาด")
        finally:
            release_all()
