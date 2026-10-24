"""ครอบ MediaPipe HandLandmarker (Tasks API) + ดาวน์โหลดโมเดล + เลือกมือ"""
from __future__ import annotations

import logging
import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .features import ANCHOR_IDS, Point

log = logging.getLogger(__name__)

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")
MODEL_NAME = "hand_landmarker.task"


def ensure_model(models_dir: Path) -> Path:
    path = models_dir / MODEL_NAME
    if path.exists():
        return path
    models_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    log.info("กำลังดาวน์โหลดโมเดลมือ → %s", path)
    print("Downloading hand model...", flush=True)
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.replace(path)  # เขียนเสร็จค่อยเปลี่ยนชื่อ กันไฟล์ครึ่งๆ กลางๆ ถ้าโหลดไม่จบ
    return path


@dataclass(frozen=True)
class TrackedHand:
    landmarks: list[Point]  # พิกัดในภาพ (0–1)
    world: list[Point]      # พิกัด 3D หน่วยเมตร
    handedness: str         # "right" | "left"
    score: float


def _anchor(points: list[Point]) -> tuple[float, float]:
    return (sum(points[i][0] for i in ANCHOR_IDS) / len(ANCHOR_IDS),
            sum(points[i][1] for i in ANCHOR_IDS) / len(ANCHOR_IDS))


class HandTracker:
    def __init__(self, model_path: Path, hand: str, mirror: bool, min_presence: float):
        self._hand = hand
        self._mirror = mirror
        self._min_presence = min_presence
        self._last_ms = -1
        self._last_anchor: tuple[float, float] | None = None
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2,  # ตรวจได้ 2 มือ เพื่อเลือกมือที่ตั้งไว้ แล้วไม่สนใจอีกมือ
            min_hand_detection_confidence=min_presence,
            min_hand_presence_confidence=min_presence,
            min_tracking_confidence=min_presence,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)

    def process(self, rgb: np.ndarray, ts: float) -> TrackedHand | None:
        """rgb = ภาพ RGB (กลับด้านแล้วถ้า mirror), ts = timestamp ของเฟรม (วินาที)"""
        ms = int(ts * 1000)
        if ms <= self._last_ms:  # VIDEO mode ต้องการ timestamp เพิ่มขึ้นเสมอ
            ms = self._last_ms + 1
        self._last_ms = ms
        res = self._landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ms)

        candidates = []
        for lms, wlms, cats in zip(res.hand_landmarks, res.hand_world_landmarks, res.handedness):
            cat = cats[0]
            # MediaPipe ตั้งชื่อมือโดยถือว่าภาพเป็นกระจกเงา (กล้องหน้า กลับด้านแล้ว)
            # ถ้าไม่ได้กลับด้านภาพ ต้องสลับซ้าย/ขวา
            label = cat.category_name.lower()
            if not self._mirror:
                label = "left" if label == "right" else "right"
            if label != self._hand or cat.score < self._min_presence:
                continue
            candidates.append(TrackedHand(
                landmarks=[(p.x, p.y, p.z) for p in lms],
                world=[(p.x, p.y, p.z) for p in wlms],
                handedness=label,
                score=cat.score,
            ))

        if not candidates:
            self._last_anchor = None
            return None
        if len(candidates) > 1 and self._last_anchor is not None:
            # มีมือข้างเดียวกันหลายมือ (เช่นอีกคนเข้ามาในเฟรม) → เลือกมือที่ใกล้ตำแหน่งเดิม
            hand = min(candidates, key=lambda h: math.dist(_anchor(h.landmarks), self._last_anchor))
        else:
            hand = max(candidates, key=lambda h: h.score)
        self._last_anchor = _anchor(hand.landmarks)
        return hand

    def close(self) -> None:
        self._landmarker.close()
