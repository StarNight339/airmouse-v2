from airmouse.config import GestureConfig
from airmouse.features import HandFeatures
from airmouse.gestures import GestureTracker


def _f(curls=(1.0, 1.0, 1.0, 1.0, 1.0), spread=1.2, facing=0.9) -> HandFeatures:
    names = ("thumb", "index", "middle", "ring", "pinky")
    return HandFeatures(curl=dict(zip(names, curls)), pinch=0.1, hand_size=0.2,
                        anchor=(0.5, 0.5), spread=spread, palm_facing=facing)


PALM = _f()
FIST = _f(curls=(0.5, 0.3, 0.3, 0.3, 0.3), spread=0.3)
RELAXED = _f(curls=(0.9, 0.7, 0.7, 0.7, 0.7), spread=0.5)  # mean 0.7 อยู่ระหว่าง down/up
OPEN_IDX = _f(curls=(0.9, 0.9, 0.9, 0.9, 0.9), spread=0.3)


def _run(t: GestureTracker, feats, t0: float, t1: float, step: float = 0.01):
    """ป้อนเฟรมซ้ำตั้งแต่ t0 ถึง t1 (รวม) คืน (ts สุดท้าย, events ทั้งหมด, state สุดท้าย)"""
    events: list[str] = []
    n = round((t1 - t0) / step)
    st = None
    for i in range(n + 1):
        st = t.update(feats, t0 + i * step)
        events += st.events
    return t0 + n * step, events, st


def _enabled() -> tuple[GestureTracker, float]:
    t = GestureTracker(GestureConfig())
    ts, ev, _ = _run(t, PALM, 0.0, 1.05)
    assert ev == ["toggle_on"]
    ts, _, _ = _run(t, RELAXED, ts + 0.01, ts + 0.1)  # ปล่อยฝ่ามือ
    return t, ts


def test_starts_disabled():
    st = GestureTracker(GestureConfig()).update(None, 0.0)
    assert not st.enabled and not st.clutch


def test_toggle_requires_full_hold():
    t = GestureTracker(GestureConfig())
    _, ev, st = _run(t, PALM, 0.0, 0.95)
    assert ev == [] and not st.enabled
    assert 0.8 < st.toggle_progress < 1.0
    _, ev, st = _run(t, PALM, 0.96, 1.1)
    assert ev == ["toggle_on"] and st.enabled


def test_no_repeat_while_held_then_toggle_off():
    t = GestureTracker(GestureConfig())
    ts, ev, _ = _run(t, PALM, 0.0, 3.5)
    assert ev == ["toggle_on"]  # ค้างต่อไม่ toggle ซ้ำ
    ts, ev, st = _run(t, RELAXED, ts + 0.01, ts + 0.1)
    assert ev == [] and st.enabled
    _, ev, st = _run(t, PALM, ts + 0.01, ts + 1.2)
    assert ev == ["toggle_off"] and not st.enabled


def test_progress_grows_from_zero_to_one():
    t = GestureTracker(GestureConfig())
    prog = []
    for i in range(110):
        prog.append(t.update(PALM, i * 0.01).toggle_progress)
    assert prog[0] == 0.0
    mid = prog[:100]
    assert all(b >= a for a, b in zip(mid, mid[1:]))
    assert 0.9 < prog[99] <= 1.0
    assert prog[-1] == 0.0  # toggle แล้ว progress กลับเป็น 0


def test_bad_palm_does_not_count():
    t = GestureTracker(GestureConfig())
    for feats in (_f(spread=0.3), _f(facing=0.0), OPEN_IDX):
        _, ev, st = _run(t, feats, 0.0, 2.0)
        assert ev == [] and not st.open_palm


def test_interrupted_hold_restarts():
    t = GestureTracker(GestureConfig())
    _run(t, PALM, 0.0, 0.8)
    _run(t, RELAXED, 0.81, 0.9)
    _, ev, _ = _run(t, PALM, 0.91, 1.5)
    assert ev == []


def test_clutch_needs_confirm_and_hysteresis():
    t, ts = _enabled()
    st = t.update(FIST, ts + 0.01)
    assert not st.clutch  # เฟรมแรกยังไม่ยืนยัน
    st = t.update(FIST, ts + 0.02)
    assert st.clutch and st.events == ("clutch_on",)
    # ค่ากลางระหว่าง down/up ยังคง clutch (hysteresis)
    for i in range(5):
        assert t.update(RELAXED, ts + 0.03 + i * 0.01).clutch
    st = t.update(OPEN_IDX, ts + 0.1)
    assert st.clutch  # เฟรมแรกยังไม่พอ
    st = t.update(OPEN_IDX, ts + 0.11)
    assert not st.clutch and st.events == ("clutch_off",)


def test_clutch_false_while_disabled():
    t = GestureTracker(GestureConfig())
    for i in range(10):
        st = t.update(FIST, i * 0.01)
    assert not st.clutch and "clutch_on" not in st.events


def test_palm_releases_clutch_before_toggle_off():
    # toggle ขณะ clutch เข้าไม่ถึงโดยโครงสร้าง: ฝ่ามือทำให้ค่า curl เฉลี่ยสูง → clutch หลุดก่อนครบเวลา hold
    t, ts = _enabled()
    _run(t, FIST, ts + 0.01, ts + 0.05)
    assert t.clutch
    ts2, ev, st = _run(t, PALM, ts + 0.1, ts + 0.2)
    assert "clutch_off" in ev and not st.clutch and st.enabled
    _, ev, st = _run(t, PALM, ts2 + 0.01, ts2 + 1.2)
    assert ev == ["toggle_off"] and not st.clutch


def test_one_frame_dropout_does_not_rearm_toggle():
    t = GestureTracker(GestureConfig())
    ts, ev, _ = _run(t, PALM, 0.0, 1.05)
    assert ev == ["toggle_on"]
    t.update(None, ts + 0.01)  # dropout 1 เฟรม
    _, ev, st = _run(t, PALM, ts + 0.02, ts + 2.5)
    assert ev == [] and st.enabled


def test_long_loss_clears_release_requirement():
    t = GestureTracker(GestureConfig())
    ts, ev, _ = _run(t, PALM, 0.0, 1.05)
    t.update(None, ts + 0.01)
    t.update(None, ts + 0.8)
    _, ev, _ = _run(t, PALM, ts + 0.81, ts + 2.0)
    assert ev == ["toggle_off"]


def test_progress_clamped_when_time_goes_backwards():
    t = GestureTracker(GestureConfig())
    for i in range(10):
        t.update(PALM, 5.0 + i * 0.01)
    st = t.update(PALM, 4.0)
    assert 0.0 <= st.toggle_progress <= 1.0
    _, ev, _ = _run(t, PALM, 4.01, 4.5)
    assert ev == []  # นับใหม่จาก ts ที่ย้อน ไม่ toggle ก่อนครบ 1 s


def test_hand_lost_resets_hold_and_clutch():
    t = GestureTracker(GestureConfig())
    _run(t, PALM, 0.0, 0.8)
    st = t.update(None, 0.81)
    assert st.toggle_progress == 0.0
    _, ev, _ = _run(t, PALM, 0.82, 1.5)
    assert ev == []  # ต้องนับเวลาใหม่

    t, ts = _enabled()
    _run(t, FIST, ts + 0.01, ts + 0.05)
    assert t.clutch
    st = t.update(None, ts + 0.1)
    assert st.events == ("clutch_off",) and not st.clutch and st.enabled


# ===================== Phase 3: click / drag / scroll =====================
import itertools

import pytest

FPS = [10, 30]
EXT = 0.95


def _g(idx=EXT, mid=EXT, ring=EXT, pinky=EXT, pinch=0.1, spread=0.3, facing=0.9) -> HandFeatures:
    f = _f(curls=(0.9, idx, mid, ring, pinky), spread=spread, facing=facing)
    return HandFeatures(curl=f.curl, pinch=pinch, hand_size=f.hand_size, anchor=f.anchor,
                        spread=f.spread, palm_facing=f.palm_facing)


class Rig:
    """tracker ที่ enable แล้ว + ป้อนเฟรมที่ fps ที่กำหนด เก็บ event พร้อม ts"""

    def __init__(self, fps: int) -> None:
        self.fps = fps
        self.dt = 1 / fps
        self.t = GestureTracker(GestureConfig())
        self.ts = 0.0
        self.log: list[tuple[float, str]] = []
        self.states: list = []
        for _ in range(int(1.2 * fps) + 2):  # ฝ่ามือค้างเกิน 1 s
            self.feed(PALM)
        assert [e for _, e in self.log] == ["toggle_on"]
        self.log.clear()
        self.feed_n(_g(), 3)  # เหยียดนิ้ว → armed

    def feed(self, feats):
        self.ts += self.dt
        st = self.t.update(feats, self.ts)
        self.states.append(st)
        self.log += [(self.ts, e) for e in st.events]
        return st

    def feed_n(self, feats, n: int):
        st = None
        for _ in range(n):
            st = self.feed(feats)
        return st

    def events(self) -> list[str]:
        return [e for _, e in self.log]

    def count(self, name: str) -> int:
        return self.events().count(name)


def _ramp(a: float, b: float, n: int) -> list[float]:
    """n ค่าจาก a ไป b (ไม่รวม a, รวม b)"""
    return [a + (b - a) * (i + 1) / n for i in range(n)]


def _bend_frames(fps: int) -> int:
    return 3 if fps <= 10 else 8  # พับนิ้วใช้ ~0.3 s


@pytest.mark.parametrize("fps", FPS)
def test_single_bend_gives_one_left_click_with_bend_ts(fps):
    r = Rig(fps)
    n = _bend_frames(fps)
    first_below_up = None
    for v in _ramp(EXT, 0.3, n):
        r.feed(_g(idx=v))
        if first_below_up is None and v < 0.8:
            first_below_up = r.ts
    r.feed_n(_g(idx=0.3), 2 * fps)  # ค้างพับนาน ๆ ไม่คลิกซ้ำ
    assert r.events() == ["click_left"]
    click_state = next(s for s in r.states if "click_left" in s.events)
    assert click_state.click_bend_ts == pytest.approx(first_below_up)
    assert click_state.mode == "move"
    r.feed_n(_g(), 3)
    assert r.count("click_left") == 1


@pytest.mark.parametrize("fps", FPS)
def test_two_quick_bends_give_two_clicks(fps):
    r = Rig(fps)
    n = _bend_frames(fps)
    for _ in range(2):
        for v in _ramp(EXT, 0.3, n):
            r.feed(_g(idx=v))
        for v in _ramp(0.3, EXT, max(1, n // 2)):
            r.feed(_g(idx=v))
    r.feed_n(_g(), 2)
    assert r.count("click_left") == 2


@pytest.mark.parametrize("fps", FPS)
def test_pinch_gives_drag_not_click(fps):
    r = Rig(fps)
    r.feed_n(_g(idx=0.7, pinch=0.02), 5)
    assert r.events() == ["drag_start"]
    assert r.states[-1].mode == "drag" and r.states[-1].pinch_progress == 1.0
    # ระหว่างลาก นิ้วชี้พับก็ไม่คลิก
    r.feed_n(_g(idx=0.3, pinch=0.02), 5)
    assert "click_left" not in r.events()
    r.feed_n(_g(idx=EXT, pinch=0.1), 3)
    assert r.events() == ["drag_start", "drag_end"]
    assert r.states[-1].mode == "move"


@pytest.mark.parametrize("fps", FPS)
def test_pinch_hysteresis_keeps_drag(fps):
    r = Rig(fps)
    r.feed_n(_g(pinch=0.02), 4)
    r.feed_n(_g(pinch=0.045), 6)  # อยู่ระหว่าง down/up
    assert r.states[-1].mode == "drag"
    assert r.events() == ["drag_start"]


@pytest.mark.parametrize("fps", FPS)
def test_left_click_needs_rearm_after_drag(fps):
    r = Rig(fps)
    r.feed_n(_g(pinch=0.02), 4)
    r.feed_n(_g(idx=0.3, pinch=0.1), 6)  # ปล่อยจีบแต่ชี้ยังพับ
    assert r.events() == ["drag_start", "drag_end"]
    r.feed_n(_g(), 3)  # เหยียด = armed
    r.feed_n(_g(idx=0.3), 3)
    assert r.count("click_left") == 1


@pytest.mark.parametrize("fps", FPS)
@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_natural_fist_close_never_clicks(fps, order):
    # order[i] = ลำดับที่นิ้ว i เริ่มพับ เหลื่อมกันลำดับละ 40 ms (รวม 120 ms) (กำมือธรรมชาติ)
    r = Rig(fps)
    n = _bend_frames(fps)
    for step in range(n + 4):
        curls = []
        for finger in range(4):
            k = step - order[finger] * 0.04 * fps  # เฟรมที่ผ่านไปนับจากเริ่มพับของนิ้วนี้
            curls.append(EXT if k <= 0 else max(0.3, EXT - (EXT - 0.3) * k / n))
        r.feed(_g(*curls))
    r.feed_n(_g(0.3, 0.3, 0.3, 0.3), 4)
    ev = r.events()
    assert "click_left" not in ev and "click_right" not in ev
    assert "clutch_on" in ev


@pytest.mark.parametrize("fps", FPS)
def test_scroll_pose_start_end_and_no_click(fps):
    r = Rig(fps)
    n = _bend_frames(fps)
    for v in _ramp(EXT, 0.3, n):  # นางกับก้อยพับ โดยชี้/กลางเหยียด
        r.feed(_g(ring=v, pinky=v))
    r.feed_n(_g(ring=0.3, pinky=0.3), 5)
    assert r.events() == ["scroll_start"]
    assert r.states[-1].mode == "scroll"
    r.feed_n(_g(), 4)
    assert r.events() == ["scroll_start", "scroll_end"]
    assert r.count("click_left") == 0 and r.count("click_right") == 0


@pytest.mark.parametrize("fps", FPS)
def test_right_click_needs_index_extended(fps):
    r = Rig(fps)
    n = _bend_frames(fps)
    for v in _ramp(EXT, 0.3, n):
        r.feed(_g(mid=v))
    r.feed_n(_g(mid=0.3), 3)
    assert r.events() == ["click_right"]
    st = next(s for s in r.states if "click_right" in s.events)
    assert st.click_bend_ts is not None
    # ชี้ไม่เหยียด (0.7) + กลางพับ → ไม่คลิกขวา
    r2 = Rig(fps)
    r2.feed_n(_g(idx=0.7, mid=0.3), 6)
    assert "click_right" not in r2.events()


@pytest.mark.parametrize("fps", FPS)
def test_no_click_while_clutched_or_off(fps):
    r = Rig(fps)
    r.feed_n(_g(0.3, 0.3, 0.3, 0.3), 4)
    assert r.states[-1].mode == "clutch"
    r.feed_n(_g(0.3, 0.3, 0.3, 0.3), 6)
    assert r.count("click_left") == 0
    t = GestureTracker(GestureConfig())  # ยังไม่ enable
    for i in range(10):
        st = t.update(_g(idx=0.3), i * 0.05)
    assert st.mode == "off" and st.events == ()


@pytest.mark.parametrize("fps", FPS)
def test_clutch_release_requires_rearm(fps):
    r = Rig(fps)
    r.feed_n(_g(0.3, 0.3, 0.3, 0.3), 4)
    # ปล่อยกำ: ชี้ยังพับต่ำ แต่ที่เหลือคลาย (mean>up)
    r.feed_n(_g(idx=0.3, mid=1.0, ring=1.0, pinky=1.0), 4)
    assert r.states[-1].mode == "move"
    assert r.count("click_left") == 0


@pytest.mark.parametrize("fps", FPS)
def test_hand_lost_during_drag_ends_it_immediately(fps):
    r = Rig(fps)
    r.feed_n(_g(pinch=0.02), 4)
    st = r.t.update(None, r.ts + r.dt)
    assert st.events == ("drag_end",)
    assert st.mode == "move"


@pytest.mark.parametrize("fps", FPS)
def test_hand_lost_during_scroll_ends_it(fps):
    r = Rig(fps)
    r.feed_n(_g(ring=0.3, pinky=0.3), 5)
    st = r.t.update(None, r.ts + r.dt)
    assert st.events == ("scroll_end",)


@pytest.mark.parametrize("fps", FPS)
def test_toggle_off_during_drag_ends_it_same_frame(fps):
    r = Rig(fps)
    # ฝ่ามือ + จีบพร้อมกัน (สังเคราะห์) ค้างจน toggle off
    palm_pinch = HandFeatures(curl=PALM.curl, pinch=0.02, hand_size=0.2, anchor=(0.5, 0.5),
                              spread=1.2, palm_facing=0.9)
    for _ in range(int(1.3 * fps) + 2):
        r.feed(palm_pinch)
    ev = r.events()
    assert "drag_start" in ev and "toggle_off" in ev
    off_ts = next(t for t, e in r.log if e == "toggle_off")
    assert (off_ts, "drag_end") in r.log
    assert r.states[-1].mode == "off"


@pytest.mark.parametrize("fps", FPS)
def test_pinch_during_clutch_does_not_start_drag(fps):
    r = Rig(fps)
    r.feed_n(_g(0.3, 0.3, 0.3, 0.3), 4)
    r.feed_n(_g(0.3, 0.3, 0.3, 0.3, pinch=0.02), 6)
    assert "drag_start" not in r.events()


def test_progress_fields():
    t = GestureTracker(GestureConfig())
    st = t.update(_g(idx=0.7, mid=0.95, pinch=0.045), 0.0)
    assert st.index_progress == pytest.approx(0.5)
    assert st.middle_progress == 0.0
    assert st.pinch_progress == pytest.approx(0.5)
    st = t.update(_g(idx=0.3, mid=0.3, pinch=0.01), 0.1)
    assert st.index_progress == 1.0 and st.middle_progress == 1.0 and st.pinch_progress == 1.0
