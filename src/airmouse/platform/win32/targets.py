"""หาปุ่มที่คลิกได้ใกล้ cursor เพื่อให้ snap (แม่เหล็ก/หนืด) ใช้

thread แยก "targets" ทำงาน ~12 Hz เฉพาะตอน active:
1) ปุ่ม caption (ย่อ/ขยาย/ปิด) จาก DwmGetWindowAttribute — เร็ว ไม่ต้องพึ่ง UI Automation
2) UI Automation ผ่าน comtypes: ElementFromPoint ที่ cursor + 8 จุดบนวงรัศมี search_px
พิกัดทั้งหมดเป็นพิกเซลจริง (โปรเซส per-monitor DPI aware จาก system.init())
"""
from __future__ import annotations

import ctypes
import logging
import math
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from .mouse import RateLimitedLog

try:
    # import ที่ระดับ module (thread ที่ import) เพราะ comtypes จะ CoInitialize thread ที่ import
    # ครั้งแรกเองเป็น STA ถ้า import ใน worker thread จะชนกับ MTA ที่ตั้งตอนเริ่ม thread
    import comtypes
    import comtypes.client as _cc
except Exception:  # noqa: BLE001
    comtypes = None  # type: ignore[assignment]
    _cc = None  # type: ignore[assignment]

log = logging.getLogger(__name__)
_uia_err_log = RateLimitedLog(log)

STALE_S = 0.5  # targets เก่ากว่านี้ถือว่าไม่มี
UIA_TIMEOUT_MS = 200
UIA_FAIL_LIMIT = 5  # query UIA ล้มเหลวทั้งหมดติดกันกี่ครั้งถึงเลิกใช้ UIA
RING_POINTS = 8
MAX_ANCESTORS = 3
MIN_SIZE = 6.0
MAX_W = 400.0
MAX_H = 120.0
REUSE_PX = 5.0  # cursor ขยับน้อยกว่านี้ภายใน REUSE_S -> ไม่ query ซ้ำ
REUSE_S = 0.35

# control type id (ตรวจกับ typelib ของ UIAutomationCore แล้ว) -> kind
CONTROL_KINDS: dict[int, str] = {
    50000: "button",
    50002: "checkbox",
    50003: "combobox",
    50005: "link",
    50007: "listitem",
    50011: "menuitem",
    50013: "radio",
    50019: "tab",
    50024: "treeitem",
    50031: "splitbutton",
}
# property ids (UIA_*PropertyId)
_PROP_BOUNDS = 30001
_PROP_PID = 30002
_PROP_CTYPE = 30003
_PROP_NAME = 30005
_PROP_ENABLED = 30010
_PROP_OFFSCREEN = 30022


@dataclass(frozen=True)
class Target:
    left: float
    top: float
    right: float
    bottom: float
    kind: str
    name: str = ""

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.right) / 2, (self.top + self.bottom) / 2

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return (self.left - margin <= x <= self.right + margin
                and self.top - margin <= y <= self.bottom + margin)

    def distance_to(self, x: float, y: float) -> float:
        """ระยะจากจุดถึงกรอบ (0 ถ้าอยู่ข้างใน)"""
        dx = max(self.left - x, 0.0, x - self.right)
        dy = max(self.top - y, 0.0, y - self.bottom)
        return math.hypot(dx, dy)


# ---------------- pure helpers ----------------
def split_caption_buttons(left: float, top: float, right: float, bottom: float) -> list[Target]:
    """แบ่งกรอบปุ่ม caption (พิกัดจอ) เป็น ย่อ/ขยาย/ปิด ด้วยการหารสาม (ซ้าย->ขวา)
    กรอบที่กว้างหรือสูงน้อยเกินไป (แอปที่วาด caption เอง DWM จะให้ความกว้าง 0) คืน []"""
    w, h = right - left, bottom - top
    if w < 3 * MIN_SIZE or h < MIN_SIZE:
        return []
    third = w / 3
    kinds = ("caption_min", "caption_max", "caption_close")
    names = ("Minimize", "Maximize", "Close")
    return [Target(left + i * third, top, left + (i + 1) * third, bottom, k, n)
            for i, (k, n) in enumerate(zip(kinds, names))]


def keep_element(control_type: int, w: float, h: float, enabled: bool, offscreen: bool,
                 pid: int, own_pid: int) -> bool:
    """กรอง UIA element: ต้องเป็น control ที่คลิกได้ เปิดใช้งาน อยู่บนจอ ไม่ใช่ของเราเอง
    และขนาดพอดี (ไม่เล็กจนเป็นจุด ไม่ใหญ่จนเป็น pane)"""
    return (control_type in CONTROL_KINDS and enabled and not offscreen and pid != own_pid
            and MIN_SIZE <= w <= MAX_W and MIN_SIZE <= h <= MAX_H)


def dedupe(targets: list[Target]) -> list[Target]:
    """ตัดรายการที่กรอบ (ปัดเป็นพิกเซล) ซ้ำกัน เก็บอันแรกและรักษาลำดับ"""
    seen: set[tuple[int, int, int, int]] = set()
    out: list[Target] = []
    for t in targets:
        key = (round(t.left), round(t.top), round(t.right), round(t.bottom))
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def ring_points(x: float, y: float, radius: float, n: int = RING_POINTS) -> list[tuple[int, int]]:
    """จุดค้นหา: ตัว cursor เอง + n จุดบนวงรัศมี"""
    pts = [(round(x), round(y))]
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((round(x + radius * math.cos(a)), round(y + radius * math.sin(a))))
    return pts


# ---------------- Win32 (caption buttons) ----------------
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = wintypes.LONG
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_dwm.DwmGetWindowAttribute.restype = ctypes.c_long

GA_ROOT = 2
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
DWMWA_CAPTION_BUTTON_BOUNDS = 5


def caption_targets_at(x: int, y: int, own_pid: int, search_px: float) -> list[Target]:
    """ปุ่ม caption ของหน้าต่างใต้ (x, y) ถ้าอยู่ในระยะ 2*search_px"""
    hwnd = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return []
    root = _user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    if pid.value == own_pid:
        return []
    if (_user32.GetWindowLongW(root, GWL_STYLE) & WS_CAPTION) != WS_CAPTION:
        return []
    cb = wintypes.RECT()
    if _dwm.DwmGetWindowAttribute(root, DWMWA_CAPTION_BUTTON_BOUNDS,
                                  ctypes.byref(cb), ctypes.sizeof(cb)) != 0:
        return []
    wr = wintypes.RECT()
    if not _user32.GetWindowRect(root, ctypes.byref(wr)):
        return []
    # ตรวจบนเครื่องจริง: bounds สัมพัทธ์กับมุมซ้ายบนของ GetWindowRect (รวมขอบ resize ที่มองไม่เห็น)
    # ขอบขวาของปุ่มปิด + wr.left ตรงกับ DWMWA_EXTENDED_FRAME_BOUNDS.right พอดี
    ts = split_caption_buttons(cb.left + wr.left, cb.top + wr.top,
                               cb.right + wr.left, cb.bottom + wr.top)
    return [t for t in ts if t.distance_to(x, y) <= search_px * 2]


# ---------------- UI Automation ----------------
class _Uia:
    """ห่อ comtypes/UIA (สร้างและใช้ใน thread เดียวกับที่ CoInitialize)"""

    def __init__(self) -> None:
        cc = _cc
        mod = cc.GetModule("UIAutomationCore.dll")
        try:
            self.uia = cc.CreateObject(mod.CUIAutomation8, interface=mod.IUIAutomation2)
            self.uia.ConnectionTimeout = UIA_TIMEOUT_MS
            self.uia.TransactionTimeout = UIA_TIMEOUT_MS
        except Exception:  # noqa: BLE001
            log.info("ไม่มี IUIAutomation2 ใช้ IUIAutomation ธรรมดา (ไม่มี timeout)")
            self.uia = cc.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
        for cid, name in ((50000, "Button"), (50011, "MenuItem"), (50031, "SplitButton")):
            got = getattr(mod, f"UIA_{name}ControlTypeId", cid)
            if got != cid:
                log.warning("control type id %s ไม่ตรง typelib (%s != %s)", name, cid, got)
        self.cache = self.uia.CreateCacheRequest()
        for pid in (_PROP_BOUNDS, _PROP_PID, _PROP_CTYPE, _PROP_NAME, _PROP_ENABLED, _PROP_OFFSCREEN):
            self.cache.AddProperty(pid)
        self.walker = self.uia.ControlViewWalker

    def target_at(self, x: int, y: int, own_pid: int,
                  memo: dict | None = None) -> Target | None:
        """memo: cache ภายใน query เดียว (rect+type ของ element แรก -> ผลลัพธ์) กันไต่ parent ซ้ำ
        เมื่อหลายจุดบนวงไปตกที่ element เดียวกัน (การไต่ parent คือส่วนที่แพงที่สุด)"""
        el = self.uia.ElementFromPointBuildCache(wintypes.POINT(x, y), self.cache)
        if not el:
            return None
        r0 = el.CachedBoundingRectangle
        key = (r0.left, r0.top, r0.right, r0.bottom, el.CachedControlType)
        if memo is not None and key in memo:
            return memo[key]
        res = self._climb(el, own_pid)
        if memo is not None:
            memo[key] = res
        return res

    def _climb(self, el, own_pid: int) -> Target | None:  # noqa: ANN001
        for _ in range(MAX_ANCESTORS + 1):
            if not el:
                return None
            ctype = el.CachedControlType
            r = el.CachedBoundingRectangle
            w, h = r.right - r.left, r.bottom - r.top
            if ctype in CONTROL_KINDS:
                if keep_element(ctype, w, h, bool(el.CachedIsEnabled),
                                bool(el.CachedIsOffscreen), el.CachedProcessId, own_pid):
                    return Target(r.left, r.top, r.right, r.bottom,
                                  CONTROL_KINDS[ctype], el.CachedName or "")
                return None  # control ที่คลิกได้ตัวแรกไม่ผ่านเงื่อนไข: หยุด
            if w > MAX_W or h > MAX_H:
                return None  # parent ย่อมใหญ่กว่านี้ ไม่ต้องไต่ต่อ
            el = self.walker.GetParentElementBuildCache(el, self.cache)
        return None


class TargetFinder(threading.Thread):
    def __init__(self, search_px: float = 48.0, use_uia: bool = True, hz: float = 12.0) -> None:
        super().__init__(name="targets", daemon=True)
        self._search_px = float(search_px)
        self._use_uia = use_uia
        self._period = 1.0 / hz
        self._lock = threading.Lock()
        self._active_evt = threading.Event()
        self._stop_evt = threading.Event()
        self._probe: tuple[float, float] | None = None
        self._targets: list[Target] = []
        self._stamp = 0.0
        self._q_sum = 0.0
        self._q_max = 0.0
        self._q_n = 0
        self._last_error = ""
        self._own_pid = os.getpid()
        self._last_q: tuple[tuple[float, float] | None, float] = (None, 0.0)

    # ---- API จาก thread อื่น ----
    def set_probe(self, x: float, y: float) -> None:
        with self._lock:
            self._probe = (float(x), float(y))

    def set_active(self, active: bool) -> None:
        if active:
            self._active_evt.set()
        else:
            self._active_evt.clear()
            with self._lock:
                self._targets = []

    @property
    def targets(self) -> list[Target]:
        with self._lock:
            if time.monotonic() - self._stamp > STALE_S:
                return []
            return list(self._targets)

    @property
    def stats(self) -> dict[str, object]:
        """snapshot แล้วรีเซ็ตตัวนับ: {'avg_ms','max_ms','queries','last_error'}"""
        with self._lock:
            n = self._q_n
            out = {"avg_ms": (self._q_sum / n * 1000.0) if n else 0.0,
                   "max_ms": self._q_max * 1000.0, "queries": n, "last_error": self._last_error}
            self._q_sum = self._q_max = 0.0
            self._q_n = 0
        return out

    def stop(self) -> None:
        self._stop_evt.set()
        self._active_evt.set()  # ปลุกจากการรอ

    # ---- ภายใน ----
    def _query(self, uia: _Uia | None, probe: tuple[float, float]) -> tuple[list[Target], bool]:
        """คืน (targets, uia_ล้มเหลวทั้งหมดไหม)"""
        found: list[Target] = []
        try:
            found += caption_targets_at(round(probe[0]), round(probe[1]),
                                        self._own_pid, self._search_px)
        except Exception as e:  # noqa: BLE001
            self._set_error(f"caption: {e}")
        uia_failed = False
        if uia is not None:
            fails = 0
            pts = ring_points(probe[0], probe[1], self._search_px)
            memo: dict = {}
            for px, py in pts:
                try:
                    t = uia.target_at(px, py, self._own_pid, memo)
                except Exception as e:  # noqa: BLE001
                    fails += 1
                    self._set_error(f"uia: {e}")
                    continue
                if t is not None:
                    found.append(t)
            uia_failed = fails == len(pts)
        return dedupe(found), uia_failed

    def _is_fresh(self, probe: tuple[float, float], now: float) -> bool:
        """True ถ้า query ล่าสุดทำที่ตำแหน่งใกล้กัน (< REUSE_PX) และไม่เกิน REUSE_S วินาที
        ในกรณีนี้ต่ออายุ stamp ของผลเดิมแทนการ query ใหม่"""
        lp, lt = self._last_q
        if lp is None or now - lt > REUSE_S:
            return False
        if math.hypot(probe[0] - lp[0], probe[1] - lp[1]) >= REUSE_PX:
            return False
        with self._lock:
            if self._targets or self._stamp:
                self._stamp = time.monotonic()
        return True

    def _set_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = msg
        _uia_err_log.warning("TargetFinder: %s", msg)

    def run(self) -> None:
        com_ok = False
        uia: _Uia | None = None
        try:
            if self._use_uia:
                try:
                    if comtypes is None:
                        raise ImportError("comtypes ไม่พร้อมใช้งาน")
                    # MTA: thread นี้ไม่มี message pump ซึ่ง STA ต้องการ และ UIA client ทำงานใน MTA ได้
                    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
                    com_ok = True
                    uia = _Uia()
                except Exception as e:  # noqa: BLE001
                    log.warning("ใช้ UI Automation ไม่ได้ (%s) -> ใช้เฉพาะปุ่ม caption", e)
                    uia = None
            fail_streak = 0
            while not self._stop_evt.is_set():
                if not self._active_evt.wait(0.2):
                    continue
                if self._stop_evt.is_set():
                    break
                t0 = time.perf_counter()
                with self._lock:
                    probe = self._probe
                if probe is not None and self._is_fresh(probe, t0):
                    pass  # cursor แทบไม่ขยับ: ใช้ผลเดิม ต่ออายุให้ไม่ stale (ลด CPU)
                elif probe is not None:
                    try:
                        result, failed = self._query(uia, probe)
                    except Exception as e:  # noqa: BLE001
                        self._set_error(f"query: {e}")
                        result, failed = [], False
                    fail_streak = fail_streak + 1 if failed else 0
                    if uia is not None and fail_streak >= UIA_FAIL_LIMIT:
                        log.warning("UIA ล้มเหลวต่อเนื่อง -> ใช้เฉพาะปุ่ม caption")
                        uia = None
                    dt = time.perf_counter() - t0
                    self._last_q = (probe, t0)
                    with self._lock:
                        if self._active_evt.is_set():  # set_active(False) คั่นกลาง = ทิ้งผล
                            self._targets = result
                            self._stamp = time.monotonic()
                        self._q_sum += dt
                        self._q_max = max(self._q_max, dt)
                        self._q_n += 1
                rest = self._period - (time.perf_counter() - t0)
                if rest > 0:
                    self._stop_evt.wait(rest)
        finally:
            uia = None
            if com_ok:
                try:
                    import gc

                    gc.collect()  # ปล่อย COM pointer ก่อน CoUninitialize
                    comtypes.CoUninitialize()
                except Exception:  # noqa: BLE001
                    log.debug("CoUninitialize ล้มเหลว", exc_info=True)
