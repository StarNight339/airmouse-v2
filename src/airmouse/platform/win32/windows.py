"""รายการหน้าต่างสำหรับ Swipe Switcher: enumerate (กรองแบบ Alt+Tab), ไอคอน, DWM thumbnail, สลับหน้าต่าง

ไม่ import Qt เลย; พิกัดทั้งหมดเป็นพิกเซลจริง (โปรเซส per-monitor DPI aware จาก system.init())
ทุกการเรียก Win32 ห่อ try/except — หน้าต่างหนึ่งบานพังต้องไม่ทำให้ทั้งรายการพัง
ใช้ WinDLL instance ของตัวเอง (ไม่แชร์ argtypes กับ module อื่น) และประกาศ argtypes/restype ครบ
เพราะ handle เป็น 64 บิต
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ---- ค่าคงที่ ----
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
GW_OWNER = 4
GA_ROOT = 2
DWMWA_CLOAKED = 14
SW_MINIMIZE = 6
SW_RESTORE = 9
WM_GETICON = 0x007F
ICON_SMALL = 0
ICON_BIG = 1
ICON_SMALL2 = 2
SMTO_ABORTIFHUNG = 0x0002
ICON_TIMEOUT_MS = 50
GCLP_HICON = -14
GCLP_HICONSM = -34
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

DWM_TNP_RECTDESTINATION = 0x1
DWM_TNP_OPACITY = 0x4
DWM_TNP_VISIBLE = 0x8
DWM_TNP_SOURCECLIENTAREAONLY = 0x10

# คลาสของ shell ที่ไม่ควรโผล่ใน switcher (ปกติ cloaked/ไม่มี title อยู่แล้ว กันเหนียว)
SHELL_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                           "Windows.UI.Core.CoreWindow"})


@dataclass(frozen=True)
class WinInfo:
    hwnd: int
    title: str
    pid: int
    exe: str        # basename เช่น "chrome.exe" ("" ถ้าไม่ทราบ)
    iconic: bool    # ย่ออยู่


# ---------------- pure ----------------
def is_switch_candidate(visible: bool, owner: int, ex_style: int, cloaked: int, title: str,
                        class_name: str) -> bool:
    """ตัวกรองแบบ Alt+Tab (แบบง่าย): มองเห็น, ไม่มี owner, ไม่ใช่ tool/noactivate window
    (ยกเว้นตั้ง WS_EX_APPWINDOW), ไม่ cloaked (UWP พื้นหลัง/virtual desktop อื่น), มี title,
    ไม่ใช่คลาสของ shell"""
    if not visible or owner:
        return False
    if cloaked:
        return False
    if not title or not title.strip():
        return False
    if class_name in SHELL_CLASSES:
        return False
    app = bool(ex_style & WS_EX_APPWINDOW)
    if (ex_style & WS_EX_TOOLWINDOW) and not app:
        return False
    if (ex_style & WS_EX_NOACTIVATE) and not app:
        return False
    return True


if sys.platform == "win32":
    from .mouse import INPUT  # struct เดียวกับที่ใช้ส่งเมาส์

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _H = ctypes.c_void_p  # handle ทุกชนิด (HWND/HICON/HTHUMBNAIL) เป็น int ล้วน ไม่ได้ None→0 ปนกัน

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, _H, wintypes.LPARAM)

    class _DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
        _fields_ = [
            ("dwFlags", wintypes.DWORD),
            ("rcDestination", wintypes.RECT),
            ("rcSource", wintypes.RECT),
            ("opacity", ctypes.c_ubyte),
            ("fVisible", wintypes.BOOL),
            ("fSourceClientAreaOnly", wintypes.BOOL),
        ]

    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [_H]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [_H]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindowLongW.argtypes = [_H, ctypes.c_int]
    _user32.GetWindowLongW.restype = wintypes.LONG
    _user32.GetWindow.argtypes = [_H, wintypes.UINT]
    _user32.GetWindow.restype = _H
    _user32.GetWindowTextLengthW.argtypes = [_H]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [_H, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetClassNameW.argtypes = [_H, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [_H, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetAncestor.argtypes = [_H, wintypes.UINT]
    _user32.GetAncestor.restype = _H
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = _H
    _user32.SetForegroundWindow.argtypes = [_H]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [_H]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [_H, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.SendMessageTimeoutW.argtypes = [_H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                            wintypes.UINT, wintypes.UINT,
                                            ctypes.POINTER(ctypes.c_size_t)]
    _user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    _user32.GetClassLongPtrW.argtypes = [_H, ctypes.c_int]
    _user32.GetClassLongPtrW.restype = ctypes.c_size_t
    _user32.DestroyIcon.argtypes = [_H]
    _user32.DestroyIcon.restype = wintypes.BOOL

    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = _H
    _kernel32.CloseHandle.argtypes = [_H]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [_H, wintypes.DWORD, wintypes.LPWSTR,
                                                     ctypes.POINTER(wintypes.DWORD)]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    _shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(_H),
                                        ctypes.POINTER(_H), wintypes.UINT]
    _shell32.ExtractIconExW.restype = wintypes.UINT

    _dwm.DwmGetWindowAttribute.argtypes = [_H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    _dwm.DwmRegisterThumbnail.argtypes = [_H, _H, ctypes.POINTER(_H)]
    _dwm.DwmRegisterThumbnail.restype = ctypes.c_long
    _dwm.DwmUnregisterThumbnail.argtypes = [_H]
    _dwm.DwmUnregisterThumbnail.restype = ctypes.c_long
    _dwm.DwmUpdateThumbnailProperties.argtypes = [_H, ctypes.POINTER(_DWM_THUMBNAIL_PROPERTIES)]
    _dwm.DwmUpdateThumbnailProperties.restype = ctypes.c_long
    _dwm.DwmQueryThumbnailSourceSize.argtypes = [_H, ctypes.POINTER(wintypes.SIZE)]
    _dwm.DwmQueryThumbnailSourceSize.restype = ctypes.c_long

    # ไอคอนที่ดึงจากไฟล์ exe (ExtractIconEx) cache ตาม (path, big) — เป็นของ module นี้
    # จำนวนจำกัดเท่าจำนวน exe ที่เคยเจอ จึงไม่ DestroyIcon (ผู้เรียกห้ามทำลาย HICON ที่ get_icon คืน)
    _extracted_icons: dict[tuple[str, bool], int | None] = {}


# ---------------- helpers ภายใน ----------------
def _title(hwnd: int) -> str:
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _cloaked(hwnd: int) -> int:
    v = wintypes.DWORD(0)
    hr = _dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return int(v.value) if hr == 0 else 0


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _exe_path(pid: int) -> str:
    if pid <= 0:
        return ""
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        _kernel32.CloseHandle(h)


# ---------------- API ----------------
def list_switch_windows(own_pid: int) -> list[WinInfo]:
    """หน้าต่างที่สลับได้ เรียงตามลำดับ EnumWindows (z-order = ใช้ล่าสุดก่อน)"""
    if sys.platform != "win32":
        return []
    out: list[WinInfo] = []

    def _cb(hwnd, _lparam):  # noqa: ANN001
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            owner = _user32.GetWindow(hwnd, GW_OWNER) or 0
            ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
            title = _title(hwnd)
            if not is_switch_candidate(True, int(owner), ex, _cloaked(hwnd), title, _class(hwnd)):
                return True
            pid = _pid(hwnd)
            if pid == own_pid:
                return True
            exe = os.path.basename(_exe_path(pid))
            out.append(WinInfo(int(hwnd), title, pid, exe, bool(_user32.IsIconic(hwnd))))
        except Exception:  # noqa: BLE001
            log.debug("อ่านหน้าต่าง %s ไม่สำเร็จ", hwnd, exc_info=True)
        return True

    try:
        cb = _WNDENUMPROC(_cb)  # ต้องถือ reference ไว้ตลอดการ enumerate
        _user32.EnumWindows(cb, 0)
    except Exception:  # noqa: BLE001
        log.warning("EnumWindows ล้มเหลว", exc_info=True)
    return out


def _icon_from_exe(path: str, big: bool) -> int | None:
    key = (path, big)
    if key in _extracted_icons:
        return _extracted_icons[key]
    hicon: int | None = None
    try:
        large, small = _H(), _H()
        if _shell32.ExtractIconExW(path, 0, ctypes.byref(large), ctypes.byref(small), 1):
            want, other = (large, small) if big else (small, large)
            hicon = want.value or other.value or None
            # ทำลายอันที่ไม่ได้ใช้ทันที (อันที่ใช้ cache ไว้ตลอดอายุโปรเซส)
            for h in (large.value, small.value):
                if h and h != hicon:
                    _user32.DestroyIcon(h)
    except Exception:  # noqa: BLE001
        log.debug("ExtractIconEx ล้มเหลว: %s", path, exc_info=True)
    _extracted_icons[key] = hicon
    return hicon


def get_icon(hwnd: int, big: bool = True) -> int | None:
    """HICON ของหน้าต่าง (หรือ None). ลำดับ: WM_GETICON (timeout 50 ms กันแอปค้าง) →
    ไอคอนของ window class → ExtractIconEx จากไฟล์ exe
    ความเป็นเจ้าของ: HICON ที่ได้จาก WM_GETICON/class เป็นของหน้าต่าง/แอปนั้น ส่วนที่ได้จาก
    ExtractIconEx เป็นของ module นี้ (cache ไว้ตามไฟล์) — ผู้เรียก "ห้าม" DestroyIcon ทั้งสองแบบ
    และควร copy เป็นภาพทันที (เช่น QImage.fromHICON) แล้ว cache ภาพแทนการถือ HICON"""
    if sys.platform != "win32":
        return None
    try:
        kinds = (ICON_BIG, ICON_SMALL2) if big else (ICON_SMALL2, ICON_BIG)
        for kind in kinds:
            res = ctypes.c_size_t(0)
            ok = _user32.SendMessageTimeoutW(hwnd, WM_GETICON, kind, 0, SMTO_ABORTIFHUNG,
                                             ICON_TIMEOUT_MS, ctypes.byref(res))
            if ok and res.value:
                return int(res.value)
        for idx in ((GCLP_HICON, GCLP_HICONSM) if big else (GCLP_HICONSM, GCLP_HICON)):
            v = _user32.GetClassLongPtrW(hwnd, idx)
            if v:
                return int(v)
        path = _exe_path(_pid(hwnd))
        if path:
            return _icon_from_exe(path, big)
    except Exception:  # noqa: BLE001
        log.debug("get_icon ล้มเหลว (hwnd=%s)", hwnd, exc_info=True)
    return None


# ---- DWM thumbnail (rect เป็นพิกเซลจริง สัมพันธ์กับ client area ของหน้าต่างปลายทาง) ----
def register_thumbnail(dest_hwnd: int, src_hwnd: int) -> int | None:
    if sys.platform != "win32":
        return None
    try:
        th = _H()
        hr = _dwm.DwmRegisterThumbnail(dest_hwnd, src_hwnd, ctypes.byref(th))
        if hr != 0 or not th.value:
            log.debug("DwmRegisterThumbnail ล้มเหลว hr=0x%08X (src=%s)", hr & 0xFFFFFFFF, src_hwnd)
            return None
        return int(th.value)
    except Exception:  # noqa: BLE001
        log.debug("register_thumbnail ผิดพลาด", exc_info=True)
        return None


def update_thumbnail(thumb: int, rect: tuple[int, int, int, int], opacity: int = 255,
                     visible: bool = True) -> None:
    """rect = (left, top, right, bottom) พิกเซลจริง สัมพันธ์ client area ปลายทาง"""
    if sys.platform != "win32" or not thumb:
        return
    try:
        props = _DWM_THUMBNAIL_PROPERTIES()
        props.dwFlags = (DWM_TNP_RECTDESTINATION | DWM_TNP_OPACITY | DWM_TNP_VISIBLE
                         | DWM_TNP_SOURCECLIENTAREAONLY)
        props.rcDestination = wintypes.RECT(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        props.opacity = max(0, min(255, int(opacity)))
        props.fVisible = bool(visible)
        props.fSourceClientAreaOnly = False
        _dwm.DwmUpdateThumbnailProperties(thumb, ctypes.byref(props))
    except Exception:  # noqa: BLE001
        log.debug("update_thumbnail ผิดพลาด", exc_info=True)


def thumbnail_source_size(thumb: int) -> tuple[int, int] | None:
    if sys.platform != "win32" or not thumb:
        return None
    try:
        sz = wintypes.SIZE()
        if _dwm.DwmQueryThumbnailSourceSize(thumb, ctypes.byref(sz)) != 0:
            return None
        if sz.cx <= 0 or sz.cy <= 0:
            return None
        return int(sz.cx), int(sz.cy)
    except Exception:  # noqa: BLE001
        return None


def unregister_thumbnail(thumb: int) -> None:
    if sys.platform != "win32" or not thumb:
        return
    try:
        _dwm.DwmUnregisterThumbnail(thumb)
    except Exception:  # noqa: BLE001
        log.debug("unregister_thumbnail ผิดพลาด", exc_info=True)


# ---- สลับ/ย่อ ----
def foreground() -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(_user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def _root(hwnd: int) -> int:
    return int(_user32.GetAncestor(hwnd, GA_ROOT) or hwnd or 0)


def _tap_alt() -> None:
    """กด-ปล่อย Alt หนึ่งครั้ง: ทำให้โปรเซสเรา "เพิ่งมี input ล่าสุด" ผ่านกฎ foreground lock
    ของ Windows แล้ว SetForegroundWindow จึงจะสำเร็จ"""
    arr = (INPUT * 2)()
    for i, flags in enumerate((0, KEYEVENTF_KEYUP)):
        arr[i].type = INPUT_KEYBOARD
        arr[i].ki.wVk = VK_MENU
        arr[i].ki.dwFlags = flags
    _user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def _is_fg(hwnd: int) -> bool:
    fg = foreground()
    return bool(fg) and _root(fg) == _root(hwnd)


def activate(hwnd: int) -> bool:
    """ดึงหน้าต่างขึ้นหน้า (restore ถ้าย่ออยู่). คืน True ถ้าได้เป็น foreground จริง"""
    if sys.platform != "win32":
        return False
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        _tap_alt()
        _user32.SetForegroundWindow(hwnd)
        if _is_fg(hwnd):
            return True
        time.sleep(0.03)
        if _is_fg(hwnd):  # บางแอปใช้เวลาเปลี่ยนสถานะเล็กน้อย
            return True
        # fallback: ผูก input queue กับ thread ของ foreground ปัจจุบันชั่วคราว
        fg = foreground()
        fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        cur_tid = _kernel32.GetCurrentThreadId()
        attached = False
        try:
            if fg_tid and fg_tid != cur_tid:
                attached = bool(_user32.AttachThreadInput(cur_tid, fg_tid, True))
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _user32.AttachThreadInput(cur_tid, fg_tid, False)
        if _is_fg(hwnd):
            return True
        log.warning("activate ไม่สำเร็จ (hwnd=%s)", hwnd)
    except Exception:  # noqa: BLE001
        log.warning("activate ผิดพลาด (hwnd=%s)", hwnd, exc_info=True)
    return False


def minimize(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    try:
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
    except Exception:  # noqa: BLE001
        log.warning("minimize ผิดพลาด (hwnd=%s)", hwnd, exc_info=True)
