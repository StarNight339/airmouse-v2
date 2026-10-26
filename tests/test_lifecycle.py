import pytest

from airmouse.config import AppConfig
from airmouse.lifecycle import Lifecycle

DT = 0.1  # 10 fps


def _cfg(**kw) -> AppConfig:
    kw.setdefault("low_power_after_s", 3.0)
    kw.setdefault("auto_exit_minutes", 1.0)
    return AppConfig(**kw)


def _run(lc: Lifecycle, present: bool, t0: float, seconds: float):
    """ป้อนเฟรม 10 fps; คืน [(ts, LifeState)] เฉพาะเฟรมที่มี event"""
    out = []
    n = round(seconds / DT)
    for i in range(n + 1):
        ts = round(t0 + i * DT, 6)
        st = lc.update(present, ts)
        if st.events:
            out.append((ts, st))
    return out


def test_active_while_hand_present():
    lc = Lifecycle(_cfg())
    for i in range(50):
        st = lc.update(True, i * DT)
        assert st.state == "active" and st.events == () and st.idle_s == 0.0


def test_timeline_paused_low_power_exit_at_exact_thresholds():
    lc = Lifecycle(_cfg())
    lc.update(True, 0.0)
    ev = _run(lc, False, 0.1, 61.0)
    names = [(ts, e) for ts, st in ev for e in st.events]
    assert names[0] == (0.1, "paused")
    assert names[1] == (3.0, "low_power")  # idle = 3.0 - 0.0
    assert names[2] == (60.0, "exit")
    assert len(names) == 3  # แต่ละ event ครั้งเดียว
    assert lc.state == "exit"


def test_states_between_thresholds():
    lc = Lifecycle(_cfg())
    lc.update(True, 0.0)
    assert lc.update(False, 0.1).state == "paused"
    assert lc.update(False, 2.9).state == "paused"
    st = lc.update(False, 3.0)
    assert st.state == "low_power" and st.events == ("low_power",)
    assert st.idle_s == pytest.approx(3.0)
    assert lc.update(False, 30.0).state == "low_power"


def test_resumed_before_low_power():
    lc = Lifecycle(_cfg())
    lc.update(True, 0.0)
    lc.update(False, 0.1)
    st = lc.update(True, 1.0)
    assert st.state == "active" and st.events == ("resumed",)
    # หายอีกครั้ง: idle นับใหม่จากตอนที่เห็นมือล่าสุด
    st = lc.update(False, 1.1)
    assert st.events == ("paused",) and st.idle_s == pytest.approx(0.1)


def test_resumed_after_low_power_is_flagged():
    lc = Lifecycle(_cfg())
    lc.update(True, 0.0)
    lc.update(False, 0.1)
    lc.update(False, 3.5)
    assert lc.state == "low_power"
    st = lc.update(True, 4.0)
    assert st.state == "active"
    assert st.events == ("resumed", "resumed_from_low_power")
    assert lc.update(True, 4.1).events == ()


@pytest.mark.parametrize("kwargs", [{"stay": True}, {"cfg_minutes": 0.0}, {"cfg_minutes": -1.0}])
def test_never_exits_when_disabled(kwargs):
    minutes = kwargs.get("cfg_minutes", 1.0)
    lc = Lifecycle(_cfg(auto_exit_minutes=minutes), stay=kwargs.get("stay", False))
    lc.update(True, 0.0)
    for ts in (10.0, 100.0, 5000.0, 1e6):
        st = lc.update(False, ts)
        assert st.state == "low_power" and "exit" not in st.events


def test_startup_without_hand_still_exits():
    lc = Lifecycle(_cfg())
    st = lc.update(False, 100.0)  # เฟรมแรก ts ไม่ใช่ 0
    assert st.state == "paused" and st.events == ("paused",) and st.idle_s == 0.0
    st = lc.update(False, 103.0)
    assert st.events == ("low_power",)
    assert lc.update(False, 159.9).state == "low_power"
    st = lc.update(False, 160.0)
    assert st.state == "exit" and st.events == ("exit",)


def test_exit_is_final_even_if_hand_appears():
    lc = Lifecycle(_cfg(auto_exit_minutes=0.05))  # 3 s
    lc.update(False, 0.0)
    lc.update(False, 3.0)
    assert lc.state == "exit"
    st = lc.update(True, 4.0)
    assert st.state == "exit" and st.events == ()


def test_backwards_ts_is_safe():
    lc = Lifecycle(_cfg())
    lc.update(True, 10.0)
    lc.update(False, 10.5)
    st = lc.update(False, 5.0)  # เวลาย้อน
    assert st.idle_s >= 0 and st.state == "paused" and st.events == ()
    st = lc.update(True, 4.0)
    assert st.state == "active" and st.events == ("resumed",) and st.idle_s == 0.0
    st = lc.update(False, 3.0)
    assert st.events == ("paused",) and st.idle_s == 0.0


def test_first_update_with_hand_has_no_events():
    st = Lifecycle(_cfg()).update(True, 5.0)
    assert st.state == "active" and st.events == ()
