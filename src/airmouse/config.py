"""โหลด/บันทึก config.toml เป็น dataclass (ค่าไหนไม่มีในไฟล์ใช้ค่าเริ่มต้น)"""
from __future__ import annotations

import dataclasses
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
MODELS_DIR = PROJECT_ROOT / "models"
LOGS_DIR = PROJECT_ROOT / "logs"


@dataclass
class CameraConfig:
    index: int = 0
    width: int = 640
    height: int = 480
    mirror: bool = True
    hand: str = "right"  # "right" | "left"


@dataclass
class MotionConfig:
    mode: str = "relative"  # "relative" | "absolute"
    sensitivity: float = 1.0
    deadzone: float = 0.065
    accel_low_gain: float = 0.4
    accel_high_gain: float = 2.5
    edge_overshoot: float = 0.05
    click_rollback_ms: int = 150
    min_cutoff: float = 1.0
    beta: float = 0.007


@dataclass
class GestureConfig:
    curl_down: float = 0.60
    curl_up: float = 0.80
    pinch_down: float = 0.035
    pinch_up: float = 0.055
    confirm_frames: int = 2
    toggle_hold_ms: int = 1000
    switcher_hold_ms: int = 300
    switcher_step: float = 0.6
    min_presence: float = 0.6
    palm_spread: float = 0.5
    palm_facing_min: float = 0.3


@dataclass
class AppConfig:
    auto_exit_minutes: int = 10
    low_power_after_s: float = 3.0
    sounds: bool = True


@dataclass
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    gesture: GestureConfig = field(default_factory=GestureConfig)
    app: AppConfig = field(default_factory=AppConfig)


_CHOICES = {
    ("camera", "hand"): ("right", "left"),
    ("motion", "mode"): ("relative", "absolute"),
}


def _coerce(section: str, f: dataclasses.Field, value):
    """แปลงชนิดค่าจากไฟล์ให้ตรงกับ field (int → float ได้, อย่างอื่นต้องตรง)"""
    default = f.default
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, float):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        value = float(value) if ok else value
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(default))
    if not ok:
        raise ValueError(f"[{section}] {f.name}: ต้องเป็น {type(default).__name__} ได้ {value!r}")
    choices = _CHOICES.get((section, f.name))
    if choices and value not in choices:
        raise ValueError(f"[{section}] {f.name}: ต้องเป็นหนึ่งใน {choices} ได้ {value!r}")
    return value


def from_dict(data: dict) -> Config:
    cfg = Config()
    for section, values in data.items():
        target = getattr(cfg, section, None)
        if not dataclasses.is_dataclass(target) or not isinstance(values, dict):
            log.warning("config: ไม่รู้จัก section [%s] ข้าม", section)
            continue
        fields = {f.name: f for f in dataclasses.fields(target)}
        for key, value in values.items():
            if key not in fields:
                log.warning("config: ไม่รู้จัก [%s] %s ข้าม", section, key)
                continue
            setattr(target, key, _coerce(section, fields[key], value))
    return cfg


def load(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not path.exists():
        log.warning("ไม่พบ %s ใช้ค่าเริ่มต้น", path)
        return Config()
    with path.open("rb") as fh:
        return from_dict(tomllib.load(fh))


def save(cfg: Config, path: Path = DEFAULT_CONFIG_PATH) -> None:
    # หมายเหตุ: tomli-w ไม่เก็บ comment เดิมในไฟล์
    with path.open("wb") as fh:
        tomli_w.dump(dataclasses.asdict(cfg), fh)
