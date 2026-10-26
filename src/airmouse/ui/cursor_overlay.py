"""วงแหวนเล็กๆ ตามเคอร์เซอร์ บอกโหมดปัจจุบัน + ความคืบหน้าของท่า (คลิก/พลิกมือ)

ไม่รับ input เลย (click-through) และไม่แย่งโฟกัส เรียกจาก main thread เท่านั้น
โปรเซสเป็น per-monitor DPI aware: GetCursorPos ให้พิกเซลจริง ส่วน Qt ใช้พิกัด logical
จึงขยับหน้าต่างด้วย SetWindowPos (พิกเซลจริง, SWP_NOSIZE) ส่วนขนาด logical คงที่ ให้ Qt
คูณ DPI ของจอที่หน้าต่างอยู่เอง
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

SIZE_LOGICAL = 44
RING_W = 3.0
POLL_MS = 16

# สีตามโหมด (RGB) — off/flip เป็นเทา
MODE_COLORS: dict[str, QColor] = {
    "move": QColor(60, 200, 90),
    "clutch": QColor(240, 200, 40),
    "drag": QColor(230, 60, 60),
    "scroll": QColor(130, 200, 250),
    "flip": QColor(150, 150, 150),
    "off": QColor(150, 150, 150),
}
DEFAULT_COLOR = QColor(150, 150, 150)

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010


def overlay_progress(state) -> float:  # noqa: ANN001
    """ความคืบหน้า 0..1 ที่ overlay ควรวาด (duck-typed บน GestureState)
    move: ท่าที่ใกล้ถึงเกณฑ์ที่สุด; flip/off: การพลิกมือค้าง; drag: เต็มวง; อื่นๆ: 0"""
    mode = getattr(state, "mode", "off")
    if mode == "move":
        v = max(state.index_progress, state.middle_progress, state.pinch_progress)
    elif mode in ("flip", "off"):
        v = state.toggle_progress
    elif mode == "drag":
        v = 1.0
    else:
        v = 0.0
    return min(1.0, max(0.0, float(v)))


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL


class CursorOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(SIZE_LOGICAL, SIZE_LOGICAL)
        self._mode = "off"
        self._progress = 0.0
        self._snapped = False
        self._want_visible = False
        self._last_pos: tuple[int, int] | None = None
        self._styled = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._follow)

    # ---- API ----
    def set_state(self, mode: str, progress: float, visible: bool, snapped: bool = False) -> None:
        """อัปเดตโหมด/ความคืบหน้า/การแสดง; repaint เฉพาะเมื่อค่าเปลี่ยน
        snapped = cursor กำลังเกาะปุ่ม → วงหนาและทึบขึ้น"""
        progress = min(1.0, max(0.0, float(progress)))
        if snapped != self._snapped:
            self._snapped = snapped
            if visible:
                self.update()
        if visible != self._want_visible:
            self._want_visible = visible
            if visible:
                self._last_pos = None
                self.show()
                self._timer.start()
                self._follow()
            else:
                self._timer.stop()
                self.hide()
        if mode != self._mode or abs(progress - self._progress) > 1e-3:
            self._mode = mode
            self._progress = progress
            if visible:
                self.update()

    # ---- ภายใน ----
    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        if not self._styled:
            self._apply_ex_styles()

    def _hwnd(self) -> int:
        return int(self.winId())

    def _apply_ex_styles(self) -> None:
        if sys.platform != "win32":
            return
        try:
            h = self._hwnd()
            cur = _user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
            new = cur | (WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE
                         | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
            _user32.SetWindowLongPtrW(h, GWL_EXSTYLE, new)
            self._styled = True
        except Exception:  # noqa: BLE001
            log.warning("ตั้ง extended style ของ overlay ไม่สำเร็จ", exc_info=True)

    def _follow(self) -> None:
        """อ่านตำแหน่งเคอร์เซอร์ (พิกเซลจริง) แล้วย้ายหน้าต่างถ้าเปลี่ยน"""
        if sys.platform != "win32" or not self._want_visible:
            return
        from ..platform.win32.mouse import _safe_cursor

        pos = _safe_cursor()
        if pos is None or pos == self._last_pos:
            return
        try:
            h = self._hwnd()
            rc = wintypes.RECT()
            if not _user32.GetWindowRect(h, ctypes.byref(rc)):
                return
            w, hh = rc.right - rc.left, rc.bottom - rc.top  # ขนาดจริงตาม DPI ของจอนี้
            _user32.SetWindowPos(h, HWND_TOPMOST, pos[0] - w // 2, pos[1] - hh // 2, 0, 0,
                                 SWP_NOSIZE | SWP_NOACTIVATE)
            self._last_pos = pos
        except Exception:  # noqa: BLE001
            log.warning("ย้าย overlay ไม่สำเร็จ", exc_info=True)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        color = MODE_COLORS.get(self._mode, DEFAULT_COLOR)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = RING_W + 1
        rect = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        fill = QColor(color)
        fill.setAlpha(110 if self._snapped else 60)
        ring = QColor(color)
        ring.setAlpha(255 if self._snapped else 190)
        p.setBrush(fill)
        p.setPen(QPen(ring, RING_W if self._snapped else RING_W * 0.5))
        p.drawEllipse(rect)
        if self._progress > 0.0:
            arc = QColor(color)
            arc.setAlpha(255)
            p.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(arc, RING_W)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            # เริ่ม 12 นาฬิกา (90°) หมุนตามเข็ม = มุมลบ; หน่วย 1/16 องศา
            p.drawArc(rect, 90 * 16, int(-self._progress * 360 * 16))
        p.end()
