"""ไอคอนถาดระบบ: สถานะเปิด/ปิด + เมนูควบคุม (เรียกจาก main thread เท่านั้น)"""
from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

log = logging.getLogger(__name__)

COLOR_ON = QColor(60, 200, 90)
COLOR_OFF = QColor(140, 140, 140)
ICON_PX = 64


def make_icon(enabled: bool) -> QIcon:
    """วาดไอคอนเอง: วงกลมสีตามสถานะ (เขียว=เปิด เทา=ปิด) + ตัว "A" """
    pm = QPixmap(ICON_PX, ICON_PX)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(COLOR_ON if enabled else COLOR_OFF)
    p.setPen(QPen(QColor(30, 30, 30), 3))
    p.drawEllipse(QRectF(4, 4, ICON_PX - 8, ICON_PX - 8))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(34)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(0, 0, ICON_PX, ICON_PX), Qt.AlignmentFlag.AlignCenter, "A")
    p.end()
    return QIcon(pm)


class Tray(QSystemTrayIcon):
    toggle_requested = Signal()
    debug_requested = Signal()
    open_config_requested = Signal()
    quit_requested = Signal()
    calibrate_requested = Signal()
    practice_requested = Signal()
    snap_toggled = Signal(bool)  # เมนู "Snap เกาะปุ่ม" ถูกติ๊ก/เอาออก

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._icons = {True: make_icon(True), False: make_icon(False)}
        self.setIcon(self._icons[False])
        self.setToolTip("Air Mouse")

        menu = QMenu()
        self._act_toggle = menu.addAction("เปิด/ปิดการควบคุม")
        self._act_toggle.triggered.connect(self.toggle_requested)
        self._act_snap = menu.addAction("Snap เกาะปุ่ม")
        self._act_snap.setCheckable(True)
        self._act_snap.toggled.connect(self.snap_toggled)
        self._act_debug = menu.addAction("หน้าต่าง debug")
        self._act_debug.triggered.connect(self.debug_requested)
        menu.addSeparator()
        self._act_calibrate: QAction = menu.addAction("Calibrate…")
        self._act_calibrate.triggered.connect(self.calibrate_requested)
        self._act_practice: QAction = menu.addAction("โหมดฝึก")
        self._act_practice.triggered.connect(self.practice_requested)
        menu.addSeparator()
        self._act_config = menu.addAction("เปิด config.toml")
        self._act_config.triggered.connect(self.open_config_requested)
        self._act_quit = menu.addAction("ออก")
        self._act_quit.triggered.connect(self.quit_requested)
        self._menu = menu  # ถือไว้ ไม่งั้นโดน GC
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_requested.emit()

    def set_snap_checked(self, on: bool) -> None:
        """ตั้งสถานะติ๊กเริ่มต้นโดยไม่ยิง snap_toggled"""
        self._act_snap.blockSignals(True)
        self._act_snap.setChecked(on)
        self._act_snap.blockSignals(False)

    def set_status(self, text: str, enabled: bool) -> None:
        self.setToolTip(f"Air Mouse — {text}")
        self.setIcon(self._icons[bool(enabled)])

    def notify(self, title: str, text: str, ms: int = 3000) -> None:
        if QSystemTrayIcon.supportsMessages():
            self.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, ms)
