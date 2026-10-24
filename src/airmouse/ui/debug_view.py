"""หน้าต่าง debug: ภาพกล้อง + landmark, ค่า curl ทุกนิ้ว, pinch และ FPS"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..config import GestureConfig
from ..features import ANCHOR_IDS, FINGERS, INDEX_TIP, THUMB_TIP

HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)
PANEL_W = 280
PINCH_SCALE_M = 0.12  # ความยาวแถบ pinch เต็มหลอด

BG = QColor(24, 24, 28)
TEXT = QColor(230, 230, 235)
MUTED = QColor(140, 140, 150)
RED = QColor(235, 80, 80)
YELLOW = QColor(240, 200, 60)
GREEN = QColor(80, 210, 120)
BONE = QColor(255, 255, 255, 200)
JOINT = QColor(0, 200, 255)
ANCHOR = QColor(80, 255, 80)


class DebugView(QWidget):
    def __init__(self, inference, gesture: GestureConfig):
        super().__init__()
        self._inference = inference
        self._g = gesture
        self._result = None
        self.setWindowTitle("Air Mouse — debug view (Esc เพื่อออก)")
        self.resize(640 + PANEL_W, 480)
        self.setMinimumSize(480, 320)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(15)

    def _poll(self) -> None:
        r = self._inference.latest()
        if r is not None and (self._result is None or r.seq != self._result.seq):
            self._result = r
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)

    # ---------- วาด ----------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), BG)
        r = self._result
        if r is None:
            p.setPen(TEXT)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "กำลังรอภาพจากกล้อง…")
            return
        img_rect = self._draw_frame(p, r)
        if r.hand is not None:
            self._draw_hand(p, r, img_rect)
        self._draw_panel(p, r, QRectF(self.width() - PANEL_W, 0, PANEL_W, self.height()))

    def _draw_frame(self, p: QPainter, r) -> QRectF:
        h, w = r.frame.shape[:2]
        image = QImage(r.frame.data, w, h, r.frame.strides[0], QImage.Format.Format_BGR888)
        area = QRectF(0, 0, self.width() - PANEL_W, self.height())
        scale = min(area.width() / w, area.height() / h)
        rect = QRectF(0, 0, w * scale, h * scale)
        rect.moveCenter(area.center())
        p.drawImage(rect, image)
        return rect

    def _draw_hand(self, p: QPainter, r, rect: QRectF) -> None:
        pts = [QPointF(rect.x() + x * rect.width(), rect.y() + y * rect.height())
               for x, y, _ in r.hand.landmarks]
        p.setPen(QPen(BONE, 2))
        for a, b in HAND_CONNECTIONS:
            p.drawLine(pts[a], pts[b])
        p.setPen(Qt.PenStyle.NoPen)
        for i, pt in enumerate(pts):
            p.setBrush(QColor(255, 140, 0) if i in (THUMB_TIP, INDEX_TIP) else JOINT)
            p.drawEllipse(pt, 4, 4)
        ax, ay = r.features.anchor
        p.setPen(QPen(ANCHOR, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(rect.x() + ax * rect.width(), rect.y() + ay * rect.height()), 9, 9)
        for i in ANCHOR_IDS:
            p.drawEllipse(pts[i], 6, 6)

    def _draw_panel(self, p: QPainter, r, rect: QRectF) -> None:
        x, y = rect.x() + 16, 24.0
        bar_w = rect.width() - 32

        def text(s: str, color=TEXT, bold=False, size=10):
            nonlocal y
            f = QFont(self.font())
            f.setPointSize(size)
            f.setBold(bold)
            p.setFont(f)
            p.setPen(color)
            p.drawText(QPointF(x, y), s)
            y += size * 2.0

        text("Air Mouse — debug", bold=True, size=11)
        fps_color = GREEN if r.infer_fps >= 25 else (YELLOW if r.infer_fps >= 15 else RED)
        text(f"FPS  infer {r.infer_fps:5.1f}   cam {r.camera_fps:5.1f}", fps_color)
        text(f"inference {r.infer_ms:5.1f} ms", MUTED)
        y += 6

        if r.hand is None:
            text("ไม่เห็นมือ", YELLOW, bold=True)
            return
        f = r.features
        text(f"มือ {r.hand.handedness}  score {r.hand.score:.2f}", bold=True)
        text(f"ขนาดมือ {f.hand_size:.3f}", MUTED)
        y += 4

        text("curl  (1.0 = เหยียด)", MUTED, size=9)
        for name in FINGERS:
            v = f.curl[name]
            color = RED if v < self._g.curl_down else (GREEN if v > self._g.curl_up else YELLOW)
            self._bar(p, x, y - 12, bar_w, v, color,
                      (self._g.curl_down, self._g.curl_up), f"{name:<6} {v:.2f}")
            y += 26
        y += 8

        text("pinch  (โป้ง–ชี้)", MUTED, size=9)
        pv = f.pinch
        color = RED if pv < self._g.pinch_down else (GREEN if pv > self._g.pinch_up else YELLOW)
        self._bar(p, x, y - 12, bar_w, pv / PINCH_SCALE_M, color,
                  (self._g.pinch_down / PINCH_SCALE_M, self._g.pinch_up / PINCH_SCALE_M),
                  f"{pv * 100:.1f} cm")

    def _bar(self, p: QPainter, x: float, y: float, w: float, value: float,
             color: QColor, marks: tuple[float, float], label: str) -> None:
        """แถบค่า 0–1 พร้อมขีดบอก threshold กด/ปล่อย (hysteresis)"""
        h = 18
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(50, 50, 58))
        p.drawRoundedRect(QRectF(x, y, w, h), 4, 4)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(x, y, w * max(0.0, min(1.0, value)), h), 4, 4)
        p.setPen(QPen(TEXT, 1, Qt.PenStyle.DashLine))
        for m in marks:
            mx = x + w * m
            p.drawLine(QPointF(mx, y - 2), QPointF(mx, y + h + 2))
        f = QFont(self.font())
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        # ตัวอักษรขาวมีเงา อ่านได้ทั้งบนแถบสีและพื้นมืด
        for color, dx in ((QColor(0, 0, 0, 200), 1), (TEXT, 0)):
            p.setPen(color)
            p.drawText(QRectF(x + 6 + dx, y + dx, w - 12, h), Qt.AlignmentFlag.AlignVCenter, label)
