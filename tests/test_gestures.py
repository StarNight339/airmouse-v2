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
OPEN_IDX = _f(curls=(0.9, 0.9, 0.9, 0.9, 0.9), spread=0.5)


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
    for feats in (_f(spread=0.5), _f(facing=0.0), OPEN_IDX):
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


def test_toggle_off_clears_clutch():
    t, ts = _enabled()
    _run(t, FIST, ts + 0.01, ts + 0.05)
    assert t.clutch
    t.clutch = True
    # บังคับสถานะ: ฝ่ามือค้างจนปิด
    _, ev, st = _run(t, PALM, ts + 0.1, ts + 1.3)
    assert "toggle_off" in ev and not st.clutch and not st.enabled


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
