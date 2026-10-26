"""เสียงตอบสนองสั้นๆ (คลิก/ลาก/เปิด-ปิด) สร้างไฟล์ WAV เองตอนรันครั้งแรก

เล่นผ่าน QSoundEffect ต้องเรียกจาก main thread (GUI) เท่านั้น ห้าม raise ออกมา
"""
from __future__ import annotations

import logging
import math
import struct
import wave
from collections.abc import Sequence
from pathlib import Path

from .config import PROJECT_ROOT

log = logging.getLogger(__name__)

DEFAULT_DIR = PROJECT_ROOT / "assets" / "sounds"
SAMPLE_RATE = 44100
VOLUME = 0.25
FADE_S = 0.004

Tone = float | tuple[float, float]  # ความถี่คงที่ หรือ (เริ่ม, จบ) แบบ sweep

# ชื่อไฟล์ -> (โทนต่อเนื่อง, ความยาวรวมวินาที)
SOUND_SPECS: dict[str, tuple[Sequence[Tone], float]] = {
    "click": ((2000.0,), 0.035),
    "drag_start": ((1000.0, 1500.0), 0.090),
    "drag_end": ((1500.0, 1000.0), 0.090),
    "toggle_on": ((( 600.0, 1400.0),), 0.120),
    "toggle_off": (((1400.0, 600.0),), 0.120),
    "notify": ((1200.0, 900.0), 0.160),
}

# event จาก gestures/lifecycle -> ชื่อเสียง
EVENT_SOUNDS: dict[str, str] = {
    "click_left": "click",
    "click_right": "click",
    "drag_start": "drag_start",
    "drag_end": "drag_end",
    "toggle_on": "toggle_on",
    "toggle_off": "toggle_off",
    "exit": "notify",
}


def make_wav(path: Path, freqs: Sequence[Tone], duration_s: float, volume: float = VOLUME) -> None:
    """เขียน WAV 16-bit mono 44.1 kHz: sine ต่อเนื่องตาม freqs (แบ่งเวลาเท่าๆ กัน)
    แต่ละโทนมี fade in/out กันเสียงป๊อก; peak ไม่เกิน volume"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n_total = max(1, int(SAMPLE_RATE * duration_s))
    per = max(1, n_total // max(1, len(freqs)))
    fade = max(1, min(int(SAMPLE_RATE * FADE_S), per // 2))
    frames = bytearray()
    for tone in freqs:
        f0, f1 = (tone, tone) if isinstance(tone, (int, float)) else tone
        phase = 0.0
        for i in range(per):
            f = f0 + (f1 - f0) * i / per
            phase += 2 * math.pi * f / SAMPLE_RATE
            env = min(1.0, i / fade, (per - 1 - i) / fade)
            v = volume * env * math.sin(phase)
            frames += struct.pack("<h", int(round(v * 32767)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))


def sound_for_event(event: str) -> str | None:
    """ชื่อเสียงของ event (None ถ้าไม่รู้จัก)"""
    return EVENT_SOUNDS.get(event)


class Sounds:
    def __init__(self, enabled: bool, directory: Path = DEFAULT_DIR) -> None:
        self.enabled = enabled
        self.directory = Path(directory)
        self._effects: dict[str, object] = {}
        self._failed = False
        if enabled:
            self._ensure_files()

    def _ensure_files(self) -> None:
        try:
            for name, (freqs, dur) in SOUND_SPECS.items():
                p = self.directory / f"{name}.wav"
                if not p.exists():
                    make_wav(p, freqs, dur)
        except Exception:  # noqa: BLE001
            log.exception("สร้างไฟล์เสียงไม่สำเร็จ ปิดเสียง")
            self._failed = True

    def _effect(self, name: str):  # noqa: ANN202
        """สร้าง QSoundEffect แบบ lazy (ใน main thread ตอน play ครั้งแรก)"""
        eff = self._effects.get(name)
        if eff is None:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect

            eff = QSoundEffect()
            eff.setSource(QUrl.fromLocalFile(str(self.directory / f"{name}.wav")))
            eff.setVolume(1.0)  # ระดับเสียงอยู่ในไฟล์แล้ว (~0.25)
            self._effects[name] = eff
        return eff

    def play(self, event: str) -> None:
        if not self.enabled or self._failed:
            return
        name = sound_for_event(event)
        if name is None:
            return
        try:
            self._effect(name).play()
        except Exception:  # noqa: BLE001
            log.exception("เล่นเสียงไม่ได้ (QtMultimedia?) ปิดเสียง")
            self._failed = True
