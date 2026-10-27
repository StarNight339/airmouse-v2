"""Calibration: คำนวณเกณฑ์ท่ามือจากตัวอย่างที่เก็บจากมือผู้ใช้ (pure logic ไม่แตะ OS/Qt)

เก็บท่าละ ~3 วินาที แล้วแยกสองกลุ่ม (A ต้องสูงกว่า B):
lo = p5(A), hi = p95(B), g = lo - hi ; down = hi + 0.4g, up = hi + 0.7g
ค่าที่ได้ต้องสอดคล้องกับ predicate ใน gestures.py (is_fist, is_click_*, switcher_gap, is_flip)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .config import GestureConfig
from .features import HandFeatures
from .gestures import switcher_gap

MIN_SAMPLES = 15          # ท่าที่เห็นมือน้อยกว่านี้ใช้ไม่ได้
MIN_CURL_GAP = 0.02       # ช่องว่างระหว่างสองกลุ่มของ curl ที่ยอมรับ
MIN_PINCH_GAP = 0.004     # เมตร (ท่าพักกับท่าจีบห่างกันแค่ ~1 cm ก็แยกได้)
DOWN_FRAC = 0.4
UP_FRAC = 0.7
CURL_RANGE = (0.3, 0.98)
PINCH_RANGE = (0.005, 0.08)
MIN_FACING = 0.3          # |median facing| ของมือปกติต้องมากกว่านี้ถึงบอกด้านได้
MIN_THREE_SEPARATION = 0.06  # median gap ของท่า 3 นิ้ว ต้องสูงกว่า p99 มือปกติเท่านี้
# ตอน calibrate มือนิ่งกว่าตอนใช้จริง (ภาพเบลอตอนขยับ, ก้อยกระตุก) ค่า noise จึงต่ำเกินจริง:
# log 20261027 มือปกติ 3 วินิ่งได้ gap p99 = -0.004 แต่ใช้งานจริง 7,024 เฟรมได้ +0.07
# และเกณฑ์ที่คำนวณจากท่านิ่ง (on 0.097) ทำให้ switcher เปิดเองใน log เก่า 2 ครั้ง (ค่าจูนมือ: 0 ครั้ง)
REAL_USE_GAP_P99 = 0.07       # พื้นของ p99 gap มือปกติ (กัน switcher เปิดเอง)
PINKY_OPEN_MAX = 0.90         # เพดาน switcher_pinky_open: ใช้จริงก้อยมือปกติ p1 = 0.84–0.90

Key = tuple[str, str]


@dataclass(frozen=True)
class PoseSpec:
    id: str
    title: str
    text: str
    seconds: float = 3.0


POSES: list[PoseSpec] = [
    PoseSpec("relaxed", "มือปกติ",
             "ทำมือเหมือนตอนขยับเคอร์เซอร์ตามปกติ นิ้วทั้งห้าผ่อนคลาย หันไปทางกล้อง "
             "แล้วขยับมือไปมาช้าๆ และเร็วๆ เหมือนใช้งานจริง"),
    PoseSpec("fold_index", "พับนิ้วชี้",
             "พับนิ้วชี้ลงเหมือนกดคลิกซ้าย นิ้วอื่นเหยียดตามปกติ แล้วค้างไว้"),
    PoseSpec("fold_middle", "พับนิ้วกลาง",
             "พับนิ้วกลางลงเหมือนคลิกขวา นิ้วชี้ยังเหยียดอยู่ แล้วค้างไว้"),
    PoseSpec("pinch", "จีบนิ้ว",
             "จีบปลายนิ้วโป้งกับนิ้วชี้ให้ชิดกัน (ท่าลาก) แล้วค้างไว้"),
    PoseSpec("fist", "กำมือ",
             "กำมือแน่นทั้งสี่นิ้ว (ท่าพักเคอร์เซอร์) แล้วค้างไว้"),
    PoseSpec("three", "สามนิ้ว",
             "ชูนิ้วชี้ กลาง และนางขึ้น พับนิ้วก้อยลง (ท่าเปิดตัวสลับหน้าต่าง) แล้วค้างไว้"),
    PoseSpec("flip", "พลิกมือ",
             "พลิกมือให้อีกด้านหนึ่งหันเข้าหากล้อง (ท่าเปิด/ปิดระบบ) นิ้วเหยียดตรง แล้วค้างไว้"),
]
POSE_IDS = [p.id for p in POSES]

# ค่าที่ calibrate ได้ (section ทั้งหมดคือ gesture) ใช้ตรวจ/แสดงผล
RESULT_KEYS: list[Key] = [
    ("gesture", k) for k in (
        "click_curl_down", "click_curl_up", "curl_down", "curl_up",
        "pinch_down", "pinch_up", "switcher_gap_on", "switcher_gap_hold",
        "switcher_pinky_open", "normal_facing", "flip_facing_min")
]


@dataclass(frozen=True)
class CalibrationResult:
    values: dict[Key, float | int] = field(default_factory=dict)
    problems: dict[str, str] = field(default_factory=dict)   # pose id -> ข้อความไทย
    stats: dict[str, dict[str, float]] = field(default_factory=dict)  # pose id -> สรุปค่า


# ---------------------------------------------------------------- สถิติพื้นฐาน
def percentile(vals: Sequence[float], q: float) -> float:
    """percentile แบบ interpolate เชิงเส้น (q = 0..100)"""
    s = sorted(vals)
    if not s:
        raise ValueError("percentile ของลิสต์ว่าง")
    k = (len(s) - 1) * q / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def median(vals: Sequence[float]) -> float:
    return percentile(vals, 50)


def _sign(v: float) -> int:
    return 1 if v > 0 else -1 if v < 0 else 0


def split_thresholds(a: Sequence[float], b: Sequence[float],
                     min_gap: float = MIN_CURL_GAP) -> tuple[float, float, float] | None:
    """A (สูงกว่า) กับ B (ต่ำกว่า) -> (down, up, g) หรือ None ถ้าแยกไม่ออก (g < min_gap)"""
    lo = percentile(a, 5)
    hi = percentile(b, 95)
    g = lo - hi
    if g < min_gap:
        return None
    return hi + DOWN_FRAC * g, hi + UP_FRAC * g, g


def _stats(frames: Sequence[HandFeatures]) -> dict[str, float]:
    if not frames:
        return {"n": 0}
    out: dict[str, float] = {"n": len(frames)}
    for name in ("index", "middle", "ring", "pinky"):
        out[f"{name}_med"] = round(median([f.curl[name] for f in frames]), 3)
    out["pinch_med"] = round(median([f.pinch for f in frames]), 4)
    out["facing_med"] = round(median([f.palm_facing for f in frames]), 3)
    out["gap_med"] = round(median([switcher_gap(f.curl) for f in frames]), 3)
    return out


# ---------------------------------------------------------------- compute
def compute(samples: dict[str, list[HandFeatures]], current: GestureConfig) -> CalibrationResult:
    problems: dict[str, str] = {}
    values: dict[Key, float | int] = {}
    stats = {pid: _stats(samples.get(pid, [])) for pid in POSE_IDS}

    def problem(pose: str, msg: str) -> None:
        problems[pose] = f"{problems[pose]}; {msg}" if pose in problems else msg

    usable: dict[str, list[HandFeatures]] = {}
    for pid in POSE_IDS:
        frames = samples.get(pid, [])
        if len(frames) < MIN_SAMPLES:
            problem(pid, "เห็นมือน้อยเกินไป")
        else:
            usable[pid] = frames

    def curls(pid: str, name: str) -> list[float]:
        return [f.curl[name] for f in usable[pid]]

    # ---- click_curl: ชี้ (relaxed vs fold_index) และกลาง (relaxed vs fold_middle) ----
    click_downs: list[float] = []
    click_ups: list[float] = []
    click_los: list[float] = []
    click_ok = "relaxed" in usable
    for pose, finger in (("fold_index", "index"), ("fold_middle", "middle")):
        if not click_ok or pose not in usable:
            click_ok = False
            continue
        a = curls("relaxed", finger)
        b = curls(pose, finger)
        res = split_thresholds(a, b)
        if res is None:
            problem(pose, "แยกท่าไม่ออก ทำท่านี้ใหม่ (พับให้สุดกว่านี้)")
            click_ok = False
            continue
        click_downs.append(res[0])
        click_ups.append(res[1])
        click_los.append(percentile(a, 5))
    click: tuple[float, float] | None = None
    if click_ok and len(click_downs) == 2:
        click = (min(click_downs), min(min(click_ups), min(click_los)))

    # ---- curl_down/up: กำมือ ----
    fist: tuple[float, float] | None = None
    if "relaxed" in usable and "fist" in usable:
        a = [min(f.curl["index"], f.curl["middle"]) for f in usable["relaxed"]]
        b = [max(f.curl[n] for n in ("index", "middle", "ring", "pinky")) for f in usable["fist"]]
        res = split_thresholds(a, b)
        if res is None:
            problem("fist", "แยกท่าไม่ออก ทำท่านี้ใหม่ (กำให้แน่นกว่านี้)")
        else:
            fist = (res[0], res[1])

    # ---- pinch (เมตร) ----
    pinch: tuple[float, float] | None = None
    if "relaxed" in usable and "pinch" in usable:
        res = split_thresholds([f.pinch for f in usable["relaxed"]],
                               [f.pinch for f in usable["pinch"]], MIN_PINCH_GAP)
        if res is None:
            problem("pinch", "แยกท่าไม่ออก ทำท่านี้ใหม่ (จีบให้ชิดกว่านี้)")
        else:
            pinch = (res[0], res[1])

    # ---- สอดคล้องกัน: click_down > curl_down, ค่าอยู่ในช่วงสมเหตุสมผล ----
    def in_range(pair: tuple[float, float], rng: tuple[float, float]) -> bool:
        return all(rng[0] <= v <= rng[1] for v in pair) and pair[0] < pair[1]

    if click is not None and not in_range(click, CURL_RANGE):
        problem("fold_index", "ค่าคลิกที่ได้อยู่นอกช่วงที่เหมาะสม ทำท่าพับนิ้วใหม่")
        click = None
    if fist is not None and not in_range(fist, CURL_RANGE):
        problem("fist", "ค่ากำมือที่ได้อยู่นอกช่วงที่เหมาะสม ทำท่ากำมือใหม่")
        fist = None
    if pinch is not None and not in_range(pinch, PINCH_RANGE):
        problem("pinch", "ค่าจีบที่ได้อยู่นอกช่วงที่เหมาะสม ทำท่าจีบใหม่")
        pinch = None
    # คลิกต้องพับน้อยกว่ากำมือ: เทียบเมื่อได้ทั้งสองค่าใหม่ (ค่าเก่ากับค่าใหม่ไม่ใช่ความผิดของท่าใดท่าหนึ่ง)
    if click is not None and fist is not None and click[0] <= fist[0]:
        problem("fist", "กำมือกับพับนิ้วคลิกได้ค่าใกล้กันเกินไป (คลิกต้องพับน้อยกว่ากำมือ) ทำใหม่")
        click = fist = None

    if click is not None:
        values[("gesture", "click_curl_down")] = round(click[0], 3)
        values[("gesture", "click_curl_up")] = round(click[1], 3)
    if fist is not None:
        values[("gesture", "curl_down")] = round(fist[0], 3)
        values[("gesture", "curl_up")] = round(fist[1], 3)
    if pinch is not None:
        values[("gesture", "pinch_down")] = round(pinch[0], 4)
        values[("gesture", "pinch_up")] = round(pinch[1], 4)

    # ---- switcher (3 นิ้ว) ----
    if "relaxed" in usable:
        r_gaps = [switcher_gap(f.curl) for f in usable["relaxed"]]
        r99 = max(percentile(r_gaps, 99), REAL_USE_GAP_P99)
        if "three" in usable:
            t50 = median([switcher_gap(f.curl) for f in usable["three"]])
            if t50 - r99 < MIN_THREE_SEPARATION:
                problem("three", "แยกท่าสามนิ้วไม่ออก ทำท่านี้ใหม่ (พับก้อยให้ชัด ชูชี้ กลาง นางตรง)")
            else:
                on = (t50 + r99) / 2
                hold = r99 + 0.25 * (on - r99)
                values[("gesture", "switcher_gap_on")] = round(on, 3)
                values[("gesture", "switcher_gap_hold")] = round(hold, 3)
        pinky_open = min(percentile(curls("relaxed", "pinky"), 5) - 0.02, PINKY_OPEN_MAX)
        if CURL_RANGE[0] <= pinky_open <= CURL_RANGE[1]:
            values[("gesture", "switcher_pinky_open")] = round(pinky_open, 3)
        else:
            problem("relaxed", "ค่านิ้วก้อยตอนมือปกติผิดปกติ ทำท่านี้ใหม่")

    # ---- ด้านปกติของมือ / พลิกมือ ----
    normal = current.normal_facing if current.normal_facing in (-1, 1) else 0
    if "relaxed" in usable:
        med = median([f.palm_facing for f in usable["relaxed"]])
        if abs(med) < MIN_FACING:
            problem("relaxed", "บอกด้านของมือไม่ได้ ทำท่านี้ใหม่ (หันฝ่ามือเข้าหากล้องให้ชัด)")
        else:
            normal = _sign(med)
            values[("gesture", "normal_facing")] = normal
    if "flip" in usable:
        facings = [f.palm_facing for f in usable["flip"]]
        fmed = median(facings)
        if normal != 0 and _sign(fmed) == normal:
            problem("flip", "ด้านที่พลิกเหมือนด้านปกติ ทำท่านี้ใหม่ (พลิกมือให้สุด)")
        else:
            lim = 0.7 * percentile([abs(v) for v in facings], 10)
            values[("gesture", "flip_facing_min")] = round(min(0.5, max(0.3, lim)), 3)

    return CalibrationResult(values, problems, stats)


# ---------------------------------------------------------------- สรุปสำหรับตาราง UI
def _fmt(v: object) -> str:
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def summary_rows(result: CalibrationResult, current: GestureConfig) -> list[tuple[str, str, str]]:
    """(key, ค่าปัจจุบัน, ค่าใหม่) เรียงตาม RESULT_KEYS"""
    rows: list[tuple[str, str, str]] = []
    for sec, key in RESULT_KEYS:
        if (sec, key) in result.values:
            rows.append((key, _fmt(getattr(current, key)), _fmt(result.values[(sec, key)])))
    return rows


def samples_to_jsonable(samples: dict[str, list[HandFeatures]]) -> dict[str, list[dict]]:
    """ค่าดิบต่อเฟรม สำหรับ dump ลง logs/calib-*.json"""
    return {
        pid: [{"curl": {k: round(v, 4) for k, v in f.curl.items()},
               "pinch": round(f.pinch, 5), "palm_facing": round(f.palm_facing, 4),
               "spread": round(f.spread, 4), "hand_size": round(f.hand_size, 4)}
              for f in frames]
        for pid, frames in samples.items()
    }


# ---------------------------------------------------------------- state machine ของขั้นตอน
PREPARE_S = 3.0


class CalibrationSession:
    """ขั้นตอนเก็บท่าทีละท่า (pure, ส่งเวลาเข้ามาเอง จึงเทสต์ได้โดยไม่ต้องมี widget)

    phase: "prepare" (นับถอยหลัง) -> "capture" (เก็บ) -> ท่าถัดไป ... -> "review"
    redo(pose) จากหน้าสรุป: ทำเฉพาะท่านั้นแล้วกลับ "review"
    """

    def __init__(self, poses: Sequence[PoseSpec] = POSES, prepare_s: float = PREPARE_S) -> None:
        self.poses = list(poses)
        self.prepare_s = prepare_s
        self.samples: dict[str, list[HandFeatures]] = {p.id: [] for p in self.poses}
        self.index = 0
        self.phase = "prepare"
        self._phase_t0: float | None = None
        self._last_seq: int | None = None
        self._single = False   # redo ท่าเดียว

    @property
    def current(self) -> PoseSpec:
        return self.poses[min(self.index, len(self.poses) - 1)]

    @property
    def finished(self) -> bool:
        return self.phase == "review"

    def _begin(self, phase: str, now: float) -> None:
        self.phase = phase
        self._phase_t0 = now

    def restart_prepare(self, now: float) -> None:
        """เริ่มนับถอยหลังของท่าปัจจุบันใหม่ (ล้างตัวอย่างของท่านี้)"""
        self.samples[self.current.id] = []
        self._last_seq = None
        self._begin("prepare", now)

    def redo(self, pose_id: str, now: float) -> None:
        self.index = next(i for i, p in enumerate(self.poses) if p.id == pose_id)
        self._single = self.phase == "review"
        self.restart_prepare(now)

    def elapsed(self, now: float) -> float:
        return 0.0 if self._phase_t0 is None else max(0.0, now - self._phase_t0)

    def countdown(self, now: float) -> float:
        """วินาทีที่เหลือของ prepare (0 เมื่อไม่ใช่ prepare)"""
        if self.phase != "prepare":
            return 0.0
        return max(0.0, self.prepare_s - self.elapsed(now))

    def progress(self, now: float) -> float:
        """ความคืบหน้าการเก็บ 0..1"""
        if self.phase == "review":
            return 1.0
        if self.phase != "capture":
            return 0.0
        return min(1.0, self.elapsed(now) / self.current.seconds)

    def feed(self, seq: int, feats: HandFeatures | None) -> bool:
        """รับเฟรมจาก inference; นับเฉพาะ seq ใหม่ที่เห็นมือ ขณะ capture; คืน True ถ้าเก็บเฟรมนี้"""
        if seq == self._last_seq:
            return False
        self._last_seq = seq
        if self.phase != "capture" or feats is None:
            return False
        self.samples[self.current.id].append(feats)
        return True

    def tick(self, now: float) -> bool:
        """เดินเวลา; คืน True ถ้าเปลี่ยน phase/ท่า"""
        if self.phase == "prepare":
            if self.elapsed(now) >= self.prepare_s:
                self._begin("capture", now)
                return True
        elif self.phase == "capture":
            if self.elapsed(now) >= self.current.seconds:
                if self._single or self.index + 1 >= len(self.poses):
                    self.phase = "review"
                    self._phase_t0 = None
                else:
                    self.index += 1
                    self._last_seq = None
                    self._begin("prepare", now)
                return True
        return False
