"""ควบคุมเมาส์ผ่าน SendInput (ctypes) + MouseThread ที่ interpolate ที่ ~200 Hz

พิกัดทั้งหมดที่รับเข้ามาเป็นพิกเซลของ virtual desktop (อาจติดลบถ้ามีจอซ้ายของจอหลัก)
การตรวจว่า "ผู้ใช้ขยับเมาส์จริง" ใช้ Raw Input (ไม่ใช้ low-level hook เพราะจะเพิ่ม latency
ให้เมาส์ทั้งระบบ): input ที่ SendInput ฉีดเข้าไปมี hDevice เป็น NULL ส่วนเมาส์จริงมี handle
"""
from __future__ import annotations

import collections
import ctypes
import logging
import threading
import time
from collections.abc import Callable
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
SM_CXDOUBLECLK = 36
SM_CYDOUBLECLK = 37

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
# ติดใน dwExtraInfo ของทุก input ที่เราฉีด เพื่อแยกจาก input ของผู้ใช้ใน Raw Input ('ARMS')
INJECT_MAGIC = 0x41524D53

WM_INPUT = 0x00FF
WM_QUIT = 0x0012
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
HWND_MESSAGE = -3

# ช่วง frame_interval ที่ยอมรับ: กล้องในที่มืดอาจเหลือ ~10 fps (0.1 s)
MIN_SEGMENT_S = 0.010
MAX_SEGMENT_S = 0.100
# fallback (ไม่มี Raw Input): ระยะ px ที่เคอร์เซอร์ต่างจากที่เราส่ง และต้องค้างกี่ tick
USER_MOVE_THRESHOLD_PX = 3
USER_MOVE_PERSIST_TICKS = 2
SCREEN_REFRESH_S = 2.0  # refresh virtual screen rect (จอเสียบ/ถอด/เปลี่ยน resolution)
SEND_BACKOFF_S = 0.25  # พัก N วินาทีหลัง SendInput ล้มเหลว (UAC/lock screen/UIPI)
LOG_INTERVAL_S = 5.0


class RateLimitedLog:
    """log.warning ครั้งแรกทันที จากนั้นอย่างมากหนึ่งครั้งต่อ interval พร้อมจำนวนที่ถูกซ่อน"""

    def __init__(self, logger: logging.Logger, interval: float = LOG_INTERVAL_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._logger = logger
        self._interval = interval
        self._clock = clock
        self._lock = threading.Lock()
        self._last: float | None = None
        self._suppressed = 0

    def warning(self, msg: str, *args: object, **kw: object) -> None:
        now = self._clock()
        with self._lock:
            if self._last is not None and now - self._last < self._interval:
                self._suppressed += 1
                return
            n, self._suppressed, self._last = self._suppressed, 0, now
        if n:
            msg += f" (ซ้ำอีก {n} ครั้งที่ไม่ได้แสดง)"
        self._logger.warning(msg, *args, **kw)


_send_fail_log = RateLimitedLog(log)
_cursor_fail_log = RateLimitedLog(log)
_tick_err_log = RateLimitedLog(log)

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


# ---- Raw Input structs (RAWINPUTHEADER = 24 bytes บน x64) ----
class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BTN(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _RAWMOUSE_U(ctypes.Union):
    _anonymous_ = ("s",)
    _fields_ = [("ulButtons", wintypes.ULONG), ("s", _RAWMOUSE_BTN)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _RAWMOUSE_U),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWINPUT(ctypes.Structure):
    """ส่วน header + mouse ของ RAWINPUT (union จริงใหญ่กว่านี้ แต่เราอ่านแค่เมาส์)"""
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


_WNDPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetDoubleClickTime.argtypes = []
_user32.GetDoubleClickTime.restype = wintypes.UINT
_user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_user32.RegisterClassW.restype = wintypes.ATOM
_user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE]
_user32.UnregisterClassW.restype = wintypes.BOOL
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.DestroyWindow.restype = wintypes.BOOL
_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = wintypes.LPARAM
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = wintypes.LPARAM
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE),
                                            wintypes.UINT, wintypes.UINT]
_user32.RegisterRawInputDevices.restype = wintypes.BOOL
_user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                    ctypes.POINTER(wintypes.UINT), wintypes.UINT]
_user32.GetRawInputData.restype = wintypes.UINT
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HANDLE
_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


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

    ใช้ pixel-centre: n = floor((v - origin + 0.5) * 65536 / size) แล้ว clamp 0..65535
    Windows แปลงกลับเป็นพิกเซลด้วย floor(n * size / 65536) จึงตกที่พิกเซลเดิมเสมอ
    (ทดสอบทุกพิกเซลใน tests) — สูตร /(size-1) แบบเดิมคลาดไป 1px ราว 7 จาก 1920 พิกเซล
    """
    def conv(v: float, origin: int, size: int) -> int:
        s = (v - origin + 0.5) * 65536 / max(size, 1)
        if s <= 0:
            return 0
        return min(65535, int(s))

    return conv(x, rect.x, rect.w), conv(y, rect.y, rect.h)


def get_cursor() -> tuple[int, int]:
    pt = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(pt)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(pt.x), int(pt.y)


def _safe_cursor() -> tuple[int, int] | None:
    """get_cursor ที่ไม่ raise (secure desktop/lock screen ทำให้ล้มเหลวได้) + จำกัด log"""
    try:
        return get_cursor()
    except OSError as e:
        _cursor_fail_log.warning("GetCursorPos ล้มเหลว: %s", e)
        return None


def _send(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> bool:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, INJECT_MAGIC)
    n = _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if n != 1:
        # มักเกิดจาก UIPI (หน้าต่าง admin) หรือ secure desktop
        _send_fail_log.warning("SendInput ล้มเหลว (flags=0x%X, err=%d)",
                               flags, ctypes.get_last_error())
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


def double_click_metrics() -> tuple[int, int, int]:
    """(เวลา ms, ความกว้าง px, ความสูง px) ของ double-click ของระบบ; fallback (500, 4, 4)"""
    try:
        t = int(_user32.GetDoubleClickTime())
        w = int(_user32.GetSystemMetrics(SM_CXDOUBLECLK))
        h = int(_user32.GetSystemMetrics(SM_CYDOUBLECLK))
        if t > 0 and w > 0 and h > 0:
            return t, w, h
    except Exception:  # noqa: BLE001
        log.debug("อ่านค่า double-click ไม่ได้ ใช้ค่าเริ่มต้น", exc_info=True)
    return 500, 4, 4


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


# ---------------- Raw Input ----------------
def is_user_mouse_input(buf: bytes) -> bool:
    """แปลง buffer RAWINPUT: True ถ้าเป็น input ของผู้ใช้ที่ขยับ/กดปุ่ม/หมุนล้อ
    ถือว่าเป็นของเราเมื่อ ulExtraInformation == INJECT_MAGIC (ที่ _send ติดไว้) เท่านั้น
    ไม่ดูที่ hDevice เพราะ touchpad แบบ Precision / RDP / Synergy มักมี hDevice = NULL"""
    if len(buf) < ctypes.sizeof(RAWINPUT):
        return False
    ri = RAWINPUT.from_buffer_copy(buf[: ctypes.sizeof(RAWINPUT)])
    if ri.header.dwType != RIM_TYPEMOUSE:
        return False
    mo = ri.mouse
    if mo.ulExtraInformation == INJECT_MAGIC:
        return False
    return bool(mo.lLastX or mo.lLastY or mo.usButtonFlags)


is_physical_mouse_input = is_user_mouse_input  # alias เดิม


class RawInputMonitor(threading.Thread):
    """thread "rawinput": message-only window + WM_INPUT (RIDEV_INPUTSINK) เรียก on_user_input()
    เมื่อเจอ input จากเมาส์จริง ไม่ใช้ hook จึงไม่เพิ่ม latency ให้ระบบ"""

    def __init__(self, on_user_input: Callable[[], None]) -> None:
        super().__init__(name="rawinput", daemon=True)
        self._cb = on_user_input
        self._proc = _WNDPROC(self._wndproc)  # ต้องถือ reference ไว้ ไม่งั้นโดน GC
        self._ready = threading.Event()
        self._tid = 0
        self.ok = False

    def start_and_wait(self, timeout: float = 2.0) -> bool:
        self.start()
        self._ready.wait(timeout)
        return self.ok

    def _wndproc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001
        if msg == WM_INPUT:
            try:
                self._handle(lparam)
            except Exception:  # noqa: BLE001
                _tick_err_log.warning("อ่าน raw input ผิดพลาด", exc_info=True)
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle(self, hraw: int) -> None:
        size = wintypes.UINT(0)
        hs = ctypes.sizeof(RAWINPUTHEADER)
        if _user32.GetRawInputData(hraw, RID_INPUT, None, ctypes.byref(size), hs) != 0:
            return
        buf = ctypes.create_string_buffer(size.value)
        got = _user32.GetRawInputData(hraw, RID_INPUT, buf, ctypes.byref(size), hs)
        if got == size.value and is_user_mouse_input(buf.raw):
            self._cb()

    def run(self) -> None:
        cls = f"AirMouseRawInput{id(self):x}"
        hinst = _kernel32.GetModuleHandleW(None)
        hwnd = None
        registered = False
        try:
            self._tid = _kernel32.GetCurrentThreadId()
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.lpszClassName = cls
            if not _user32.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            hwnd = _user32.CreateWindowExW(0, cls, "airmouse-rawinput", 0, 0, 0, 0, 0,
                                           HWND_MESSAGE, None, hinst, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)  # generic desktop / mouse
            if not _user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.ok = True
        except Exception as e:  # noqa: BLE001
            log.warning("Raw Input ใช้ไม่ได้ (%s) -> ใช้การเทียบตำแหน่งเคอร์เซอร์แทน", e)
        finally:
            self._ready.set()
        try:
            if self.ok:
                msg = wintypes.MSG()
                while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.ok:
                rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_REMOVE, None)
                _user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid))
            if hwnd:
                _user32.DestroyWindow(hwnd)
            if registered:
                _user32.UnregisterClassW(cls, hinst)

    def stop(self) -> None:
        if self.ok and self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self.is_alive() and threading.current_thread() is not self:
            self.join(2.0)


def _clamp_to(p: tuple[int, int], r: Rect) -> tuple[int, int]:
    return (min(max(p[0], r.x), r.right - 1), min(max(p[1], r.y), r.bottom - 1))


class MouseThread(threading.Thread):
    """thread ส่งเมาส์ความถี่สูง: รับ target จาก inference thread แล้ว interpolate ให้เนียน"""

    def __init__(self, hz: float = 200) -> None:
        super().__init__(name="mouse", daemon=True)
        self._period = 1.0 / hz
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._rect = virtual_screen()
        self._next_screen_check = 0.0
        p = _safe_cursor() or (self._rect.x + self._rect.w // 2, self._rect.y + self._rect.h // 2)
        self._active = False
        self._user_moved = False
        self._seg_start: tuple[float, float] = (float(p[0]), float(p[1]))
        self._seg_target = self._seg_start
        self._seg_t0 = time.perf_counter()
        self._seg_dur = 0.0
        self._last_sent: tuple[int, int] | None = p  # ไม่ส่งซ้ำจนกว่า target จะเปลี่ยน
        self._gen = 0  # เพิ่มทุกครั้งที่ resync เพื่อทิ้งผลส่งที่ค้างมาจากก่อน resync
        self._backoff_until = 0.0
        self._cmds: collections.deque[tuple] = collections.deque()
        self._held: set[str] = set()
        # fallback เมื่อ Raw Input ใช้ไม่ได้
        self._fallback = False
        self._recent: collections.deque[tuple[int, int]] = collections.deque(maxlen=3)
        self._dev_ticks = 0
        self._raw: RawInputMonitor | None = None
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
        """ซิงก์ตำแหน่งภายในกับเคอร์เซอร์จริง และล้างธง user_moved
        ตั้ง last_sent = ตำแหน่งนี้ด้วย จึงไม่ส่งอะไรจนกว่า target จะเปลี่ยนจริง
        (กันดึงเคอร์เซอร์กลับถ้าผู้ใช้ยังขยับเมาส์อยู่)"""
        with self._lock:
            # อ่านใน lock กัน race กับ tick; ถ้าอ่านไม่ได้ (secure desktop) ใช้ตำแหน่งภายในเดิม
            p = _safe_cursor() or (round(self._seg_target[0]), round(self._seg_target[1]))
            self._seg_start = self._seg_target = (float(p[0]), float(p[1]))
            self._seg_dur = 0.0
            self._seg_t0 = time.perf_counter()
            self._last_sent = p
            self._recent.clear()
            self._recent.append(p)  # seed ไม่งั้น fallback ตรวจไม่ได้จนกว่าจะส่งครั้งแรก
            self._dev_ticks = 0
            self._gen += 1
            self._user_moved = False
        return float(p[0]), float(p[1])

    @property
    def user_moved(self) -> bool:
        with self._lock:
            return self._user_moved

    @property
    def screen(self) -> Rect:
        """virtual screen rect ปัจจุบัน (refresh ทุก SCREEN_REFRESH_S)"""
        with self._lock:
            return self._rect

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

    # ---- ภายใน ----
    def _on_user_input(self) -> None:
        """เรียกจาก rawinput thread เมื่อมีเมาส์จริงขยับ"""
        with self._lock:
            self._user_moved = True

    def _record(self, dt: float) -> None:
        with self._lock:
            self._ticks += 1
            self._dt_sum += dt
            self._dt_max = max(self._dt_max, dt)
            if dt > 2 * self._period:
                self._late += 1

    def _fallback_user_moved(self, rect: Rect) -> bool:
        """fallback: เคอร์เซอร์ห่างจากตำแหน่งที่เราส่ง 3 ตำแหน่งล่าสุดทั้งหมด > threshold
        ติดกัน USER_MOVE_PERSIST_TICKS tick (ทน latency ของ SendInput ที่เป็น async)
        เทียบกับตำแหน่งที่ clamp เข้า rect แล้ว กันกรณี OS ตรึงเคอร์เซอร์ที่ขอบ
        (ช่องว่างระหว่างจอขนาดต่างกันยังอาจพลาดได้ จึงเป็นแค่ fallback)"""
        with self._lock:
            recent = tuple(self._recent)  # copy ใน lock กัน resync แก้ระหว่างวน
        if not recent:
            return False
        cur = _safe_cursor()
        if cur is None:
            return False
        dev = min(max(abs(cur[0] - q[0]), abs(cur[1] - q[1]))
                  for q in (_clamp_to(s, rect) for s in recent))
        with self._lock:
            self._dev_ticks = self._dev_ticks + 1 if dev > USER_MOVE_THRESHOLD_PX else 0
            return self._dev_ticks >= USER_MOVE_PERSIST_TICKS

    # ---- คิวคำสั่ง (ปุ่ม/scroll/click) : รันบน mouse thread เรียงลำดับเดียวกับการขยับ ----
    def click_at(self, x: float, y: float, btn: str = "left") -> None:
        """กระโดดไป (x, y) ทันที (ไม่ interpolate) แล้ว down+up"""
        self._enqueue(("click", float(x), float(y), self._check_btn(btn)))

    def button_down(self, btn: str) -> None:
        self._enqueue(("down", self._check_btn(btn)))

    def button_up(self, btn: str) -> None:
        self._enqueue(("up", self._check_btn(btn)))

    def scroll(self, delta: int) -> None:
        """delta หน่วยดิบของล้อ (120 = หนึ่งรอยบาก, บวก = ขึ้น); 0 ถูกข้าม"""
        if int(delta) != 0:
            self._enqueue(("scroll", int(delta)))

    @property
    def held_buttons(self) -> frozenset[str]:
        """ปุ่มที่ thread นี้กดค้างอยู่ (เช่นระหว่าง drag)"""
        with self._lock:
            return frozenset(self._held)

    @staticmethod
    def _check_btn(btn: str) -> str:
        if btn not in _BUTTON_FLAGS:
            raise ValueError(f"unknown button {btn!r}")
        return btn

    def _enqueue(self, cmd: tuple) -> None:
        with self._lock:
            self._cmds.append(cmd)

    def _exec_up(self, btn: str) -> None:
        # ปล่อยเฉพาะปุ่มที่เรากดค้างอยู่จริง: up ซ้ำอาจไปยกเลิกปุ่มที่ผู้ใช้กดเมาส์จริงอยู่
        with self._lock:
            held = btn in self._held
        with _pressed_lock:
            held = held or btn in _pressed
        if not held:
            return
        if button_up(btn):
            with self._lock:
                self._held.discard(btn)

    def _run_commands(self) -> None:
        """รันคิวก่อน interpolation ของทุก tick กติกา: button_up รันเสมอ (ห้ามทิ้ง ไม่งั้นปุ่มค้าง)
        ส่วน down/click_at/scroll จะถูกทิ้ง (log debug) ถ้า inactive หรือ user_moved ตอนรัน
        และถ้า user_moved ขณะเรากดปุ่มค้างอยู่ จะปล่อยปุ่มทันที (ผู้ใช้เข้ามาควบคุมแล้ว)"""
        with self._lock:
            cmds = list(self._cmds)
            self._cmds.clear()
        for cmd in cmds:
            kind = cmd[0]
            if kind == "up":
                self._exec_up(cmd[1])
                continue
            with self._lock:
                allowed = self._active and not self._user_moved
                rect = self._rect
            if not allowed:
                log.debug("ทิ้งคำสั่ง %s (inactive/user_moved)", kind)
                continue
            if kind == "down":
                if button_down(cmd[1]):
                    with self._lock:
                        self._held.add(cmd[1])
            elif kind == "scroll":
                scroll(cmd[1])
            elif kind == "click":
                _, x, y, btn = cmd
                r = (round(x), round(y))
                with self._lock:
                    self._seg_start = self._seg_target = (x, y)
                    self._seg_dur = 0.0
                    self._seg_t0 = time.perf_counter()
                    gen = self._gen
                if not move_abs(r[0], r[1], rect):
                    log.debug("click_at: ย้ายไม่สำเร็จ ข้ามการคลิก")
                    continue
                with self._lock:
                    if self._gen == gen:
                        self._last_sent = r
                        self._recent.append(r)
                if button_down(btn):
                    button_up(btn)
        with self._lock:
            held = list(self._held) if self._user_moved else []
        for b in held:
            self._exec_up(b)

    def _drain_on_exit(self) -> None:
        """ตอนปิด thread: รันเฉพาะ button_up ที่ค้างในคิว แล้ว release_all"""
        with self._lock:
            ups = [c for c in self._cmds if c[0] == "up"]
            self._cmds.clear()
        for c in ups:
            self._exec_up(c[1])
        release_all()
        with self._lock:
            self._held.clear()

    def _tick(self, now: float) -> None:
        if now >= self._next_screen_check:
            self._next_screen_check = now + SCREEN_REFRESH_S
            new = virtual_screen()
            if new.w > 0 and new.h > 0:
                with self._lock:
                    self._rect = new
        self._run_commands()
        # คำนวณใน lock แล้วปล่อยก่อนทำ syscall (SendInput/GetCursorPos)
        with self._lock:
            if not self._active or self._user_moved or now < self._backoff_until:
                return
            px, py = interpolate(self._seg_start, self._seg_target,
                                 self._seg_t0, self._seg_dur, now)
            r = (round(px), round(py))
            rect = self._rect
            gen = self._gen
            fallback = self._fallback
            unchanged = r == self._last_sent
        if fallback and self._fallback_user_moved(rect):
            with self._lock:
                if self._gen == gen:
                    self._user_moved = True
            return
        if unchanged:
            return
        ok = move_abs(r[0], r[1], rect)
        with self._lock:
            if self._gen != gen:
                return  # resync คั่นกลาง: ผลนี้ล้าสมัย
            if ok:
                self._last_sent = r
                self._recent.append(r)
            else:
                self._backoff_until = now + SEND_BACKOFF_S

    def run(self) -> None:
        raw = RawInputMonitor(self._on_user_input)
        if raw.start_and_wait():
            self._raw = raw
        else:
            self._fallback = True
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
                    _tick_err_log.warning("mouse tick ผิดพลาด", exc_info=True)
        finally:
            raw.stop()
            self._drain_on_exit()
