"""หน้าต่าง Calibrate: เก็บท่ามือทีละท่า คำนวณเกณฑ์ แสดงตารางเทียบ แล้วบันทึกลง config.toml

เป็นหน้าต่างปกติ (รับโฟกัสได้) เรียกจาก Qt main thread เท่านั้น
อ่านฟีเจอร์ของมือจาก inference.latest() ทุก ~30 ms; การหยุดควบคุมเมาส์ระหว่างนี้เป็นหน้าที่ของผู้เรียก
ลอจิกขั้นตอน/จับเวลาอยู่ใน calibration.CalibrationSession (pure) ที่นี่มีแต่ UI
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import calibration
from .. import config as config_mod
from ..config import Config

log = logging.getLogger(__name__)

TICK_MS = 30
STYLE = """
QWidget { font-family: 'Leelawadee UI', 'Segoe UI', Tahoma; font-size: 14px; }
QLabel#title { font-size: 30px; font-weight: bold; }
QLabel#step { color: #888; }
QLabel#hand_ok { font-weight: bold; }
QLabel#status { font-size: 22px; }
QLabel#problem { color: #c0392b; }
"""


def dump_path(now: datetime | None = None) -> Path:
    return config_mod.LOGS_DIR / f"calib-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}.json"


def result_to_jsonable(result: calibration.CalibrationResult | None) -> dict | None:
    if result is None:
        return None
    return {"values": {f"{s}.{k}": v for (s, k), v in result.values.items()},
            "problems": result.problems, "stats": result.stats}


class CalibrateWindow(QWidget):
    closed = Signal()
    # {(section, key): value} ที่เขียนแล้ว (รวม meta.calibrated); ใช้ object เพราะ dict คีย์ tuple แปลงเป็น C++ ไม่ได้
    saved = Signal(object)

    def __init__(self, inference, cfg: Config, config_path: Path) -> None:  # noqa: ANN001
        super().__init__()
        self._inference = inference
        self._cfg = cfg
        self._path = config_path
        self._session = calibration.CalibrationSession()
        self._result: calibration.CalibrationResult | None = None
        self._dump_file = dump_path()
        self._done = False
        self._closing = False

        self.setWindowTitle("Calibrate ท่ามือ")
        self.setStyleSheet(STYLE)
        self.resize(640, 480)
        self._build()
        self._center()

        self._session.restart_prepare(time.monotonic())
        self._refresh_pose()
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---------------------------------------------------------------- UI
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)

        # --- หน้าเก็บท่า ---
        self._capture = QWidget()
        cl = QVBoxLayout(self._capture)
        self._step = QLabel(objectName="step")
        self._title = QLabel(objectName="title")
        self._text = QLabel()
        self._text.setWordWrap(True)
        self._hand = QLabel(objectName="hand_ok")
        self._status = QLabel(objectName="status")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setTextVisible(False)
        self._redo = QPushButton("ทำท่านี้ใหม่")
        self._redo.clicked.connect(self._on_redo_current)
        cancel = QPushButton("ยกเลิก")
        cancel.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addWidget(self._redo)
        row.addStretch(1)
        row.addWidget(cancel)
        for w in (self._step, self._title, self._text, self._hand):
            cl.addWidget(w)
        cl.addStretch(1)
        cl.addWidget(self._status)
        cl.addWidget(self._bar)
        cl.addStretch(1)
        cl.addLayout(row)
        root.addWidget(self._capture)

        # --- หน้าสรุป ---
        self._review = QWidget()
        rl = QVBoxLayout(self._review)
        self._review_head = QLabel(objectName="title")
        self._problem_box = QVBoxLayout()
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["ค่า", "ปัจจุบัน", "ใหม่"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._save = QPushButton("บันทึก")
        self._save.clicked.connect(self._on_save)
        cancel2 = QPushButton("ยกเลิก")
        cancel2.clicked.connect(self.close)
        self._message = QLabel()
        row2 = QHBoxLayout()
        row2.addWidget(self._message, 1)
        row2.addWidget(self._save)
        row2.addWidget(cancel2)
        rl.addWidget(self._review_head)
        rl.addLayout(self._problem_box)
        rl.addWidget(self._table, 1)
        rl.addLayout(row2)
        root.addWidget(self._review)
        self._review.hide()

    def _center(self) -> None:
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)

    # ---------------------------------------------------------------- ขั้นตอน
    def _refresh_pose(self) -> None:
        s = self._session
        spec = s.current
        self._step.setText(f"ท่าที่ {s.index + 1} / {len(s.poses)}")
        self._title.setText(spec.title)
        self._text.setText(spec.text)

    def _on_redo_current(self) -> None:
        self._session.restart_prepare(time.monotonic())

    def _tick(self) -> None:
        if self._done:
            return
        s = self._session
        now = time.monotonic()
        fr = self._inference.latest()
        feats = None
        if fr is not None:
            feats = getattr(fr, "features", None)
            if feats is None:
                feats = getattr(fr, "feats", None)
            s.feed(fr.seq, feats)
        if s.finished:
            return
        before = s.index
        if s.tick(now):
            if s.finished:
                self._enter_review()
                return
            if s.index != before:
                self._refresh_pose()
        self._hand.setText("เห็นมือ ✓" if feats is not None else "ไม่เห็นมือ")
        self._hand.setStyleSheet("color: #27ae60;" if feats is not None else "color: #c0392b;")
        if s.phase == "prepare":
            self._status.setText(f"เตรียมตัว... {s.countdown(now):.1f} วินาที")
            self._bar.setValue(0)
        else:
            self._status.setText(f"กำลังเก็บ... ({len(s.samples[s.current.id])} เฟรม)")
            self._bar.setValue(int(s.progress(now) * 1000))

    # ---------------------------------------------------------------- หน้าสรุป
    def _enter_review(self) -> None:
        self._result = calibration.compute(self._session.samples, self._cfg.gesture)
        self._dump()
        res = self._result
        self._capture.hide()
        self._review.show()
        while self._problem_box.count():
            item = self._problem_box.takeAt(0)
            lay, w = item.layout(), item.widget()
            if w is not None:
                w.deleteLater()
            if lay is not None:
                while lay.count():
                    sub = lay.takeAt(0).widget()
                    if sub is not None:
                        sub.deleteLater()
                lay.deleteLater()
        titles = {p.id: p.title for p in calibration.POSES}
        if res.problems:
            self._review_head.setText("มีบางท่าที่ยังไม่ผ่าน")
        else:
            self._review_head.setText("ผลการ Calibrate")
        for pid, msg in res.problems.items():
            row = QHBoxLayout()
            lab = QLabel(f"{titles[pid]}: {msg}", objectName="problem")
            lab.setWordWrap(True)
            btn = QPushButton(f"ทำ \"{titles[pid]}\" ใหม่")
            btn.clicked.connect(lambda _=False, p=pid: self._redo_pose(p))
            row.addWidget(lab, 1)
            row.addWidget(btn)
            self._problem_box.addLayout(row)
        rows = calibration.summary_rows(res, self._cfg.gesture)
        self._table.setRowCount(len(rows))
        for i, cells in enumerate(rows):
            for j, text in enumerate(cells):
                self._table.setItem(i, j, QTableWidgetItem(text))
        self._save.setEnabled(bool(res.values))
        self._message.setText("" if res.values else "ไม่มีค่าที่คำนวณได้ ให้ทำท่าที่มีปัญหาใหม่")

    def _redo_pose(self, pose_id: str) -> None:
        self._review.hide()
        self._capture.show()
        self._session.redo(pose_id, time.monotonic())
        self._refresh_pose()

    # ---------------------------------------------------------------- บันทึก / dump
    def _on_save(self) -> None:
        if self._result is None or self._done:
            return
        updates: dict[tuple[str, str], object] = dict(self._result.values)
        updates[("meta", "calibrated")] = datetime.now().isoformat(timespec="seconds")
        try:
            config_mod.update_values(self._path, updates)
        except Exception as exc:  # noqa: BLE001
            log.exception("calibrate: บันทึกไม่สำเร็จ")
            self._message.setText(f"บันทึกไม่สำเร็จ: {exc}")
            return
        for (section, key), value in updates.items():
            setattr(getattr(self._cfg, section), key, value)
        self._done = True
        self._dump(applied=updates)
        self.saved.emit(dict(updates))
        self._save.setEnabled(False)
        self._message.setText("บันทึกแล้ว")
        QTimer.singleShot(1200, self.close)

    def _dump(self, applied: dict | None = None) -> None:
        """เก็บตัวอย่างดิบ + ผลลัพธ์ลง logs/calib-*.json (ไฟล์เดียวต่อหนึ่งหน้าต่าง เขียนทับ)"""
        samples = self._session.samples
        if not any(samples.values()):
            return
        payload = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "hand": self._cfg.camera.hand,
            "samples": calibration.samples_to_jsonable(samples),
            "result": result_to_jsonable(self._result),
        }
        if applied is not None:
            payload["applied"] = {f"{s}.{k}": v for (s, k), v in applied.items()}
        try:
            self._dump_file.parent.mkdir(parents=True, exist_ok=True)
            self._dump_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.exception("calibrate: เขียน %s ไม่ได้", self._dump_file)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._closing:
            self._closing = True
            self._timer.stop()
            if not self._done:
                self._dump()  # ยกเลิกกลางคัน: ยังเก็บตัวอย่างที่ได้ไว้ตรวจย้อนหลัง
            self.closed.emit()
        super().closeEvent(event)
