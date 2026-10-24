"""ตั้งค่าระดับโปรเซส: DPI awareness, timer resolution, power throttling, priority

แต่ละขั้นตอนอิสระต่อกัน ล้มเหลวแล้ว log.warning แล้วไปต่อ ไม่ raise
ต้องเรียก init() ก่อนสร้าง QApplication (DPI awareness ตั้งหลังสร้าง window ไม่ได้)
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2
PROCESS_POWER_THROTTLING = 4  # ProcessPowerThrottling (PROCESS_INFORMATION_CLASS)
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 0x4
ABOVE_NORMAL_PRIORITY_CLASS = 0x8000


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.ULONG),
        ("ControlMask", wintypes.ULONG),
        ("StateMask", wintypes.ULONG),
    ]


_lock = threading.Lock()
_initialized = False
_timer_begun = False


def _set_dpi_awareness() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [wintypes.HANDLE]
        fn.restype = wintypes.BOOL
        if fn(wintypes.HANDLE(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            return
        err = ctypes.get_last_error()
        log.warning("SetProcessDpiAwarenessContext ล้มเหลว (err=%d) ลอง shcore", err)
    except AttributeError:
        log.warning("ไม่มี SetProcessDpiAwarenessContext ลอง shcore")
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    fn2 = shcore.SetProcessDpiAwareness
    fn2.argtypes = [ctypes.c_int]
    fn2.restype = ctypes.c_long  # HRESULT
    hr = fn2(PROCESS_PER_MONITOR_DPI_AWARE)
    # E_ACCESSDENIED (0x80070005) = ตั้งไปแล้ว (เช่นจาก manifest) ถือว่าโอเค
    if hr not in (0, ctypes.c_long(0x80070005).value):
        raise OSError(f"SetProcessDpiAwareness hr=0x{hr & 0xFFFFFFFF:08X}")


def _time_begin_period() -> bool:
    winmm = ctypes.WinDLL("winmm")
    winmm.timeBeginPeriod.argtypes = [wintypes.UINT]
    winmm.timeBeginPeriod.restype = wintypes.UINT
    rc = winmm.timeBeginPeriod(1)
    if rc != 0:  # TIMERR_NOERROR = 0
        raise OSError(f"timeBeginPeriod rc={rc}")
    return True


def _disable_power_throttling(control_mask: int = PROCESS_POWER_THROTTLING_EXECUTION_SPEED) -> None:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.argtypes = []
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetProcessInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    k32.SetProcessInformation.restype = wintypes.BOOL
    state = PROCESS_POWER_THROTTLING_STATE(
        PROCESS_POWER_THROTTLING_CURRENT_VERSION,
        control_mask,
        0,  # StateMask=0 + ControlMask=bit -> ปิด throttling ของ bit นั้น
    )
    ok = k32.SetProcessInformation(
        k32.GetCurrentProcess(), PROCESS_POWER_THROTTLING,
        ctypes.byref(state), ctypes.sizeof(state),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _raise_priority() -> None:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.argtypes = []
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.SetPriorityClass.restype = wintypes.BOOL
    if not k32.SetPriorityClass(k32.GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS):
        raise ctypes.WinError(ctypes.get_last_error())


def init() -> None:
    """ตั้งค่าระบบทั้งหมด เรียกซ้ำได้ (ครั้งที่ 2 เป็น no-op) ไม่ raise"""
    global _initialized, _timer_begun
    with _lock:
        if _initialized:
            return
        _initialized = True
        try:
            _set_dpi_awareness()
        except Exception as e:  # noqa: BLE001
            log.warning("ตั้ง DPI awareness ไม่สำเร็จ: %s", e)
        try:
            _timer_begun = _time_begin_period()
        except Exception as e:  # noqa: BLE001
            log.warning("timeBeginPeriod(1) ไม่สำเร็จ: %s", e)
        try:
            _disable_power_throttling()
        except Exception as e:  # noqa: BLE001
            log.warning("ปิด power throttling ไม่สำเร็จ: %s", e)
        try:
            # Win11 ไม่เคารพ timer resolution ของโปรเซสที่ถูกบัง/background; แยกเรียกเพื่อให้
            # Win10 ที่ไม่รู้จัก flag นี้ไม่ทำให้ EXECUTION_SPEED ข้างบนหลุดไปด้วย
            _disable_power_throttling(PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION)
        except Exception as e:  # noqa: BLE001
            log.warning("ตั้ง IGNORE_TIMER_RESOLUTION ไม่สำเร็จ: %s", e)
        try:
            _raise_priority()
        except Exception as e:  # noqa: BLE001
            log.warning("ตั้ง priority ไม่สำเร็จ: %s", e)


def shutdown() -> None:
    """คืน timer resolution (เฉพาะถ้า init เรียก timeBeginPeriod สำเร็จ) เรียกซ้ำได้"""
    global _timer_begun, _initialized
    with _lock:
        if not _timer_begun:
            return
        _timer_begun = False
        _initialized = False
        try:
            winmm = ctypes.WinDLL("winmm")
            winmm.timeEndPeriod.argtypes = [wintypes.UINT]
            winmm.timeEndPeriod.restype = wintypes.UINT
            winmm.timeEndPeriod(1)
        except Exception as e:  # noqa: BLE001
            log.warning("timeEndPeriod ไม่สำเร็จ: %s", e)
