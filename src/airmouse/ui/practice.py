"""โหมดฝึก: หน้าต่างเต็มจอรับ event เมาส์จริง (Air Mouse คุม cursor ตามปกติ) 3 ด่าน
คลิก -> ลาก -> scroll แล้วสรุปผลและบันทึก logs/practice-<ts>.json

ตรรกะทั้งหมด (สุ่มเป้า, hit test, คิดคะแนน, ตัวจับเวลาค้างตอน scroll) เป็น helper ล้วนที่เทสต์ได้
โดยไม่ต้องมี widget ส่วน PracticeWindow แค่ต่อ event ของ Qt เข้ากับ helper เหล่านี้
"""
from __future__ import annotations

import json
import logging
import math
import random
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QPushButton, QWidget

log = logging.getLogger(__name__)

# ---------------- ค่าคงที่ ----------------
N_NORMAL, N_DOUBLE, N_RIGHT = 10, 2, 2
R_MIN, R_MAX = 12.0, 32.0        # รัศมีเป้า (เส้นผ่านศูนย์กลาง 24–64 px)
EDGE_MARGIN = 80.0               # เว้นขอบจอ
TOP_MARGIN = 150.0               # เว้นพื้นที่ข้อความด้านบน
MIN_TARGET_DIST = 150.0          # เป้าติดกันต้องห่างกันอย่างน้อยเท่านี้
N_DRAG = 5
BALL_R = 22.0
RING_R = 64.0
N_SCROLL = 3
N_ROWS = 60
ROW_H = 56.0
HOLD_S = 0.5                     # ต้องค้างแถวในกรอบนานเท่านี้
WHEEL_PX_PER_NOTCH = 48.0        # 1 notch (120) เลื่อนกี่ px
STAGES = ("click", "drag", "scroll")
STAGE_TITLES = {"click": "คลิก", "drag": "ลาก", "scroll": "Scroll"}


# ---------------- pure helpers ----------------
@dataclass(frozen=True)
class Target:
    x: float
    y: float
    r: float
    kind: str  # "click" | "double" | "right"


def make_targets(rng: random.Random, w: float, h: float, n_normal: int = N_NORMAL,
                 n_double: int = N_DOUBLE, n_right: int = N_RIGHT,
                 r_min: float = R_MIN, r_max: float = R_MAX, margin: float = EDGE_MARGIN,
                 top: float = TOP_MARGIN, min_dist: float = MIN_TARGET_DIST) -> list[Target]:
    """สุ่มเป้าตามลำดับ: ชนิดสลับลำดับแบบสุ่ม, อยู่ในจอเว้นขอบ, เป้าติดกันห่าง >= min_dist"""
    kinds = ["click"] * n_normal + ["double"] * n_double + ["right"] * n_right
    rng.shuffle(kinds)
    out: list[Target] = []
    prev: tuple[float, float] | None = None
    for kind in kinds:
        r = rng.uniform(r_min, r_max)
        x0, x1 = margin + r, w - margin - r
        y0, y1 = top + r, h - margin - r
        if x1 <= x0 or y1 <= y0:
            raise ValueError("หน้าจอเล็กเกินไปสำหรับเป้า")
        x = y = 0.0
        for _ in range(200):
            x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
            if prev is None or math.hypot(x - prev[0], y - prev[1]) >= min_dist:
                break
        else:  # จอเล็กมากจนสุ่มไม่ผ่าน: เลือกมุมที่ไกลสุดจาก prev
            assert prev is not None
            x = x1 if abs(prev[0] - x1) > abs(prev[0] - x0) else x0
            y = y1 if abs(prev[1] - y1) > abs(prev[1] - y0) else y0
        out.append(Target(x, y, r, kind))
        prev = (x, y)
    return out


def hit_test(tx: float, ty: float, r: float, px: float, py: float) -> tuple[bool, float]:
    """(โดนไหม, ระยะถึงขอบเป้า) ระยะ = 0 ถ้าอยู่ในเป้า ไม่งั้นระยะจากจุดถึงขอบวง"""
    d = math.hypot(px - tx, py - ty)
    return d <= r, max(0.0, d - r)


def drag_success(ball_x: float, ball_y: float, ball_r: float,
                 ring_x: float, ring_y: float, ring_r: float) -> tuple[bool, float]:
    """สำเร็จถ้าจุดศูนย์กลางบอลอยู่ในรัศมี (ring_r - ball_r) คืน (ok, ระยะระหว่างศูนย์กลาง)"""
    d = math.hypot(ball_x - ring_x, ball_y - ring_y)
    return d <= ring_r - ball_r, d


def make_drag_trial(rng: random.Random, w: float, h: float,
                    min_dist: float = 300.0) -> tuple[tuple[float, float], tuple[float, float]]:
    """สุ่ม (ตำแหน่งบอล, ตำแหน่งวง) ให้ห่างกันอย่างน้อย min_dist"""
    m = EDGE_MARGIN + RING_R
    for _ in range(200):
        bx, by = rng.uniform(m, w - m), rng.uniform(TOP_MARGIN + RING_R, h - m)
        rx, ry = rng.uniform(m, w - m), rng.uniform(TOP_MARGIN + RING_R, h - m)
        if math.hypot(bx - rx, by - ry) >= min_dist:
            break
    return (bx, by), (rx, ry)


class ScrollHoldTracker:
    """จับว่าแถวเป้าอยู่ในกรอบครบ HOLD_S หรือยัง และนับ overshoot

    update(inside, now): inside = แถวเป้าทับกรอบอยู่ไหม
    overshoot = แถวเข้ากรอบแล้วหลุดออกก่อนค้างครบเวลา (เลื่อนเลยกรอบ)
    """

    def __init__(self, hold_s: float = HOLD_S) -> None:
        self.hold_s = hold_s
        self.overshoots = 0
        self.done = False
        self._since: float | None = None

    def update(self, inside: bool, now: float) -> bool:
        if self.done:
            return True
        if inside:
            if self._since is None:
                self._since = now
            elif now - self._since >= self.hold_s:
                self.done = True
        elif self._since is not None:
            self.overshoots += 1
            self._since = None
        return self.done


def _stats(times: list[float]) -> tuple[float | None, float | None]:
    if not times:
        return None, None
    return statistics.fmean(times), statistics.median(times)


def summarize(trials: list[dict]) -> dict:
    """สรุปต่อด่าน: attempts, hits, accuracy (%), mean/median time (เฉพาะครั้งที่สำเร็จ), overshoots

    trial = {"stage", "hit": bool, "time_s": float, ...} (scroll มี "overshoots")
    """
    out: dict[str, dict] = {}
    for stage in STAGES:
        ts = [t for t in trials if t.get("stage") == stage]
        hits = [t for t in ts if t.get("hit")]
        mean, med = _stats([float(t["time_s"]) for t in hits])
        entry: dict = {
            "attempts": len(ts),
            "hits": len(hits),
            "accuracy": round(100.0 * len(hits) / len(ts), 1) if ts else None,
            "mean_time_s": None if mean is None else round(mean, 3),
            "median_time_s": None if med is None else round(med, 3),
        }
        if stage == "scroll":
            entry["overshoots"] = int(sum(t.get("overshoots", 0) for t in ts))
        out[stage] = entry
    return out


def save_results(logs_dir: Path, trials: list[dict], summary: dict,
                 now: datetime | None = None) -> Path:
    now = now or datetime.now()
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"practice-{now:%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"time": now.isoformat(timespec="seconds"), "summary": summary,
                                "trials": trials}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


# ---------------- Qt ----------------
BG = QColor(24, 24, 28)
PANEL = QColor(40, 40, 46)
TEXT = QColor(230, 230, 235)
MUTED = QColor(140, 140, 150)
GREEN = QColor(80, 210, 120)
RED = QColor(235, 80, 80)
YELLOW = QColor(240, 200, 60)
CYAN = QColor(0, 200, 255)

_BTN_CSS = ("QPushButton{background:#34343c;color:#e6e6eb;border:1px solid #55555e;"
            "border-radius:8px;padding:10px 26px;font-size:18px}"
            "QPushButton:hover{background:#44444e}")

_KIND_LABEL = {"click": "คลิกซ้าย", "double": "ดับเบิลคลิก", "right": "คลิกขวา"}
_KIND_COLOR = {"click": GREEN, "double": YELLOW, "right": CYAN}


class PracticeWindow(QWidget):
    """หน้าต่างโหมดฝึกเต็มจอ ปิดด้วย Esc / ปุ่ม ปิด แล้วส่ง signal closed"""

    closed = Signal()

    def __init__(self, logs_dir: Path, clock: Callable[[], float] = time.monotonic,
                 rng: random.Random | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Air Mouse: โหมดฝึก")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.logs_dir = Path(logs_dir)
        self._clock = clock
        self._rng = rng or random.Random()
        self.saved_path: Path | None = None
        self._stage = "click"
        self._trials: list[dict] = []
        self._summary: dict = {}

        self._skip_btn = QPushButton("ข้าม", self)
        self._again_btn = QPushButton("ฝึกอีกครั้ง", self)
        self._close_btn = QPushButton("ปิด", self)
        for b in (self._skip_btn, self._again_btn, self._close_btn):
            b.setStyleSheet(_BTN_CSS)
            b.setFocusPolicy(Qt.NoFocus)
        self._skip_btn.clicked.connect(self._skip_stage)
        self._again_btn.clicked.connect(self._restart)
        self._close_btn.clicked.connect(self.close)

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

        self._restart()

    # ---------- วงจรของด่าน ----------
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._layout_buttons()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._layout_buttons()

    def _layout_buttons(self) -> None:
        self._skip_btn.adjustSize()
        self._skip_btn.move(self.width() - self._skip_btn.width() - 30, 24)
        self._again_btn.adjustSize()
        self._close_btn.adjustSize()
        y = int(self.height() * 0.78)
        total = self._again_btn.width() + 30 + self._close_btn.width()
        x = (self.width() - total) // 2
        self._again_btn.move(x, y)
        self._close_btn.move(x + self._again_btn.width() + 30, y)

    def start(self) -> None:
        self.showFullScreen()
        self.activateWindow()

    def _restart(self) -> None:
        self._trials = []
        self._summary = {}
        self.saved_path = None
        self._begin_stage("click")

    def _size(self) -> tuple[float, float]:
        return float(max(self.width(), 800)), float(max(self.height(), 600))

    def _begin_stage(self, stage: str) -> None:
        self._stage = stage
        self._skip_btn.setVisible(stage in STAGES)
        self._again_btn.setVisible(stage == "results")
        self._close_btn.setVisible(stage == "results")
        self._timer.stop()
        w, h = self._size()
        if stage == "click":
            self._targets = make_targets(self._rng, w, h)
            self._ti = 0
            self._target_t0 = self._clock()
            self._dbl_pending = False
        elif stage == "drag":
            self._di = 0
            self._new_drag_trial()
        elif stage == "scroll":
            self._si = 0
            self._new_scroll_trial()
            self._timer.start()
        elif stage == "results":
            self._summary = summarize(self._trials)
            try:
                self.saved_path = save_results(self.logs_dir, self._trials, self._summary)
            except OSError:
                log.exception("บันทึกผลโหมดฝึกไม่ได้")
        self.update()

    def _next_stage(self) -> None:
        i = STAGES.index(self._stage) + 1
        self._begin_stage(STAGES[i] if i < len(STAGES) else "results")

    def _skip_stage(self) -> None:
        self._next_stage()

    # ---------- ด่านคลิก ----------
    def _record_click(self, hit: bool, dist: float, button: str) -> None:
        t = self._targets[self._ti]
        self._trials.append({
            "stage": "click", "index": self._ti, "kind": t.kind, "radius": round(t.r, 1),
            "x": round(t.x, 1), "y": round(t.y, 1), "button": button, "hit": hit,
            "edge_dist": round(dist, 1), "time_s": round(self._clock() - self._target_t0, 3),
        })

    def _advance_target(self) -> None:
        self._ti += 1
        self._dbl_pending = False
        if self._ti >= len(self._targets):
            self._next_stage()
            return
        self._target_t0 = self._clock()
        self.update()

    def _click_press(self, e: QMouseEvent, double: bool = False) -> None:
        t = self._targets[self._ti]
        hit_pos, dist = hit_test(t.x, t.y, t.r, e.position().x(), e.position().y())
        btn = e.button()
        name = {Qt.LeftButton: "left", Qt.RightButton: "right"}.get(btn)
        if name is None:
            return
        if t.kind == "double":
            if name != "left":
                self._record_click(False, dist, name)
            elif double:  # event ดับเบิลคลิกของ Qt (คลิกที่ 2 ภายในช่วงเวลา)
                if hit_pos:
                    self._record_click(True, dist, "double")
                    self._advance_target()
                else:
                    self._record_click(False, dist, "double")
            elif not hit_pos:
                self._record_click(False, dist, name)
            # คลิกเดี่ยวบนเป้าดับเบิล: รอคลิกที่สอง ไม่นับพลาด
        else:
            want = "right" if t.kind == "right" else "left"
            if hit_pos and name == want:
                self._record_click(True, dist, name)
                self._advance_target()
            else:
                self._record_click(False, dist, name)
        self.update()

    # ---------- ด่านลาก ----------
    def _new_drag_trial(self) -> None:
        w, h = self._size()
        self._ball, self._ring = make_drag_trial(self._rng, w, h)
        self._ball_pos = QPointF(*self._ball)
        self._dragging = False
        self._grab_off = QPointF()
        self._drag_t0 = self._clock()
        self._drag_attempts = 0

    def _drag_release(self) -> None:
        self._dragging = False
        ok, d = drag_success(self._ball_pos.x(), self._ball_pos.y(), BALL_R,
                             self._ring[0], self._ring[1], RING_R)
        self._trials.append({
            "stage": "drag", "index": self._di, "hit": ok, "final_dist": round(d, 1),
            "time_s": round(self._clock() - self._drag_t0, 3),
        })
        if ok:
            self._di += 1
            if self._di >= N_DRAG:
                self._next_stage()
                return
            self._new_drag_trial()
        self.update()

    # ---------- ด่าน scroll ----------
    def _new_scroll_trial(self) -> None:
        self._offset = 0.0  # px ที่เลื่อนไปแล้ว (แถว i อยู่ที่ y = top + i*ROW_H - offset)
        while True:
            self._target_row = self._rng.randrange(8, N_ROWS - 4)
            if abs(self._row_center_y(self._target_row) - self._frame_center_y()) > ROW_H * 6:
                break
        self._tracker = ScrollHoldTracker()
        self._scroll_t0 = self._clock()

    def _frame_center_y(self) -> float:
        return self._size()[1] / 2 + 40

    def _row_center_y(self, i: int) -> float:
        # แถว 0 อยู่กลางกรอบเมื่อ offset = 0 ; เลื่อนลง offset เพิ่ม
        return self._frame_center_y() + i * ROW_H - self._offset

    def _max_offset(self) -> float:
        return (N_ROWS - 1) * ROW_H

    def _tick(self) -> None:
        if self._stage != "scroll":
            return
        inside = abs(self._row_center_y(self._target_row) - self._frame_center_y()) <= ROW_H / 2
        if self._tracker.update(inside, self._clock()):
            self._trials.append({
                "stage": "scroll", "index": self._si, "target_row": self._target_row,
                "hit": True, "time_s": round(self._clock() - self._scroll_t0, 3),
                "overshoots": self._tracker.overshoots,
            })
            self._si += 1
            if self._si >= N_SCROLL:
                self._next_stage()
                return
            self._new_scroll_trial()
        self.update()

    # ---------- event ----------
    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._stage == "click":
            self._click_press(e)
        elif self._stage == "drag" and e.button() == Qt.LeftButton:
            d = math.hypot(e.position().x() - self._ball_pos.x(),
                           e.position().y() - self._ball_pos.y())
            if d <= BALL_R:
                self._dragging = True
                self._grab_off = self._ball_pos - e.position()
                self.update()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._stage == "click":
            self._click_press(e, double=True)
        else:
            self.mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._stage == "drag" and self._dragging:
            self._ball_pos = e.position() + self._grab_off
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._stage == "drag" and self._dragging and e.button() == Qt.LeftButton:
            self._drag_release()

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        if self._stage == "scroll":
            dy = e.angleDelta().y()
            self._offset = min(max(self._offset - dy / 120.0 * WHEEL_PX_PER_NOTCH, 0.0),
                               self._max_offset())
            self.update()

    def closeEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().closeEvent(e)
        self.closed.emit()

    # ---------- วาด ----------
    def _text(self, p: QPainter, text: str, rect: QRectF, size: int, color: QColor,
              bold: bool = False, align=Qt.AlignCenter) -> None:
        f = QFont("Segoe UI", size)
        f.setBold(bold)
        p.setFont(f)
        p.setPen(color)
        p.drawText(rect, int(align), text)

    def _header(self, p: QPainter, title: str, sub: str, progress: str) -> None:
        w = float(self.width())
        self._text(p, title, QRectF(0, 20, w, 60), 28, TEXT, True)
        self._text(p, sub, QRectF(0, 80, w, 36), 16, MUTED)
        self._text(p, progress, QRectF(30, 24, 400, 40), 18, YELLOW, True,
                   Qt.AlignLeft | Qt.AlignVCenter)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), BG)
        if self._stage == "click":
            self._paint_click(p)
        elif self._stage == "drag":
            self._paint_drag(p)
        elif self._stage == "scroll":
            self._paint_scroll(p)
        else:
            self._paint_results(p)

    def _paint_click(self, p: QPainter) -> None:
        t = self._targets[self._ti]
        extra = {"click": "คลิกซ้ายที่วงกลม", "double": "ดับเบิลคลิกที่วงกลม (คลิกซ้ายสองครั้งเร็วๆ)",
                 "right": "คลิกขวาที่วงกลม"}[t.kind]
        self._header(p, f"ด่าน 1: คลิก ({_KIND_LABEL[t.kind]})", extra,
                     f"เป้า {self._ti + 1}/{len(self._targets)}")
        col = _KIND_COLOR[t.kind]
        p.setBrush(col)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawEllipse(QPointF(t.x, t.y), t.r, t.r)
        if t.kind != "click":
            label = "ดับเบิลคลิก" if t.kind == "double" else "คลิกขวา"
            self._text(p, label, QRectF(t.x - 90, t.y + t.r + 6, 180, 30), 14, col, True)

    def _paint_drag(self, p: QPainter) -> None:
        self._header(p, "ด่าน 2: ลาก", "กดค้างที่ลูกบอล ลากไปวางในวงแล้วปล่อย",
                     f"ครั้งที่ {self._di + 1}/{N_DRAG}")
        rx, ry = self._ring
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(GREEN, 4))
        p.drawEllipse(QPointF(rx, ry), RING_R, RING_R)
        p.setBrush(YELLOW if self._dragging else CYAN)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawEllipse(self._ball_pos, BALL_R, BALL_R)

    def _paint_scroll(self, p: QPainter) -> None:
        self._header(p, "ด่าน 3: Scroll",
                     f"เลื่อนให้ \"แถว {self._target_row + 1}\" อยู่ในกรอบ แล้วค้างไว้ {HOLD_S:g} วิ",
                     f"ครั้งที่ {self._si + 1}/{N_SCROLL}")
        w = float(self.width())
        cx, cy = w / 2, self._frame_center_y()
        list_w = min(560.0, w - 200)
        left = cx - list_w / 2
        p.setClipRect(QRectF(0, 130, w, self.height() - 130))
        for i in range(N_ROWS):
            y = self._row_center_y(i)
            if y < 100 or y > self.height() + ROW_H:
                continue
            r = QRectF(left, y - ROW_H / 2 + 3, list_w, ROW_H - 6)
            is_t = i == self._target_row
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(60, 90, 140) if is_t else PANEL)
            p.drawRoundedRect(r, 8, 8)
            self._text(p, f"แถว {i + 1}" + ("   << เป้า" if is_t else ""), r, 16, TEXT, is_t)
        inside = self._tracker._since is not None
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(GREEN if inside else YELLOW, 4))
        p.drawRoundedRect(QRectF(left - 8, cy - ROW_H / 2, list_w + 16, ROW_H), 10, 10)
        p.setClipping(False)

    def _paint_results(self, p: QPainter) -> None:
        w = float(self.width())
        self._text(p, "สรุปผลการฝึก", QRectF(0, 40, w, 60), 30, TEXT, True)
        y = 130.0
        for stage in STAGES:
            s = self._summary.get(stage, {})
            if not s.get("attempts"):
                line = "ข้ามด่านนี้"
            else:
                mean = s["mean_time_s"]
                med = s["median_time_s"]
                line = (f"แม่นยำ {s['accuracy']:.0f}% ({s['hits']}/{s['attempts']})   "
                        f"เวลาเฉลี่ย {mean:.2f} วิ   มัธยฐาน {med:.2f} วิ")
                if stage == "scroll":
                    line += f"   เลยกรอบ {s['overshoots']} ครั้ง"
            r = QRectF(w / 2 - 480, y, 960, 90)
            p.setPen(Qt.NoPen)
            p.setBrush(PANEL)
            p.drawRoundedRect(r, 12, 12)
            self._text(p, STAGE_TITLES[stage], QRectF(r.x() + 24, r.y() + 8, 400, 36), 18,
                       YELLOW, True, Qt.AlignLeft | Qt.AlignVCenter)
            self._text(p, line, QRectF(r.x() + 24, r.y() + 44, 920, 36), 16, TEXT, False,
                       Qt.AlignLeft | Qt.AlignVCenter)
            y += 105
        if self.saved_path is not None:
            self._text(p, f"บันทึกผลที่ {self.saved_path}", QRectF(0, y + 6, w, 30), 12, MUTED)
