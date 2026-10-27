"""แถบการ์ดหน้าต่างของ Swipe Switcher (carousel) — วาดอย่างเดียว ไม่มี logic ท่ามือ

เรียกจาก Qt main thread เท่านั้น ไม่แย่งโฟกัสเด็ดขาด (ยกเลิกต้องพึ่งหน้าต่างเดิมยังเป็น foreground)
- พิกัดใน card_layout/paint เป็น logical px ของ Qt; DWM thumbnail ใช้พิกเซลจริง
  โปรเซส per-monitor DPI aware: แปลง logical -> จริงด้วย devicePixelRatioF() ของหน้าต่างนี้
  (สมมติว่าหน้าต่างอยู่บนจอเดียว ซึ่งจริงเพราะเปิดกลางจอหลักจอเดียว)
- DWM thumbnail วาดทับเนื้อหา Qt ของหน้าต่าง จึงวาด chrome ของการ์ด "รอบ" พื้นที่ preview เท่านั้น
- ถ้า thumbnail ไม่ขึ้น (เช่นหน้าต่าง layered) ใช้ preview=False ได้ จะแสดงไอคอนใหญ่แทนทั้งหมด
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QRectF, Qt, QVariantAnimation
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

# ---- ขนาด (logical px) ----
CARD_W = 260.0
CARD_H = 170.0
GAP = 40.0  # ต้องมากกว่าส่วนที่การ์ดโฟกัสขยาย (CARD_W*(FOCUS-1)/2 *2 ~ 65/…) กันการ์ดซ้อนกัน
FOCUS_SCALE = 1.25
STRIP_FRAC = 0.80
PAD = 16.0
LABEL_H = 64.0
HEADER_H = 30.0
FADE_START = 2.0  # การ์ดที่ห่างจากศูนย์กลางเกินนี้เริ่มจาง
FADE_END = 3.5    # ห่างเท่านี้ขึ้นไปโปร่งใสเต็มที่
REMOVE_MS = 180
REMOVE_RISE = 60.0  # px ที่การ์ดลอยขึ้นตอนถูกลบ

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


# ---------------- pure ----------------
def card_layout(count: int, position: float, strip_w: float, strip_h: float,
                card_w: float, card_h: float, gap: float, focus_scale: float = 1.25
                ) -> list[tuple[float, float, float, float, float]]:
    """ตำแหน่งการ์ดทุกใบ (x, y, w, h, opacity) หน่วย logical px
    การ์ด i อยู่กึ่งกลางที่ strip_w/2 + (i - position) * (card_w + gap);
    scale ไล่จาก focus_scale (ระยะ 0) ถึง 1.0 (ระยะ >= 1); จัดกึ่งกลางแนวตั้ง;
    opacity = 1 จนระยะ FADE_START แล้วจางเหลือ 0 ที่ FADE_END"""
    out: list[tuple[float, float, float, float, float]] = []
    pitch = card_w + gap
    for i in range(max(0, count)):
        d = i - position
        ad = abs(d)
        s = focus_scale - (focus_scale - 1.0) * min(ad, 1.0)
        w, h = card_w * s, card_h * s
        cx = strip_w / 2 + d * pitch
        if ad <= FADE_START:
            op = 1.0
        elif ad >= FADE_END:
            op = 0.0
        else:
            op = 1.0 - (ad - FADE_START) / (FADE_END - FADE_START)
        out.append((cx - w / 2, (strip_h - h) / 2, w, h, op))
    return out


def fit_rect(src_w: float, src_h: float, bx: float, by: float, bw: float, bh: float
             ) -> tuple[float, float, float, float]:
    """ใส่ภาพขนาด src ลงกรอบ b โดยรักษาอัตราส่วน จัดกึ่งกลาง -> (x, y, w, h)"""
    if src_w <= 0 or src_h <= 0 or bw <= 0 or bh <= 0:
        return (bx, by, max(bw, 0.0), max(bh, 0.0))
    k = min(bw / src_w, bh / src_h)
    w, h = src_w * k, src_h * k
    return (bx + (bw - w) / 2, by + (bh - h) / 2, w, h)


def x_overlap(a: tuple[float, float, float, float, float],
              b: tuple[float, float, float, float, float]) -> bool:
    """กรอบการ์ด (x, y, w, h, op) สองใบซ้อนกันในแนวนอนหรือไม่ (ขอบชนกันไม่นับ)"""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2]


class _Card:
    __slots__ = ("info", "thumb", "src_size", "rect", "t")

    def __init__(self, info) -> None:  # noqa: ANN001
        self.info = info
        self.thumb: int | None = None
        self.src_size: tuple[int, int] | None = None
        self.rect: tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)
        self.t = 0.0  # ความคืบหน้าการลบ 0..1 (ใช้กับการ์ดที่กำลังหายไป)


class SwitcherWindow(QWidget):
    def __init__(self, preview: bool = True, acrylic: bool = False) -> None:
        """preview: ใช้ DWM thumbnail; acrylic: ลองทำพื้นหลังเบลอ (ปิดเป็นค่าเริ่มต้น เพราะ
        WA_TranslucentBackground + acrylic ให้ขอบเหลี่ยมและสีเพี้ยนบางเครื่อง — วาดสี่เหลี่ยมมนโปร่งแทนเสถียรกว่า)"""
        super().__init__(None)
        self._preview = preview and sys.platform == "win32"
        self._acrylic = acrylic
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        if not self._preview:
            # click-through ด้วย flag ของ Qt (ซึ่งทำให้หน้าต่างเป็น layered) ใช้เฉพาะโหมดไม่มี
            # thumbnail; ถ้ามี preview เลี่ยง layered เพราะ DWM thumbnail อาจไม่แสดงบนหน้าต่าง layered
            # (ไม่เป็นไรที่รับเมาส์ เพราะ NOACTIVATE ไม่แย่งโฟกัสอยู่แล้ว)
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        # preview=True: ไม่ใช้ WA_TranslucentBackground (Qt อาจทำให้หน้าต่างเป็น layered ซึ่ง
        # DWM thumbnail ไม่แสดง) -> วาดพื้นทึบ + ขอมุมมนจาก DWM (Win11). preview=False: โปร่งแสงเดิม
        self._translucent = not self._preview
        if self._translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cards: list[_Card] = []
        self._dying: list[_Card] = []
        self._anims: list[QVariantAnimation] = []
        self._icons: dict[int, QPixmap | None] = {}
        self._position = 0.0
        self._selected = 0
        self._open = False
        self._styled = False
        self._strip_h = CARD_H * FOCUS_SCALE + 2 * PAD

    @property
    def is_open(self) -> bool:
        return self._open

    # ---- API ----
    def open(self, windows: list, position: float) -> None:  # list[WinInfo]
        """สร้างการ์ด ลงทะเบียน thumbnail แล้วแสดงโดยไม่แย่งโฟกัส
        วางกลางจอหลัก (primary) เสมอ — เรียบง่าย ไม่ต้องแปลงพิกัดจากหน้าต่าง foreground"""
        self.close_switcher()
        self._cards = [_Card(w) for w in windows]
        # ล้าง cache ไอคอน (HWND อาจถูกนำกลับมาใช้ซ้ำ) และโหลดล่วงหน้าตอนนี้ ไม่ใช่ใน paintEvent
        self._icons.clear()
        for c in self._cards:
            self._icon(c)
        self._position = float(position)
        self._selected = min(max(0, int(round(position))), max(0, len(self._cards) - 1))
        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen is not None else None
        w = int((geo.width() if geo else 1280) * STRIP_FRAC)
        h = int(self._strip_h + LABEL_H + PAD)
        self.resize(w, h)
        if geo is not None:
            self.move(geo.x() + (geo.width() - w) // 2, geo.y() + (geo.height() - h) // 2)
        self._open = True
        self.show()  # showEvent ตั้ง ex style; winId ใช้ได้แล้ว
        self._apply_win32()  # ซ้ำได้ไม่เสียหาย: กรณี native window ถูกสร้างใหม่
        if sys.platform == "win32":
            try:
                from ..platform.win32 import windows as W
                layered = W.is_layered(self._hwnd())
                log.debug("switcher HWND layered=%s (preview=%s)", layered, self._preview)
                if self._preview and layered:
                    log.warning("switcher เป็น layered window — DWM thumbnail อาจไม่แสดง "
                                "(ลอง SwitcherWindow(preview=False))")
            except Exception:  # noqa: BLE001
                log.debug("ตรวจ layered ไม่สำเร็จ", exc_info=True)
        if self._preview:
            for c in self._cards:
                self._register(c)
        self._relayout()
        self.update()

    def set_position(self, position: float, selected: int) -> None:
        if not self._open:
            return
        self._position = float(position)
        self._selected = selected
        self._relayout()
        self.update()

    def animate_remove(self, index: int) -> None:
        """การ์ดที่ index ลอยขึ้น+จางใน ~180 ms แล้วถูกลบ. เอาออกจากรายการหลักทันที
        (ให้ index ของการ์ดที่เหลือตรงกับ Carousel หลัง remove) แล้วเก็บไว้วาดแยกจนจบ animation"""
        if not self._open or not (0 <= index < len(self._cards)):
            return
        card = self._cards.pop(index)
        self._dying.append(card)
        anim = QVariantAnimation(self)
        anim.setDuration(REMOVE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _step(v) -> None:  # noqa: ANN001
            card.t = float(v)
            self._sync_thumb(card)
            self.update()

        def _done() -> None:
            self._release(card)
            if card in self._dying:
                self._dying.remove(card)
            if anim in self._anims:
                self._anims.remove(anim)
            anim.deleteLater()
            self._relayout()  # แสดง thumbnail ที่ถูกซ่อนไว้คืน
            self.update()

        anim.valueChanged.connect(_step)
        anim.finished.connect(_done)
        self._anims.append(anim)
        anim.start()
        self._relayout()
        self.update()

    def close_switcher(self) -> None:
        """ซ่อนและยกเลิก thumbnail ทั้งหมด เรียกซ้ำได้ (ผู้ใช้งานต้องเรียกก่อนปิดแอป ไม่พึ่ง GC)"""
        for a in self._anims:
            try:
                a.stop()
                a.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self._anims.clear()
        for c in self._cards + self._dying:
            self._release(c)
        self._cards = []
        self._dying = []
        was = self._open
        self._open = False
        if was or self.isVisible():
            self.hide()

    # ---- ภายใน: Win32 ----
    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._apply_win32()  # ทุกครั้งที่แสดง (native window อาจถูกสร้างใหม่)

    def _hwnd(self) -> int:
        return int(self.winId())

    def _apply_win32(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.UINT]
            u.SetWindowPos.restype = wintypes.BOOL
            h = self._hwnd()
            cur = u.GetWindowLongPtrW(h, GWL_EXSTYLE)
            u.SetWindowLongPtrW(h, GWL_EXSTYLE,
                                cur | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
            u.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            self._styled = True
            if not self._translucent:
                from ..platform.win32 import windows as W
                W.set_round_corners(h)
            if self._acrylic:
                self._apply_acrylic(h)
        except Exception:  # noqa: BLE001
            log.warning("ตั้ง extended style ของ switcher ไม่สำเร็จ", exc_info=True)

    def _apply_acrylic(self, hwnd: int) -> None:
        """SetWindowCompositionAttribute (ไม่เป็นทางการ) ACCENT_ACRYLICBLURBEHIND; พังก็ปล่อยพื้นที่วาดเอง"""
        try:
            import ctypes
            from ctypes import wintypes

            class ACCENT_POLICY(ctypes.Structure):
                _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                            ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

            class WCAD(ctypes.Structure):
                _fields_ = [("Attrib", ctypes.c_int), ("pvData", ctypes.c_void_p),
                            ("cbData", ctypes.c_size_t)]

            u = ctypes.WinDLL("user32", use_last_error=True)
            u.SetWindowCompositionAttribute.argtypes = [wintypes.HWND, ctypes.POINTER(WCAD)]
            u.SetWindowCompositionAttribute.restype = wintypes.BOOL
            ap = ACCENT_POLICY(4, 2, 0x99202020, 0)  # ACRYLICBLURBEHIND, ABGR
            data = WCAD(19, ctypes.cast(ctypes.pointer(ap), ctypes.c_void_p), ctypes.sizeof(ap))
            if not u.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)):
                log.debug("acrylic ไม่สำเร็จ ใช้พื้นหลังที่วาดเอง")
        except Exception:  # noqa: BLE001
            log.debug("acrylic ผิดพลาด", exc_info=True)

    def _register(self, c: _Card) -> None:
        """ลงทะเบียน thumbnail ให้การ์ด; หน้าต่างที่ย่ออยู่ thumbnail ว่างเปล่า จึงข้าม (วาดไอคอนใหญ่แทน)"""
        if c.info.iconic:
            return
        try:
            from ..platform.win32 import windows as W
            th = W.register_thumbnail(self._hwnd(), c.info.hwnd)
            if th is None:
                # ถ้า thumbnail ไม่ขึ้นบนเครื่องจริง: ตรวจ log นี้ แล้วลอง SwitcherWindow(preview=False)
                log.debug("ลงทะเบียน DWM thumbnail ไม่สำเร็จ (%s) — แสดงไอคอนแทน", c.info.title)
                return
            c.thumb = th
            c.src_size = W.thumbnail_source_size(th)
        except Exception:  # noqa: BLE001
            log.debug("register thumbnail ผิดพลาด", exc_info=True)

    def _release(self, c: _Card) -> None:
        if c.thumb:
            try:
                from ..platform.win32 import windows as W
                W.unregister_thumbnail(c.thumb)
            except Exception:  # noqa: BLE001
                log.debug("unregister thumbnail ผิดพลาด", exc_info=True)
            c.thumb = None

    # ---- ภายใน: layout ----
    def _relayout(self) -> None:
        lay = card_layout(len(self._cards), self._position, float(self.width()), self._strip_h,
                          CARD_W, CARD_H, GAP, FOCUS_SCALE)
        for c, r in zip(self._cards, lay):
            c.rect = r
            self._sync_thumb(c)

    def _preview_box(self, c: _Card) -> tuple[float, float, float, float]:
        x, y, w, h, _op = c.rect
        s = w / CARD_W if CARD_W else 1.0
        m = 6.0 * s
        hh = HEADER_H * s
        return (x + m, y + hh, w - 2 * m, h - hh - m)

    def _card_offset(self, c: _Card) -> tuple[float, float]:
        """(dy, opacity คูณเพิ่ม) จากการ animate ลบ"""
        return (-REMOVE_RISE * c.t, 1.0 - c.t)

    def _sync_thumb(self, c: _Card) -> None:
        if not c.thumb:
            return
        try:
            from ..platform.win32 import windows as W
            x, y, w, h, op = c.rect
            dy, k = self._card_offset(c)
            op *= k
            bx, by, bw, bh = self._preview_box(c)
            by += dy
            on_strip = (x + w > 0 and x < self.width()) and bw > 1 and bh > 1
            # ระหว่างการ์ดกำลังหายไป ซ่อน thumbnail ของการ์ดที่เลื่อนมาทับช่องของมัน
            # (thumbnail วาดทับ Qt เสมอ จึงจะโผล่ทันทีโดยไม่รอ animation); จบแล้ว _relayout แสดงคืน
            blocked = c not in self._dying and any(x_overlap(c.rect, d.rect) for d in self._dying)
            if not on_strip or blocked or op < 0.02:
                W.update_thumbnail(c.thumb, (0, 0, 1, 1), 0, False)
                return
            if c.src_size:
                bx, by, bw, bh = fit_rect(c.src_size[0], c.src_size[1], bx, by, bw, bh)
            dpr = self.devicePixelRatioF()  # logical -> พิกเซลจริง
            rect = (int(round(bx * dpr)), int(round(by * dpr)),
                    int(round((bx + bw) * dpr)), int(round((by + bh) * dpr)))
            W.update_thumbnail(c.thumb, rect, int(255 * min(1.0, op)), True)
        except Exception:  # noqa: BLE001
            log.debug("อัปเดต thumbnail ผิดพลาด", exc_info=True)

    # ---- ภายใน: วาด ----
    def _icon(self, c: _Card) -> QPixmap | None:
        hw = c.info.hwnd
        if hw in self._icons:
            return self._icons[hw]
        pm: QPixmap | None = None
        try:
            from ..platform.win32 import windows as W
            hicon = W.get_icon(hw, True)
            if hicon:
                img = QImage.fromHICON(hicon)
                if not img.isNull():
                    pm = QPixmap.fromImage(img)
        except Exception:  # noqa: BLE001
            log.debug("โหลดไอคอนไม่สำเร็จ", exc_info=True)
        self._icons[hw] = pm
        return pm

    def _draw_icon_or_letter(self, p: QPainter, c: _Card, box: QRectF, size: float) -> None:
        pm = self._icon(c)
        if pm is not None:
            r = QRectF(box.center().x() - size / 2, box.center().y() - size / 2, size, size)
            p.drawPixmap(r.toRect(), pm)
            return
        letter = (c.info.exe or c.info.title or "?")[:1].upper()
        f = QFont(p.font())
        f.setPixelSize(max(8, int(size * 0.7)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), letter)

    def _paint_card(self, p: QPainter, c: _Card, selected: bool) -> None:
        x, y, w, h, op = c.rect
        dy, k = self._card_offset(c)
        op *= k
        if op < 0.02 or w <= 1:
            return
        s = w / CARD_W
        y += dy
        p.save()
        p.setOpacity(min(1.0, op))
        card = QRectF(x, y, w, h)
        path = QPainterPath()
        path.addRoundedRect(card, 12 * s, 12 * s)
        p.fillPath(path, QColor(48, 48, 54, 235))
        if selected:
            p.setPen(QPen(QColor(90, 170, 255), 3.0))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # หัวการ์ด: ไอคอนเล็ก + ชื่อ (ย่อด้วย ...)
        isz = 18 * s
        icon_box = QRectF(x + 8 * s, y + (HEADER_H * s - isz) / 2, isz, isz)
        pm = self._icon(c)
        if pm is not None:
            p.drawPixmap(icon_box.toRect(), pm)
        f = QFont(p.font())
        f.setPixelSize(max(8, int(12 * s)))
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        tx = icon_box.right() + 6 * s
        text_w = max(0.0, x + w - 8 * s - tx)
        el = QFontMetrics(f).elidedText(c.info.title, Qt.TextElideMode.ElideRight, int(text_w))
        p.drawText(QRectF(tx, y, text_w, HEADER_H * s), int(Qt.AlignmentFlag.AlignVCenter), el)

        # พื้นที่ preview: พื้นดำ; ไม่มี thumbnail (ย่อ/ไม่ใช้ preview/ลงทะเบียนพลาด) -> ไอคอนใหญ่
        bx, by, bw, bh = self._preview_box(c)
        by += dy
        box = QRectF(bx, by, bw, bh)
        p.fillRect(box, QColor(20, 20, 24, 255))
        if not c.thumb:
            self._draw_icon_or_letter(p, c, box, min(bw, bh) * 0.5)
        p.restore()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 20, 20)
        if self._translucent:
            p.fillPath(bg, QColor(24, 24, 28, 120 if self._acrylic else 215))
        else:
            # พื้นทึบเต็มหน้าต่าง (มุมมนมาจาก DWM บน Win11; Win10 เป็นสี่เหลี่ยม)
            p.fillRect(self.rect(), QColor(30, 30, 35))
        # วาดจากไกลมาใกล้ ให้การ์ดกลางอยู่บนสุด
        order = sorted(range(len(self._cards)), key=lambda i: -abs(i - self._position))
        for i in order:
            self._paint_card(p, self._cards[i], i == self._selected)
        for c in self._dying:
            self._paint_card(p, c, False)
        # ป้ายชื่อใต้แถบ
        if 0 <= self._selected < len(self._cards):
            info = self._cards[self._selected].info
            f = QFont(p.font())
            f.setPixelSize(18)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255))
            ty = self._strip_h
            w = self.width() - 2 * PAD
            el = QFontMetrics(f).elidedText(info.title, Qt.TextElideMode.ElideRight, int(w))
            p.drawText(QRectF(PAD, ty, w, 28), int(Qt.AlignmentFlag.AlignCenter), el)
            f2 = QFont(f)
            f2.setPixelSize(13)
            f2.setBold(False)
            p.setFont(f2)
            p.setPen(QColor(255, 255, 255, 150))
            p.drawText(QRectF(PAD, ty + 28, w, 22), int(Qt.AlignmentFlag.AlignCenter), info.exe)
        p.end()


__all__ = ["SwitcherWindow", "card_layout", "fit_rect"]
