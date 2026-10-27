"""โหลด/บันทึก config.toml เป็น dataclass (ค่าไหนไม่มีในไฟล์ใช้ค่าเริ่มต้น)"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

log = logging.getLogger(__name__)

def project_root(frozen: bool, executable: str, module_file: str) -> Path:
    """.exe (PyInstaller one-folder): config.toml, models/, logs/, assets/ อยู่ข้าง exe
    ไม่งั้นเป็น root ของ repo (src/airmouse/config.py → ขึ้นไปสองชั้น)"""
    if frozen:
        return Path(executable).resolve().parent
    return Path(module_file).resolve().parents[2]


PROJECT_ROOT = project_root(getattr(sys, "frozen", False), sys.executable, __file__)
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
    click_rollback_ms: int = 300
    min_cutoff: float = 1.0
    beta: float = 0.007
    scroll_speed: float = 2400.0


@dataclass
class GestureConfig:
    curl_down: float = 0.70
    curl_up: float = 0.82
    click_curl_down: float = 0.77
    click_curl_up: float = 0.84
    pinch_down: float = 0.022
    pinch_up: float = 0.033
    confirm_frames: int = 2
    toggle_hold_ms: int = 700
    switcher_hold_ms: int = 300
    switcher_step: float = 0.4  # hand-size ต่อการ์ด (ช่วงมือสบาย ~2.0 → ถึงการ์ดที่ 5+)
    switcher_gap_on: float = 0.15    # เข้า: min(ชี้,กลาง,นาง) - ก้อย ต้องเกินนี้
    switcher_gap_hold: float = 0.08  # ค้างท่า: gap ต้องเกินนี้จึงนับว่ายังอยู่ในท่า
    switcher_pinky_open: float = 0.88  # ก้อยเหยียดเกินนี้ (และ gap ต่ำ) = เลิกท่า
    switcher_swipe_speed: float = 6.0  # สะบัดขึ้น/ลง: |vy| เกินนี้ (hand-size/s)
    min_presence: float = 0.6
    flip_facing_min: float = 0.5
    normal_facing: int = 0  # ด้านปกติของมือ: -1 / +1 หรือ 0 = เรียนรู้เอง
    scroll_curl_max: float = 0.78
    scroll_extend_min: float = 0.75


@dataclass
class AppConfig:
    auto_exit_minutes: float = 10.0
    low_power_after_s: float = 3.0
    low_power_fps: float = 6.0
    sounds: bool = True
    overlay: bool = True


@dataclass
class SnapConfig:
    enabled: bool = True
    capture_px: float = 24.0     # ขยายกรอบปุ่มออกเท่านี้ (px) = ระยะที่เริ่มเกาะ
    search_px: float = 48.0      # รัศมีจุดค้นหาปุ่มรอบ cursor (px)
    friction: float = 0.35       # บนปุ่ม การขยับ cursor เหลือสัดส่วนนี้ (หนืด)
    pull_time_s: float = 0.12    # เวลาดูดเข้ากลางปุ่ม (ค่าคงที่ exponential)
    slow_speed: float = 0.8      # มือช้ากว่านี้ (hand-size/s) จึงดูด
    escape_speed: float = 2.0    # มือเร็วกว่านี้ ไม่หนืด (สะบัดหลุด)
    uia: bool = True             # ใช้ UI Automation หาปุ่มในโปรแกรม (ไม่ใช่แค่ปุ่มมุมหน้าต่าง)


@dataclass
class SwitcherConfig:
    enabled: bool = True         # ท่า 3 นิ้วเปิดตัวสลับหน้าต่าง
    preview: bool = True         # แสดงภาพตัวอย่างหน้าต่างในการ์ด


@dataclass
class MetaConfig:
    calibrated: str = ""         # เวลา (ISO) ที่ calibrate ล่าสุด; ว่าง = ยังไม่เคย


@dataclass
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    gesture: GestureConfig = field(default_factory=GestureConfig)
    app: AppConfig = field(default_factory=AppConfig)
    snap: SnapConfig = field(default_factory=SnapConfig)
    switcher: SwitcherConfig = field(default_factory=SwitcherConfig)
    meta: MetaConfig = field(default_factory=MetaConfig)


_CHOICES = {
    ("camera", "hand"): ("right", "left"),
    ("motion", "mode"): ("relative", "absolute"),
}


# ขอบล่างของค่าที่ใช้หาร/จับเวลา: (section, key) -> (ค่าต่ำสุด, ต้องมากกว่า (True) หรือเท่ากับก็ได้ (False))
_MINIMA = {
    ("gesture", "switcher_step"): (0.0, True),
    ("gesture", "switcher_hold_ms"): (0, False),
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
    low = _MINIMA.get((section, f.name))
    if low is not None and not (value > low[0] if low[1] else value >= low[0]):
        raise ValueError(f"[{section}] {f.name}: ต้อง{'มากกว่า' if low[1] else 'ไม่น้อยกว่า'} "
                         f"{low[0]} ได้ {value!r}")
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


# ---------------------------------------------------------------- แก้ค่าใน TOML โดยเก็บ comment
_SECTION_RE = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        s = repr(value)
        return s if ("." in s or "e" in s or "n" in s) else s + ".0"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # basic string ของ TOML escape เหมือน JSON
    raise TypeError(f"รูปแบบค่าไม่รองรับ: {value!r}")


def _split_eol(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _value_end(s: str, start: int) -> int:
    """ตำแหน่งท้าย token ของค่าที่เริ่มที่ start (รองรับสตริงที่มี # หรือ \\" ข้างใน)"""
    if start >= len(s):
        return start
    ch = s[start]
    if ch == '"':
        i = start + 1
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == '"':
                return i + 1
            i += 1
        return len(s)
    if ch == "'":
        j = s.find("'", start + 1)
        return len(s) if j < 0 else j + 1
    i = start
    while i < len(s) and not s[i].isspace() and s[i] != "#":
        i += 1
    return i


def _apply_one(lines: list[str], eol: str, section: str, key: str, token: str) -> list[str]:
    key_re = re.compile(r"^(\s*)" + re.escape(key) + r"(\s*=\s*)")
    cur: str | None = None
    sec_start = -1   # index บรรทัดหลัง header ของ section ที่ต้องการ
    last_content = -1  # บรรทัดสุดท้ายที่ไม่ว่างใน section นั้น
    for i, line in enumerate(lines):
        body, _ = _split_eol(line)
        m = _SECTION_RE.match(body)
        if m:
            if cur == section:
                break  # เจอ header ถัดไป จบ section
            cur = m.group(1)
            if cur == section:
                sec_start = i + 1
                last_content = i
            continue
        if cur != section:
            continue
        if body.strip():
            last_content = i
        km = key_re.match(body)
        if km:
            end = _value_end(body, km.end())
            lines[i] = body[:km.end()] + token + body[end:] + line[len(body):]
            return lines
    if sec_start >= 0:  # มี section แต่ไม่มี key: เพิ่มท้าย section (ก่อนบรรทัดว่างท้าย)
        prev_body, prev_eol = _split_eol(lines[last_content])
        if not prev_eol:
            lines[last_content] = prev_body + eol
            lines.insert(last_content + 1, f"{key} = {token}")
        else:
            lines.insert(last_content + 1, f"{key} = {token}{eol}")
        return lines
    # ไม่มี section: เพิ่มท้ายไฟล์
    if lines:
        last_body, last_eol = _split_eol(lines[-1])
        trailing = bool(last_eol)
        if not last_eol:
            lines[-1] = last_body + eol
        if last_body.strip():
            lines.append(eol)
    else:
        trailing = True
    lines.append(f"[{section}]{eol}")
    lines.append(f"{key} = {token}" + (eol if trailing else ""))
    return lines


def update_toml_text(text: str, updates: dict[tuple[str, str], object]) -> str:
    """แก้เฉพาะ token ค่าของ key ที่ระบุ เก็บ indentation ช่องว่างและ comment เดิมไว้ (pure)
    ไม่มี key → เพิ่มท้าย section ไม่มี section → เพิ่มท้ายไฟล์; คงรูปแบบขึ้นบรรทัดใหม่ของไฟล์เดิม"""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    for (section, key), value in updates.items():
        lines = _apply_one(lines, eol, section, key, _format_value(value))
    return "".join(lines)


def update_values(path: Path, updates: dict[tuple[str, str], object]) -> None:
    """เขียนค่าที่เปลี่ยนลงไฟล์ config โดยเก็บ comment: ตรวจความถูกต้องก่อน (error = ไม่เขียนอะไร)
    สำรองไฟล์เดิมเป็น <ชื่อ>.bak แล้วเขียนแบบ atomic (ไฟล์ชั่วคราว + os.replace)"""
    fields = {s.name: {f.name for f in dataclasses.fields(getattr(Config(), s.name))}
              for s in dataclasses.fields(Config)}
    for section, key in updates:
        if key not in fields.get(section, ()):
            raise ValueError(f"ไม่รู้จัก [{section}] {key}")
    raw = path.read_bytes() if path.exists() else b""
    new = update_toml_text(raw.decode("utf-8"), updates)
    from_dict(tomllib.loads(new))  # ค่าผิดชนิด/นอกช่วง → raise ก่อนเขียน
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(new.encode("utf-8"))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
