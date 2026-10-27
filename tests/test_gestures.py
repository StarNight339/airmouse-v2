import itertools

import pytest

from airmouse.config import GestureConfig
from airmouse.features import HandFeatures
from airmouse.gestures import (GestureTracker, is_scroll_pose, is_switcher_pose, raw_pose,
                               switcher_gap)

FPS = [10, 30]
EXT = 0.95
NORMAL_FACING = -0.70   # มือซ้ายจริง: ด้านปกติติดลบสม่ำเสมอ
FLIP_FACING = 0.72      # พลิกมือ


def _g(idx=0.91, mid=0.96, ring=0.96, pinky=0.95, pinch=0.05, facing=NORMAL_FACING,
       thumb=0.98) -> HandFeatures:
    curl = {"thumb": thumb, "index": idx, "middle": mid, "ring": ring, "pinky": pinky}
    return HandFeatures(curl=curl, pinch=pinch, hand_size=0.2, anchor=(0.5, 0.5),
                        spread=0.7, palm_facing=facing)


NORMAL = _g()
FLIP = _g(facing=FLIP_FACING)
FIST = _g(idx=0.45, mid=0.45, ring=0.48, pinky=0.48)
SCROLL = _g(idx=0.96, mid=0.95, ring=0.6, pinky=0.57)
PINCH = _g(idx=0.72, pinch=0.020)  # ค่าจริง: จีบค้าง 1.9–2.2 cm นิ้วชี้งอ 0.71–0.73


class Rig:
    """tracker ที่เรียนรู้ด้านปกติแล้ว (และ enable ถ้า enabled=True) ป้อนเฟรมที่ fps ที่กำหนด"""

    def __init__(self, fps: int, enabled: bool = True, normal: float = NORMAL_FACING) -> None:
        self.fps = fps
        self.dt = 1 / fps
        self.t = GestureTracker(GestureConfig())
        self.ts = 0.0
        self.log: list[tuple[float, str]] = []
        self.states: list = []
        self.normal = normal
        self.flip = -normal
        self.feed_n(_g(facing=normal), int(2 * fps) + 2)  # ต้องสังเกต >= 1.5 s ก่อน commit
        assert self.states[-1].normal_sign == (1 if normal > 0 else -1)
        if enabled:
            self.feed_for(_g(facing=self.flip), 1.2)
            assert self.events() == ["toggle_on"]
            self.feed_n(_g(facing=normal), 3)  # พลิกกลับ + armed
            self.log.clear()

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

    def feed_for(self, feats, seconds: float):
        return self.feed_n(feats, int(round(seconds * self.fps)) + 1)

    def events(self) -> list[str]:
        return [e for _, e in self.log]

    def count(self, name: str) -> int:
        return self.events().count(name)


def _ramp(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * (i + 1) / n for i in range(n)]


def _bend_frames(fps: int) -> int:
    return 3 if fps <= 10 else 8  # พับนิ้วใช้ ~0.3 s


# ===================== เรียนรู้ด้านปกติ =====================
def test_starts_disabled_and_learning():
    t = GestureTracker(GestureConfig())
    st = t.update(None, 0.0)
    assert not st.enabled and not st.clutch and st.mode == "off"
    st = t.update(NORMAL, 0.1)
    assert st.learning and st.normal_sign == 0


@pytest.mark.parametrize("normal", [NORMAL_FACING, 0.70])
def test_normal_sign_learned_for_either_hand(normal):
    t = GestureTracker(GestureConfig())
    for i in range(15):
        assert t.update(_g(facing=normal), i * 0.1).learning
    st = t.update(_g(facing=normal), 1.6)
    assert not st.learning and st.normal_sign == (1 if normal > 0 else -1)


def test_weak_facing_frames_are_not_used_for_learning():
    t = GestureTracker(GestureConfig())
    for i in range(30):
        st = t.update(_g(facing=0.3), i * 0.1)
    assert st.learning
    for i in range(20):
        st = t.update(_g(facing=-0.6), 3 + i * 0.1)
    assert st.normal_sign == -1


def test_majority_vote_for_noisy_start():
    t = GestureTracker(GestureConfig())
    facings = [-0.7, -0.6, 0.5, -0.8, -0.7, 0.6, -0.7, -0.6, -0.7, -0.8] * 2  # 2 s, 10% สวน
    for i, f in enumerate(facings):
        st = t.update(_g(facing=f), i * 0.1)
    assert st.normal_sign == -1


def test_no_toggle_before_learned():
    t = GestureTracker(GestureConfig())
    for i in range(8):  # ยังเรียนรู้ไม่ครบ
        st = t.update(FLIP, i * 0.2)
    assert st.learning and not st.enabled and st.events == ()


def test_long_loss_keeps_learned_side_even_if_return_flipped():
    r = Rig(10, enabled=False)
    r.t.update(None, r.ts + 0.1)
    r.ts += 5.0
    st = r.feed(FLIP)  # กลับมาในท่าพลิก ต้องไม่เรียนรู้ด้านพลิกเป็นปกติ
    assert st.normal_sign == -1
    r.feed_for(FLIP, 1.2)
    assert r.events() == ["toggle_on"]


def test_short_loss_keeps_normal_sign():
    r = Rig(10, enabled=False)
    r.t.update(None, r.ts + 0.1)
    st = r.t.update(NORMAL, r.ts + 1.0)
    assert st.normal_sign == -1 and not st.learning


# ===================== ท่าปกติต้องนิ่ง =====================
@pytest.mark.parametrize("fps", FPS)
def test_normal_move_pose_for_10s_does_nothing(fps):
    r = Rig(fps)
    n = 10 * fps
    for i in range(n):
        # นิ้วชี้ตกลงถึง 0.78 เป็นช่วง ๆ ระหว่างขยับ, pinch 3-6 cm, facing แกว่งเล็กน้อย
        idx = 0.78 if i % 17 in (3, 4, 5) else 0.91
        pinch = 0.03 + 0.03 * ((i % 7) / 6)
        r.feed(_g(idx=idx, pinch=pinch, facing=NORMAL_FACING + 0.05 * ((i % 5) - 2) / 2))
    assert r.events() == []
    assert all(s.mode == "move" for s in r.states[-n:])
    assert r.states[-1].enabled


@pytest.mark.parametrize("fps", FPS)
def test_relaxed_pinch_distance_does_not_drag(fps):
    r = Rig(fps)
    for pinch in (0.03, 0.045, 0.06, 0.035, 0.03):
        r.feed_n(_g(pinch=pinch), 2 * fps)
    assert r.events() == []


@pytest.mark.parametrize("fps", FPS)
def test_disabled_does_nothing(fps):
    r = Rig(fps, enabled=False)
    for feats in (FIST, PINCH, SCROLL, _g(idx=0.4)):
        r.feed_n(feats, fps)
    assert r.events() == [] and r.states[-1].mode == "off"


# ===================== toggle ด้วยการพลิกมือ =====================
@pytest.mark.parametrize("fps", FPS)
def test_flip_toggles_on_after_hold_not_before(fps):
    r = Rig(fps, enabled=False)
    r.feed_for(FLIP, 0.55)
    assert r.events() == [] and not r.states[-1].enabled
    assert 0.5 < r.states[-1].toggle_progress < 1.0
    r.feed_for(FLIP, 0.4)
    assert r.events() == ["toggle_on"] and r.states[-1].enabled


@pytest.mark.parametrize("fps", FPS)
def test_flip_no_repeat_then_flip_again_toggles_off(fps):
    r = Rig(fps, enabled=False)
    r.feed_for(FLIP, 4.0)
    assert r.events() == ["toggle_on"]  # ค้างต่อไม่ toggle ซ้ำ
    r.feed_n(NORMAL, 3)
    assert r.events() == ["toggle_on"] and r.states[-1].mode == "move"
    r.feed_for(FLIP, 1.2)
    assert r.events() == ["toggle_on", "toggle_off"]
    assert r.states[-1].enabled is False and r.states[-1].mode == "off"
    r.feed_n(NORMAL, 3)
    r.feed_for(FLIP, 1.2)
    assert r.events() == ["toggle_on", "toggle_off", "toggle_on"]


@pytest.mark.parametrize("fps", FPS)
def test_flip_works_for_positive_normal_hand(fps):
    r = Rig(fps, enabled=False, normal=0.7)
    r.feed_for(_g(facing=-0.72), 1.2)
    assert r.events() == ["toggle_on"]


def test_progress_grows_from_zero_to_one_then_resets():
    r = Rig(30, enabled=False)
    prog = [r.feed(FLIP).toggle_progress for _ in range(30)]
    assert prog[0] == 0.0
    nz = [p for p in prog if p > 0]
    assert nz == sorted(nz) and nz[-1] > 0.9
    assert prog[-1] == 0.0 and r.events() == ["toggle_on"]


@pytest.mark.parametrize("fps", FPS)
def test_weak_flip_does_not_count(fps):
    r = Rig(fps, enabled=False)
    r.feed_for(_g(facing=0.4), 2.0)  # |facing| ต่ำกว่า flip_facing_min
    assert r.events() == []


@pytest.mark.parametrize("fps", FPS)
def test_interrupted_flip_restarts(fps):
    r = Rig(fps, enabled=False)
    r.feed_for(FLIP, 0.5)
    r.feed_n(NORMAL, 2)
    r.feed_for(FLIP, 0.5)
    assert r.events() == []


@pytest.mark.parametrize("fps", FPS)
def test_one_frame_dropout_does_not_rearm_toggle(fps):
    r = Rig(fps, enabled=False)
    r.feed_for(FLIP, 1.0)
    assert r.events() == ["toggle_on"]
    r.t.update(None, r.ts + r.dt)
    r.ts += r.dt
    r.feed_for(FLIP, 2.0)
    assert r.events() == ["toggle_on"]


@pytest.mark.parametrize("fps", FPS)
def test_long_loss_clears_release_requirement(fps):
    r = Rig(fps, enabled=False)
    r.feed_for(FLIP, 1.0)
    r.t.update(None, r.ts + r.dt)
    r.ts += 1.0  # หายนาน > 0.5 s แต่ < 3 s
    r.feed_for(FLIP, 1.2)
    assert r.events() == ["toggle_on", "toggle_off"]


def test_progress_clamped_when_time_goes_backwards():
    r = Rig(10, enabled=False)
    r.feed_n(FLIP, 5)
    st = r.t.update(FLIP, 0.1)  # ts ย้อนหลัง
    assert 0.0 <= st.toggle_progress <= 1.0
    st = r.t.update(FLIP, 0.2)
    assert 0.0 <= st.toggle_progress < 1.0 and st.events == ()


# ===================== ระหว่างพลิก =====================
@pytest.mark.parametrize("fps", FPS)
def test_flip_mode_blocks_clicks_and_cursor_modes(fps):
    r = Rig(fps)
    st = r.feed(_g(facing=FLIP_FACING))
    assert st.mode == "flip"  # ทันทีตั้งแต่เฟรมแรก
    # ขณะพลิก ชี้พับ/จีบ/ท่า scroll ไม่เกิดอะไร
    r.feed_n(_g(idx=0.3, facing=FLIP_FACING), 2)
    r.feed_n(_g(pinch=0.01, facing=FLIP_FACING), 2)
    r.feed_n(_g(ring=0.4, pinky=0.4, facing=FLIP_FACING), 2)
    assert r.events() == []
    assert r.states[-1].mode == "flip"


@pytest.mark.parametrize("fps", FPS)
def test_flip_ends_drag_immediately(fps):
    r = Rig(fps)
    r.feed_n(PINCH, 4)
    assert r.events() == ["drag_start"]
    st = r.feed(_g(pinch=0.012, facing=FLIP_FACING))
    assert st.events == ("drag_end",) and st.mode == "flip"
    r.feed_n(_g(pinch=0.012, facing=FLIP_FACING), 6)
    assert r.count("drag_start") == 1  # พลิกอยู่ ไม่เริ่มลากใหม่


@pytest.mark.parametrize("fps", FPS)
def test_flip_ends_scroll_immediately(fps):
    r = Rig(fps)
    r.feed_n(SCROLL, 5)
    assert r.events() == ["scroll_start"]
    st = r.feed(_g(idx=0.96, mid=0.95, ring=0.6, pinky=0.57, facing=FLIP_FACING))
    assert st.events == ("scroll_end",)


@pytest.mark.parametrize("fps", FPS)
def test_click_needs_rearm_after_flip_back(fps):
    r = Rig(fps)
    r.feed_n(_g(idx=0.4, facing=FLIP_FACING), 4)
    r.feed_n(_g(idx=0.4), 5)  # พลิกกลับมาโดยนิ้วชี้ยังพับ
    assert r.count("click_left") == 0
    r.feed_n(NORMAL, 3)
    r.feed_n(_g(idx=0.4), 6)  # confirm + ช่วงหน่วงกันกำมือ (CLICK_GUARD_S)
    assert r.count("click_left") == 1


# ===================== จีบ / ลาก =====================
@pytest.mark.parametrize("fps", FPS)
def test_deliberate_pinch_drags_and_releases(fps):
    r = Rig(fps)
    r.feed_n(_g(idx=0.8, pinch=0.015), 5)
    assert r.events() == ["drag_start"]
    assert r.states[-1].mode == "drag" and r.states[-1].pinch_progress == 1.0
    r.feed_n(_g(idx=0.3, pinch=0.012), 5)  # ระหว่างลาก ชี้พับก็ไม่คลิก
    assert "click_left" not in r.events()
    r.feed_n(NORMAL, 5)  # median 5 เฟรม → ปล่อยหลังค่าห่างครองเสียงข้างมาก
    assert r.events() == ["drag_start", "drag_end"]
    assert r.states[-1].mode == "move"


@pytest.mark.parametrize("fps", FPS)
def test_pinch_hysteresis_keeps_drag(fps):
    r = Rig(fps)
    r.feed_n(PINCH, 4)
    r.feed_n(_g(pinch=0.025), 6)  # อยู่ระหว่าง down/up
    assert r.states[-1].mode == "drag" and r.events() == ["drag_start"]


@pytest.mark.parametrize("fps", FPS)
def test_left_click_needs_rearm_after_drag(fps):
    r = Rig(fps)
    r.feed_n(PINCH, 4)
    r.feed_n(_g(idx=0.3), 6)  # ปล่อยจีบแต่ชี้ยังพับ
    assert r.events() == ["drag_start", "drag_end"]
    r.feed_n(NORMAL, 3)
    r.feed_n(_g(idx=0.3), 6)  # confirm + ช่วงหน่วงกันกำมือ
    assert r.count("click_left") == 1


@pytest.mark.parametrize("fps", FPS)
def test_hand_lost_during_drag_ends_it_immediately(fps):
    r = Rig(fps)
    r.feed_n(PINCH, 4)
    st = r.t.update(None, r.ts + r.dt)
    assert st.events == ("drag_end",) and st.mode == "move"


@pytest.mark.parametrize("fps", FPS)
def test_toggle_off_ends_drag_and_scroll(fps):
    # พลิก = ตัด drag ทันทีตั้งแต่เฟรมแรก จึงไม่เหลือค้างตอน toggle_off
    r = Rig(fps)
    r.feed_n(PINCH, 4)
    r.feed_for(_g(pinch=0.012, facing=FLIP_FACING), 1.2)
    assert r.events() == ["drag_start", "drag_end", "toggle_off"]


@pytest.mark.parametrize("fps", FPS)
def test_pinch_during_clutch_does_not_start_drag(fps):
    r = Rig(fps)
    r.feed_n(FIST, 4)
    r.feed_n(_g(idx=0.45, mid=0.45, ring=0.48, pinky=0.48, pinch=0.012), 6)
    assert "drag_start" not in r.events()


# ===================== click =====================
@pytest.mark.parametrize("fps", FPS)
def test_deliberate_index_fold_gives_one_left_click(fps):
    r = Rig(fps)
    n = _bend_frames(fps)
    first_below_up = None
    for v in _ramp(0.91, 0.5, n):
        r.feed(_g(idx=v))
        if first_below_up is None and v < 0.82:
            first_below_up = r.ts
    r.feed_n(_g(idx=0.5), 2 * fps)  # ค้างพับนาน ๆ ไม่คลิกซ้ำ
    assert r.events() == ["click_left"]
    st = next(s for s in r.states if "click_left" in s.events)
    assert st.click_bend_ts == pytest.approx(first_below_up)
    assert st.mode == "move"
    r.feed_n(NORMAL, 3)
    assert r.count("click_left") == 1


@pytest.mark.parametrize("fps", FPS)
def test_two_quick_bends_give_two_clicks(fps):
    r = Rig(fps)
    n = _bend_frames(fps)
    for _ in range(2):
        for v in _ramp(EXT, 0.4, n):
            r.feed(_g(idx=v))
        for v in _ramp(0.4, EXT, max(1, n // 2)):
            r.feed(_g(idx=v))
    r.feed_n(NORMAL, 2)
    assert r.count("click_left") == 2


@pytest.mark.parametrize("fps", FPS)
def test_deliberate_middle_fold_gives_right_click(fps):
    r = Rig(fps)
    n = _bend_frames(fps)
    for v in _ramp(0.96, 0.45, n):
        r.feed(_g(mid=v))
    r.feed_n(_g(mid=0.45), 3)
    assert r.events() == ["click_right"]
    st = next(s for s in r.states if "click_right" in s.events)
    assert st.click_bend_ts is not None


@pytest.mark.parametrize("fps", FPS)
def test_right_click_tolerates_small_ring_cobend_only(fps):
    r = Rig(fps)
    r.feed_n(_g(mid=0.45, ring=0.85), 7)  # ring งอตามเล็กน้อย (รวมช่วงหน่วงกันกำมือ)
    assert r.events() == ["click_right"]
    r2 = Rig(fps)
    r2.feed_n(_g(mid=0.45, ring=0.6), 6)  # ring งอตามมาก = เหมือนกำมือ/ท่า scroll → ไม่คลิก
    assert r2.events() == []


@pytest.mark.parametrize("fps", FPS)
def test_right_click_needs_index_extended(fps):
    r = Rig(fps)
    r.feed_n(_g(idx=0.7, mid=0.4), 6)
    assert "click_right" not in r.events()


@pytest.mark.parametrize("fps", FPS)
def test_scroll_pose_transitions_do_not_right_click(fps):
    r = Rig(fps)
    r.feed_n(SCROLL, 5)
    # ในท่า scroll นิ้วกลางงอ: ไม่ใช่คลิกขวา (ring ต่ำ)
    r.feed_n(_g(idx=0.96, mid=0.4, ring=0.6, pinky=0.57), 6)
    r.feed_n(NORMAL, 3)
    assert "click_right" not in r.events() and "click_left" not in r.events()


@pytest.mark.parametrize("fps", FPS)
@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_natural_fist_close_never_clicks(fps, order):
    # order[i] = ลำดับที่นิ้ว i เริ่มพับ เหลื่อมกันลำดับละ 35 ms (รวม 105 ms)
    r = Rig(fps)
    n = _bend_frames(fps)
    for step in range(n + 4):
        curls = []
        for finger in range(4):
            k = step - order[finger] * 0.035 * fps
            curls.append(0.95 if k <= 0 else max(0.3, 0.95 - 0.65 * k / n))
        r.feed(_g(*curls))
    r.feed_n(_g(0.3, 0.3, 0.3, 0.3), 4)
    ev = r.events()
    assert "click_left" not in ev and "click_right" not in ev
    assert "clutch_on" in ev


# ===================== scroll =====================
@pytest.mark.parametrize("fps", FPS)
def test_real_scroll_attempt_values_enter_scroll(fps):
    r = Rig(fps)
    r.feed_n(_g(idx=0.97, mid=0.95, ring=0.6, pinky=0.57), 5)
    assert r.events() == ["scroll_start"] and r.states[-1].mode == "scroll"
    r.feed_n(_g(idx=0.96, mid=0.95, ring=0.69, pinky=0.65), 5)  # ตัวเลขอีกชุดจาก log
    assert r.events() == ["scroll_start"]
    r.feed_n(NORMAL, 4)
    assert r.events() == ["scroll_start", "scroll_end"]
    assert r.count("click_left") == 0 and r.count("click_right") == 0


@pytest.mark.parametrize("fps", FPS)
def test_fist_is_clutch_not_scroll(fps):
    r = Rig(fps)
    r.feed_n(FIST, 6)
    assert r.events() == ["clutch_on"] and r.states[-1].mode == "clutch"
    # ท่า scroll ระหว่าง clutch ไม่เริ่ม scroll (ต้องปล่อย clutch ก่อน)
    r.feed_n(SCROLL, 6)
    assert "scroll_start" not in r.events() or r.states[-1].mode in ("scroll", "move")


@pytest.mark.parametrize("fps", FPS)
def test_normal_pose_is_not_scroll(fps):
    r = Rig(fps)
    r.feed_n(_g(ring=0.8, pinky=0.8), 10)  # ring/pinky เฉลี่ย 0.8 > scroll_curl_max
    assert r.events() == []


@pytest.mark.parametrize("fps", FPS)
def test_hand_lost_during_scroll_ends_it(fps):
    r = Rig(fps)
    r.feed_n(SCROLL, 5)
    st = r.t.update(None, r.ts + r.dt)
    assert st.events == ("scroll_end",)


# ===================== clutch =====================
@pytest.mark.parametrize("fps", FPS)
def test_clutch_needs_confirm_and_hysteresis(fps):
    r = Rig(fps)
    st = r.feed(FIST)
    assert not st.clutch  # เฟรมแรกยังไม่ยืนยัน
    st = r.feed(FIST)
    assert st.clutch and st.events == ("clutch_on",)
    # ค่ากลางระหว่าง down/up (เฉลี่ย 0.76) ยังคง clutch
    for _ in range(5):
        assert r.feed(_g(idx=0.76, mid=0.76, ring=0.76, pinky=0.76)).clutch
    r.feed(NORMAL)
    st = r.feed(NORMAL)
    assert not st.clutch and st.events == ("clutch_off",)


@pytest.mark.parametrize("fps", FPS)
def test_no_click_while_clutched(fps):
    r = Rig(fps)
    r.feed_n(FIST, 10)
    assert r.count("click_left") == 0 and r.states[-1].mode == "clutch"


@pytest.mark.parametrize("fps", FPS)
def test_clutch_release_requires_rearm(fps):
    r = Rig(fps)
    r.feed_n(FIST, 4)
    r.feed_n(_g(idx=0.97, mid=0.98, ring=0.96, pinky=0.95), 4)
    assert r.states[-1].mode == "move"
    r.feed_n(_g(idx=0.5, mid=0.6, ring=0.96, pinky=0.95), 1)
    assert r.count("click_left") == 0  # ออกจาก clutch แล้วต้องเหยียดชี้ซ้ำ (armed) ก่อน


@pytest.mark.parametrize("fps", FPS)
def test_hand_lost_resets_clutch_and_flip_hold(fps):
    r = Rig(fps)
    r.feed_n(FIST, 4)
    st = r.t.update(None, r.ts + r.dt)
    assert st.events == ("clutch_off",) and not st.clutch and st.enabled
    r2 = Rig(fps, enabled=False)
    r2.feed_for(FLIP, 0.5)
    r2.t.update(None, r2.ts + r2.dt)
    r2.ts += r2.dt
    r2.feed_for(FLIP, 0.5)
    assert r2.events() == []  # ต้องนับเวลาใหม่


def test_progress_fields():
    t = GestureTracker(GestureConfig())
    st = t.update(_g(idx=0.805, mid=0.96, pinch=0.0275), 0.0)  # กึ่งกลาง click 0.77–0.84 / pinch 2.3–3.2
    assert st.index_progress == pytest.approx(0.5)
    assert st.middle_progress == 0.0
    assert st.pinch_progress == pytest.approx(0.5)
    t = GestureTracker(GestureConfig())  # median ของระยะจีบ: ใช้ tracker ใหม่ให้ค่าเดียวล้วน
    st = t.update(_g(idx=0.3, mid=0.3, pinch=0.01), 0.1)
    assert st.index_progress == 1.0 and st.middle_progress == 1.0 and st.pinch_progress == 1.0


# ===================== รอบแก้ review: B1 learning, B2 click, B3 pinch, clutch exit, raw_pose =====================
from airmouse.gestures import (is_click_left, is_click_right, is_fist, is_scroll_pose,  # noqa: E402
                               raw_pose)

# ตัวอย่างจริง: พับชี้คลิก กลางงอตาม (ชี้นำ) นาง/ก้อยยังเหยียด
CLICK_SAMPLES = [(0.55, 0.60), (0.59, 0.67), (0.62, 0.67), (0.63, 0.65)]
# ตัวอย่างจริงของกำมือ: นาง/ก้อย 0.38–0.66
FIST_SAMPLES = [(0.40, 0.42, 0.50, 0.38), (0.55, 0.50, 0.66, 0.45), (0.45, 0.55, 0.40, 0.60),
                (0.50, 0.45, 0.62, 0.50)]


@pytest.mark.parametrize("fps", FPS)
def test_click_with_cobending_middle_clicks_once_each(fps):
    r = Rig(fps)
    for idx, mid in CLICK_SAMPLES:
        r.feed_n(_g(idx=idx, mid=mid, ring=0.97, pinky=0.94, pinch=0.03), 6)
        r.feed_n(NORMAL, 3)
    assert r.events() == ["click_left"] * len(CLICK_SAMPLES)


@pytest.mark.parametrize("fps", FPS)
def test_real_fist_samples_do_not_click(fps):
    for idx, mid, ring, pinky in FIST_SAMPLES:
        r = Rig(fps)
        r.feed_n(_g(idx=idx, mid=mid, ring=ring, pinky=pinky), 6)
        ev = r.events()
        assert "click_left" not in ev and "click_right" not in ev, (idx, mid, ring, pinky)


@pytest.mark.parametrize("fps", FPS)
def test_pinch_gate_for_click_and_drag_thresholds(fps):
    # 2.8 cm: เหนือ gate คลิก (pinch_down 2.2 + 0.3) → คลิกได้ ไม่ลาก
    r = Rig(fps)
    r.feed_n(_g(idx=0.55, mid=0.62, pinch=0.028), 6)
    assert r.events() == ["click_left"]
    # 2.4 cm: ต่ำกว่า gate (2.2 + 0.3) ไม่คลิก แต่ยังไม่ถึงเกณฑ์ลาก
    r2 = Rig(fps)
    r2.feed_n(_g(idx=0.55, mid=0.62, pinch=0.024), 6)
    assert r2.events() == []
    # 2.0 cm + ชี้งอ = จีบจริง → ลาก ไม่คลิก
    r3 = Rig(fps)
    r3.feed_n(_g(idx=0.55, mid=0.62, pinch=0.020), 6)
    assert r3.events() == ["drag_start"]


@pytest.mark.parametrize("fps", FPS)
def test_normal_pose_pinch_dips_22_to_28_cm_never_drags(fps):
    r = Rig(fps)
    for i in range(10 * fps):
        r.feed(_g(pinch=0.022 + 0.006 * ((i % 5) / 4)))
    assert r.events() == []


@pytest.mark.parametrize("fps", FPS)
def test_clutch_exit_with_index_middle_extended_then_scroll(fps):
    r = Rig(fps)
    r.feed_n(FIST, 4)
    assert r.states[-1].mode == "clutch"
    # ชี้/กลางเหยียด นาง/ก้อยยังพับ (ข้อมูลจริง .59/.49): ออกจาก clutch แล้วเข้า scroll ได้
    r.feed_n(_g(idx=0.97, mid=0.98, ring=0.59, pinky=0.49), 8)
    ev = r.events()
    assert ev[:3] == ["clutch_on", "clutch_off", "scroll_start"]
    assert r.states[-1].mode == "scroll"


@pytest.mark.parametrize("fps", FPS)
def test_clutch_stays_while_index_or_middle_folded(fps):
    r = Rig(fps)
    r.feed_n(FIST, 4)
    r.feed_n(_g(idx=0.97, mid=0.5, ring=0.99, pinky=0.99), 6)
    assert r.states[-1].mode == "clutch"


def test_scroll_extend_min_is_used():
    cfg = GestureConfig()
    assert is_scroll_pose(_g(idx=0.78, mid=0.78, ring=0.5, pinky=0.5).curl, cfg)
    assert not is_scroll_pose(_g(idx=0.72, mid=0.9, ring=0.5, pinky=0.5).curl, cfg)


def test_poses_are_mutually_exclusive_on_grid():
    cfg = GestureConfig()
    vals = [0.3, 0.5, 0.65, 0.72, 0.76, 0.8, 0.85, 0.95]
    for idx, mid, ring, pinky in itertools.product(vals, repeat=4):
        c = _g(idx, mid, ring, pinky).curl
        hits = [is_fist(c, cfg), is_scroll_pose(c, cfg),
                is_click_left(c, 0.05, cfg), is_click_right(c, 0.05, cfg)]
        assert sum(hits) <= 1, (idx, mid, ring, pinky, hits)


def test_raw_pose_labels_with_real_samples():
    cfg = GestureConfig()
    cases = [
        (NORMAL, "move"),
        (_g(idx=0.78), "move"),
        (FLIP, "flip"),
        (PINCH, "pinch"),
        (FIST, "fist"),
        (SCROLL, "scroll"),
        (_g(idx=0.55, mid=0.60, pinch=0.028), "click_left"),
        (_g(mid=0.5, pinch=0.05), "click_right"),
        (_g(idx=0.55, mid=0.60, pinch=0.024), "move"),  # ต่ำกว่า gate คลิก ยังไม่ถึงจีบ
        (_g(pinch=0.020), "pinch"),  # จีบโดยชี้เหยียดตรงก็นับ (ผู้ใช้จีบแบบนี้จริง)
    ]
    for feats, want in cases:
        assert raw_pose(feats, cfg, -1) == want
    # ยังไม่รู้ด้านปกติ → ไม่มี flip
    assert raw_pose(FLIP, cfg, 0) == "move"


@pytest.mark.parametrize("fps", FPS)
def test_raw_pose_agrees_with_tracker_mode_when_stable(fps):
    cfg = GestureConfig()
    for feats, mode in ((NORMAL, "move"), (FIST, "clutch"), (SCROLL, "scroll"),
                        (PINCH, "drag"), (FLIP, "flip")):
        r = Rig(fps)
        r.feed_n(feats, 6)
        assert r.states[-1].mode == mode
        assert raw_pose(feats, cfg, -1) in {"move": ("move",), "clutch": ("fist",),
                                            "scroll": ("scroll",), "drag": ("pinch",),
                                            "flip": ("flip",)}[mode]


# ----- B1: เรียนรู้ด้านปกติต้องไม่ผิดด้านเมื่อพลิกมือตอนเริ่ม -----
@pytest.mark.parametrize("fps", FPS)
@pytest.mark.parametrize("normal_frames", [3, 5])
def test_flip_right_after_appearing_does_not_learn_flipped_side(fps, normal_frames):
    t = GestureTracker(GestureConfig())
    ts = 0.0
    signs = []

    def feed(feats):
        nonlocal ts
        ts += 1 / fps
        st = t.update(feats, ts)
        signs.append(st.normal_sign)
        return st

    for _ in range(normal_frames):
        feed(NORMAL)
    for _ in range(int(1.0 * fps)):  # พลิกค้าง 1 s
        feed(FLIP)
    for _ in range(int(5 * fps)):
        st = feed(NORMAL)
    assert +1 not in signs
    assert st.normal_sign == -1 and not st.enabled


def test_learning_waits_until_window_agrees():
    t = GestureTracker(GestureConfig())
    ts = 0.0
    for f in [-0.7] * 6 + [0.7] * 6:  # ผสม 50/50 → ยังไม่ commit
        ts += 0.1
        st = t.update(_g(facing=f), ts)
    assert st.learning
    for _ in range(40):  # ด้านปกติกลับมา ในที่สุดเกิน 80%
        ts += 0.1
        st = t.update(_g(facing=-0.7), ts)
    assert st.normal_sign == -1


def test_committed_side_is_never_relearned():
    r = Rig(10, enabled=False)
    for _ in range(100):  # ค้างพลิก 10 s ก็ไม่เปลี่ยนด้าน
        r.feed(FLIP)
    assert r.states[-1].normal_sign == -1


@pytest.mark.parametrize("sign", [-1, 1])
def test_config_normal_facing_skips_learning(sign):
    t = GestureTracker(GestureConfig(normal_facing=sign))
    st = t.update(_g(facing=0.7 * sign), 0.0)
    assert not st.learning and st.normal_sign == sign
    # ด้านตรงข้ามคือพลิก ทันทีตั้งแต่เฟรมแรก (ไม่ต้องรอเรียนรู้)
    ts = 0.0
    for _ in range(20):
        ts += 0.1
        st = t.update(_g(facing=-0.72 * sign), ts)
    assert st.enabled and st.normal_sign == sign



@pytest.mark.parametrize("fps", FPS)
def test_comfortable_index_bend_clicks(fps):
    # ค่าจริงจาก log: พับนิ้วชี้สบาย ๆ ได้ 0.72–0.74 (เดิมต้อง < 0.70 จึงไม่คลิก)
    r = Rig(fps)
    r.feed_n(_g(idx=0.73, mid=0.98, ring=0.99, pinky=0.96), 6)
    assert r.events() == ["click_left"]


@pytest.mark.parametrize("fps", FPS)
def test_click_cancelled_when_fist_follows(fps):
    # นิ้วชี้พับถึงเกณฑ์ก่อน แล้วนาง/ก้อยพับตามภายใน CLICK_GUARD_S = กำมือ → ไม่คลิก
    r = Rig(fps)
    r.feed_n(_g(idx=0.6, mid=0.95, ring=0.97, pinky=0.96), 2)
    r.feed_n(_g(idx=0.5, mid=0.5, ring=0.6, pinky=0.6), 6)
    assert "click_left" not in r.events()


def test_stale_pending_click_dropped_after_frame_gap():
    # คลิกยืนยันแล้วแต่เฟรมขาดหาย 1.5 วิ (กล้องค้าง) → ห้ามคลิกย้อนหลังตอนเฟรมกลับมา
    r = Rig(10)
    r.feed_n(_g(idx=0.74), 2)
    r.ts += 1.5
    r.feed_n(NORMAL, 2)
    assert "click_left" not in r.events()


@pytest.mark.parametrize("fps", FPS)
@pytest.mark.parametrize("pose_after", ["pinch", "scroll", "fist"])
def test_mode_change_inside_click_guard_cancels_click(fps, pose_after):
    # คลิกยืนยันแล้ว แต่ภายใน CLICK_GUARD_S เปลี่ยนเป็นลาก/scroll/กำมือ → ห้ามคลิก
    after = {"pinch": PINCH,
             "scroll": _g(idx=0.97, mid=0.95, ring=0.6, pinky=0.57),
             "fist": _g(0.4, 0.4, 0.4, 0.4)}[pose_after]
    r = Rig(fps)
    r.feed_n(_g(idx=0.74), 2)
    r.feed_n(after, 6)
    assert "click_left" not in r.events()


def test_click_at_10fps_emits_on_next_frame_after_guard():
    # dt ~96 ms: ไม่ควรเลื่อนคลิกไปอีกเฟรมเพราะมาเร็วกว่า 100 ms นิดเดียว
    r = Rig(10)
    r.dt = 0.096
    r.feed_n(_g(idx=0.74), 3)  # 2 เฟรม confirm + 1 เฟรม guard
    assert r.events() == ["click_left"]



@pytest.mark.parametrize("fps", FPS)
def test_real_sustained_pinch_drags_without_click(fps):
    # ค่าจริงจาก log 21:15: จีบค้าง ~4 วิ ที่ 1.9–2.2 cm นิ้วชี้งอ 0.71–0.73 (เดิมไม่ลาก แต่คลิกหลอก)
    r = Rig(fps)
    vals = [(0.024, 0.74), (0.022, 0.73), (0.021, 0.71), (0.020, 0.72), (0.019, 0.71), (0.021, 0.72)]
    for i in range(4 * fps):
        p, idx = vals[i % len(vals)]
        r.feed(_g(idx=idx, mid=0.95, pinch=p))
    assert r.events() == ["drag_start"]
    r.feed_n(NORMAL, 4)
    assert r.events() == ["drag_start", "drag_end"]


@pytest.mark.parametrize("fps", FPS)
def test_rest_pose_close_thumb_never_drags(fps):
    # ท่าพัก 2.5–3.5 cm มีค่าแวบลง 2.0 cm เฟรมเดียวเป็นระยะ → median ตัดทิ้ง ห้ามลาก
    r = Rig(fps)
    for i in range(3 * fps):
        r.feed(_g(pinch=0.020 if i % 4 == 0 else 0.025 + 0.01 * ((i % 3) / 2)))
    assert r.events() == []


@pytest.mark.parametrize("fps", FPS)
def test_pinch_spikes_do_not_break_drag(fps):
    # ค่าจริง: ระหว่างจีบค้างค่ากระโดดไป 3–7 cm 1–2 เฟรม (ตรวจพลาด) → การลากต้องไม่หลุด
    r = Rig(fps)
    seq = [0.011, 0.012, 0.061, 0.010, 0.013, 0.067, 0.075, 0.012, 0.011, 0.013]
    r.feed_n(PINCH, 3)
    for p in seq * 3:
        r.feed(_g(idx=0.92, pinch=p))
    assert r.events() == ["drag_start"]
    r.feed_n(_g(pinch=0.07), 6)
    assert r.events() == ["drag_start", "drag_end"]


@pytest.mark.parametrize("fps", FPS)
def test_scroll_holds_while_middle_cobends(fps):
    # ค่าจริงจาก log: ระหว่าง scroll นิ้วกลางงอลง 0.62–0.70 (เดิมหลุดท่าทันที)
    r = Rig(fps)
    r.feed_n(SCROLL, 4)
    r.feed_n(_g(idx=0.78, mid=0.67, ring=0.64, pinky=0.59), fps)
    r.feed_n(_g(idx=0.88, mid=0.68, ring=0.60, pinky=0.58), fps)
    assert r.events() == ["scroll_start"] and r.states[-1].mode == "scroll"
    r.feed_n(NORMAL, 4)  # เหยียดทุกนิ้ว → จบ scroll
    assert r.events() == ["scroll_start", "scroll_end"]


# ===================== Phase 4: disable() / request_toggle() =====================
import threading  # noqa: E402


@pytest.mark.parametrize("fps", FPS)
def test_disable_mid_drag_ends_it_and_toggles_off(fps):
    r = Rig(fps)
    r.feed_n(PINCH, 5)
    assert r.t.dragging
    ev = r.t.disable()
    assert ev == ["drag_end", "toggle_off"]
    assert not r.t.enabled and not r.t.dragging
    assert r.feed(PINCH).mode == "off"
    assert r.events().count("drag_start") == 1  # ไม่เริ่มลากใหม่ตอนปิด


@pytest.mark.parametrize("fps", FPS)
def test_disable_mid_scroll(fps):
    r = Rig(fps)
    r.feed_n(SCROLL, 5)
    assert r.t.scrolling
    assert r.t.disable() == ["scroll_end", "toggle_off"]


@pytest.mark.parametrize("fps", FPS)
def test_disable_mid_clutch(fps):
    r = Rig(fps)
    r.feed_n(FIST, 5)
    assert r.t.clutch
    assert r.t.disable() == ["clutch_off", "toggle_off"]
    assert not r.t.clutch


@pytest.mark.parametrize("fps", FPS)
def test_disable_when_already_off_emits_nothing(fps):
    r = Rig(fps, enabled=False)
    assert r.t.disable() == []
    mine: list[str] = ["x"]
    assert r.t.disable(mine) is mine and mine == ["x"]


def test_disable_appends_to_given_list():
    r = Rig(10)
    r.feed_n(PINCH, 5)
    ev = ["before"]
    assert r.t.disable(ev) is ev
    assert ev == ["before", "drag_end", "toggle_off"]


@pytest.mark.parametrize("fps", FPS)
def test_disable_clears_pending_click(fps):
    r = Rig(fps)
    r.feed_n(_g(idx=0.55, mid=0.60, pinch=0.04), 2)  # เพิ่งเข้าเกณฑ์คลิก (อาจรอยืนยัน)
    r.t.disable()
    r.t.request_toggle()
    r.feed_n(NORMAL, 3)  # เปิดใหม่ ต้องไม่มีคลิกค้างเก่าโผล่
    assert r.events().count("click_left") <= 1
    assert "click_left" not in [e for s in r.states[-3:] for e in s.events]


@pytest.mark.parametrize("fps", FPS)
def test_disable_mid_flip_does_not_retoggle_while_flip_held(fps):
    r = Rig(fps)
    r.feed_for(_g(facing=FLIP_FACING), 0.3)  # กำลังพลิก ยังไม่ถึงเวลา toggle
    assert r.states[-1].mode == "flip" and r.events() == []
    assert r.t.disable() == ["toggle_off"]
    r.feed_for(_g(facing=FLIP_FACING), 3.0)  # พลิกค้างต่อ
    assert r.events() == []  # ไม่ toggle_on ซ้ำเอง
    assert not r.t.enabled
    r.feed_n(NORMAL, 3)  # พลิกกลับ → พร้อม toggle ครั้งใหม่
    r.feed_for(_g(facing=FLIP_FACING), 1.2)
    assert r.events() == ["toggle_on"]


@pytest.mark.parametrize("fps", FPS)
def test_request_toggle_from_other_thread_applies_next_update(fps):
    r = Rig(fps, enabled=False)
    done = threading.Event()

    def worker():
        r.t.request_toggle()
        done.set()

    th = threading.Thread(target=worker)
    th.start()
    th.join(2)
    assert done.is_set()
    assert not r.t.enabled  # ยังไม่เปลี่ยนจนกว่า update ถัดไป
    st = r.feed(NORMAL)
    assert st.events == ("toggle_on",) and st.enabled and st.mode == "move"
    assert r.feed(NORMAL).events == ()  # ใช้ครั้งเดียว

    th = threading.Thread(target=r.t.request_toggle)
    th.start()
    th.join(2)
    st = r.feed(NORMAL)
    assert st.events == ("toggle_off",) and not st.enabled and st.mode == "off"


@pytest.mark.parametrize("fps", FPS)
def test_request_toggle_off_ends_actions(fps):
    r = Rig(fps)
    r.feed_n(PINCH, 5)
    r.t.request_toggle()
    st = r.feed(PINCH)
    assert st.events == ("drag_end", "toggle_off")
    assert st.mode == "off"
    assert r.t.dragging is False


def test_request_toggle_applies_even_when_hand_lost():
    r = Rig(10, enabled=False)
    r.t.request_toggle()
    st = r.t.update(None, r.ts + 0.1)
    assert st.events == ("toggle_on",) and st.enabled


def test_many_requests_before_update_collapse_to_one_toggle():
    r = Rig(10, enabled=False)
    for _ in range(3):
        r.t.request_toggle()
    st = r.feed(NORMAL)
    assert st.events == ("toggle_on",)


# ===================== Switcher (ท่า 3 นิ้ว) =====================
SW_FPS = [16, 30]
CFG = GestureConfig()


def _sw(x=0.5, y=0.5, idx=0.95, mid=0.95, ring=0.95, pinky=0.75, facing=NORMAL_FACING,
        pinch=0.05) -> HandFeatures:
    """ท่า 3 นิ้ว: ก้อยพับ ~0.75 (gap +0.20) พร้อมกำหนดตำแหน่ง anchor"""
    curl = {"thumb": 0.98, "index": idx, "middle": mid, "ring": ring, "pinky": pinky}
    return HandFeatures(curl=curl, pinch=pinch, hand_size=0.2, anchor=(x, y),
                        spread=0.7, palm_facing=facing)


SW = _sw()
BLUR = _sw(idx=0.5, mid=0.5, ring=0.5, pinky=0.70)        # motion blur: นิ้วบนตกหมด ก้อยยังอยู่
SEMI = _sw(idx=0.85, mid=0.85, ring=0.85, pinky=0.75)     # gap 0.10: ยังนับว่าอยู่ในท่า แต่ไม่ถึงเกณฑ์เข้า
AMBIG = _sw(idx=0.78, mid=0.78, ring=0.78, pinky=0.75)    # gap 0.03: ไม่ใช่ท่า, ไม่ใช่เลิกท่าชัดเจน
SW_EVENTS = ("switcher_open", "switcher_commit", "switcher_cancel", "switcher_minimize")


def _open_switcher(r: Rig, x: float = 0.5, y: float = 0.5) -> None:
    r.feed_for(_sw(x, y), 0.5)
    assert r.count("switcher_open") == 1
    assert r.states[-1].mode == "switcher"
    r.log.clear()


def _sw_events(r: Rig) -> list[str]:
    return [e for e in r.events() if e in SW_EVENTS]


def _stroke(r: Rig, y0: float, direction: int, x: float = 0.5, drift: float = 0.0) -> float:
    """สะบัดแนวตั้ง 4 เฟรม (~7.5–8 ขนาดมือ/วิ ที่ hand_size 0.2); direction -1 = ขึ้น, +1 = ลง"""
    per = (0.05 if r.fps >= 30 else 0.1) * direction
    for i in range(1, 5):
        r.feed(_sw(x + drift * i / 4, y0 + per * i))
    return y0 + 4 * per


def test_switcher_predicates():
    assert is_switcher_pose(SW.curl, CFG) and switcher_gap(SW.curl) == pytest.approx(0.20)
    assert raw_pose(SW, CFG, -1) == "switcher"
    assert raw_pose(_sw(pinch=0.01), CFG, -1) == "switcher"  # ก่อน pinch
    assert raw_pose(_sw(facing=FLIP_FACING), CFG, -1) == "flip"  # flip สำคัญกว่า
    assert not is_switcher_pose(FIST.curl, CFG)  # กำมือไม่ใช่ท่า 3 นิ้ว
    assert not is_switcher_pose(NORMAL.curl, CFG)
    assert not is_switcher_pose(_sw(idx=0.5, mid=0.5, ring=0.5, pinky=0.2).curl, CFG)  # ชี้กลางนางงอ
    # ท่า 3 นิ้วที่ ring+pinky เฉลี่ยต่ำพอจะเป็น scroll ต้องไม่ถูกนับเป็น scroll
    odd = _sw(ring=0.8, pinky=0.6)
    assert is_switcher_pose(odd.curl, CFG) and not is_scroll_pose(odd.curl, CFG)
    assert is_scroll_pose(SCROLL.curl, CFG)


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_opens_only_after_hold_and_never_scrolls(fps):
    r = Rig(fps)
    r.feed_for(SW, 0.2)
    assert r.count("switcher_open") == 0 and r.states[-1].mode == "move"
    r.feed_for(SW, 0.25)  # รวม ~0.45 s (เริ่มนับที่เฟรมที่ confirm)
    assert r.count("switcher_open") == 1 and r.states[-1].mode == "switcher"
    assert "scroll_start" not in r.events()
    # ท่าที่ ring+pinky เฉลี่ยต่ำ (ปกติจะเป็น scroll) ก็ไม่ scroll ระหว่างนับค้าง
    r2 = Rig(fps)
    r2.feed_for(_sw(ring=0.8, pinky=0.6), 0.2)
    assert "scroll_start" not in r2.events()


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_open_time_matches_hold(fps):
    r = Rig(fps)
    start = r.ts
    r.feed_for(SW, 0.6)
    t_open = [t for t, e in r.log if e == "switcher_open"][0]
    assert 0.3 <= t_open - start <= 0.3 + 3 / fps + 1e-6


def test_switcher_closes_scroll_when_opening():
    r = Rig(30)
    r.feed_for(SCROLL, 0.3)
    assert r.t.scrolling
    # ท่า 3 นิ้วที่หลุดจาก scroll ไม่ได้ทันที: ข้ามตรง ๆ ผ่าน SW (scroll_hold ต้องหลุดก่อน)
    r.feed_for(SW, 0.6)
    assert r.count("switcher_open") == 1
    assert r.count("scroll_end") == 1 and not r.t.scrolling


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_not_opened_while_dragging(fps):
    r = Rig(fps)
    r.feed_for(_sw(pinch=0.02), 1.0)  # จีบ = drag
    assert r.count("drag_start") == 1
    assert r.count("switcher_open") == 0 and r.states[-1].mode == "drag"


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_not_opened_during_clutch_and_waits_after(fps):
    r = Rig(fps)
    r.feed_for(FIST, 0.4)
    assert r.t.clutch
    r.feed_for(SW, 0.25)  # ออกจาก clutch แล้วค่อยเริ่มนับ 0.3 s
    assert r.count("switcher_open") == 0
    r.feed_for(SW, 0.4)
    assert r.count("switcher_open") == 1


def test_switcher_not_opened_when_disabled():
    r = Rig(30, enabled=False)
    r.feed_for(SW, 1.0)
    assert r.events() == [] and r.states[-1].mode == "off"


def test_switcher_disabled_by_flag():
    t = GestureTracker(GestureConfig(), switcher_enabled=False)
    r = Rig(30)
    r.t = t
    r.feed_n(_g(facing=NORMAL_FACING), 62)
    r.feed_for(_g(facing=FLIP_FACING), 1.2)
    r.feed_n(_g(facing=NORMAL_FACING), 3)
    r.log.clear()
    r.feed_for(SW, 1.0)
    assert r.count("switcher_open") == 0
    # เปิดอยู่แล้วถูกปิดจากภายนอก → cancel
    r2 = Rig(30)
    _open_switcher(r2)
    r2.t.switcher_enabled = False
    r2.feed(SW)
    assert _sw_events(r2) == ["switcher_cancel"]


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_blurry_frames_do_not_reset_entry_timer(fps):
    r = Rig(fps)
    start = r.ts
    r.feed_for(SW, 0.1)
    r.feed(BLUR)          # เฟรมเบลอเดี่ยว: gap ต่ำแค่เฟรมเดียว
    r.feed_for(SEMI, 0.1)  # กึ่งท่า (gap 0.10 > hold) ก็ไม่รีเซ็ต
    r.feed_for(SW, 0.25)
    t_open = [t for t, e in r.log if e == "switcher_open"]
    assert len(t_open) == 1
    assert t_open[0] - start <= 0.3 + 0.1 + 3 / fps + 0.1 + 1e-6  # ไม่ต้องเริ่มนับใหม่ทั้งก้อน


def test_switcher_entry_timer_resets_after_real_release():
    r = Rig(30)
    r.feed_for(SW, 0.2)
    r.feed_for(NORMAL, 0.1)  # ปล่อยท่าจริง (หลายเฟรม)
    r.feed_for(SW, 0.2)
    assert r.count("switcher_open") == 0
    r.feed_for(SW, 0.2)
    assert r.count("switcher_open") == 1


def test_normal_hand_never_opens_switcher():
    r = Rig(30)
    for f in (NORMAL, _g(pinky=0.95, idx=0.95, mid=0.95, ring=0.95), _g(pinky=0.84), SCROLL, PINCH):
        r.feed_for(f, 1.5)
    assert r.count("switcher_open") == 0
    r2 = Rig(16)
    r2.feed_for(_sw(pinky=0.95), 3.0)
    assert r2.count("switcher_open") == 0


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_dx_sign_and_scale(fps):
    r = Rig(fps)
    _open_switcher(r, x=0.5)
    assert r.states[-1].switcher_dx == pytest.approx(0.0, abs=1e-6)
    # ขยับขวา 0.06 ของภาพ: 0.06*(4/3)/(0.4*0.2) = 1.0 การ์ด
    st = r.feed_for(_sw(x=0.56), 0.5)
    assert st.switcher_dx == pytest.approx(1.0, abs=0.01)
    st = r.feed_for(_sw(x=0.44), 0.5)
    assert st.switcher_dx == pytest.approx(-1.0, abs=0.01)
    assert st.mode == "switcher"
    assert _sw_events(r) == []


def test_switcher_dx_uses_aspect_and_is_zero_when_closed():
    r = Rig(30)
    assert r.states[-1].switcher_dx == 0.0
    ts = r.ts
    for _ in range(16):  # aspect เดียวกันตลอด (1.0) ตั้งแต่ก่อนเปิด
        ts += r.dt
        st = r.t.update(SW, ts, aspect=1.0)
    assert st.mode == "switcher"
    ts += r.dt
    st = r.t.update(_sw(x=0.56), ts, aspect=1.0)
    assert st.switcher_dx == pytest.approx(0.06 / (0.4 * 0.2), abs=0.01)
    st = r.t.update(None, ts + 0.5)  # ครั้งแรกที่หาย: ยังเปิด dx ตรึง
    assert st.switcher_dx == pytest.approx(0.75, abs=0.01)
    st = r.t.update(None, ts + 0.9)  # หายนาน → ยกเลิก dx = 0
    assert st.switcher_dx == 0.0 and st.events == ("switcher_cancel",)


@pytest.mark.parametrize("fps", SW_FPS)
@pytest.mark.parametrize("pinky", [0.70, 0.68, 0.72])
def test_switcher_blur_dropout_does_not_commit(fps, pinky):
    r = Rig(fps)
    _open_switcher(r)
    blur = _sw(idx=0.46, mid=0.55, ring=0.9, pinky=pinky)
    r.feed_for(blur, 0.25)
    r.feed_for(_sw(idx=0.5, mid=0.5, ring=0.5, pinky=pinky), 0.15)  # รวม ~0.4 s < 0.6 s
    assert _sw_events(r) == []
    assert not [e for e in r.events() if e.startswith("click")]
    r.feed_for(SW, 0.3)
    assert _sw_events(r) == [] and r.states[-1].mode == "switcher"


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_pinky_reextend_commits_after_exit_time(fps):
    r = Rig(fps)
    _open_switcher(r)
    t_first = r.ts + r.dt
    r.feed_for(NORMAL, 0.1)
    assert _sw_events(r) == []  # ยังไม่ถึง 0.15 s
    r.feed_for(NORMAL, 0.3)
    assert _sw_events(r) == ["switcher_commit"]
    t_commit = [t for t, e in r.log if e == "switcher_commit"][0]
    assert 0.15 - 1e-6 <= t_commit - t_first <= 0.15 + 2 / fps
    assert r.states[-1].mode == "move"


def test_switcher_single_open_pinky_frame_does_not_commit():
    r = Rig(30)
    _open_switcher(r)
    r.feed(NORMAL)
    r.feed_for(SW, 0.3)
    assert _sw_events(r) == []


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_fist_commits(fps):
    r = Rig(fps)
    _open_switcher(r)
    r.feed_for(FIST, 0.1)
    assert _sw_events(r) == []
    r.feed_for(FIST, 0.3)
    assert _sw_events(r) == ["switcher_commit"]


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_lost_pose_fallback_commits_after_0_6s(fps):
    r = Rig(fps)
    _open_switcher(r)
    t_last_pose = r.ts
    r.feed_for(AMBIG, 0.5)
    assert _sw_events(r) == []
    r.feed_for(AMBIG, 0.3)
    assert _sw_events(r) == ["switcher_commit"]
    t_commit = [t for t, e in r.log if e == "switcher_commit"][0]
    assert 0.6 - 1e-6 <= t_commit - t_last_pose <= 0.6 + 1.5 / fps


def test_switcher_semi_pose_frames_keep_fallback_alive():
    r = Rig(30)
    _open_switcher(r)
    r.feed_for(AMBIG, 0.4)
    r.feed(SEMI)  # gap 0.10 > 0.08 → ยังอยู่ในท่า
    r.feed_for(AMBIG, 0.4)
    assert _sw_events(r) == []


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_flip_cancels(fps):
    r = Rig(fps)
    _open_switcher(r)
    r.feed_for(_sw(facing=r.flip), 0.3)  # พลิกค้าง >= 0.25 s จึงยกเลิก
    assert _sw_events(r) == ["switcher_cancel"] and r.states[-1].mode == "flip"
    r.feed_for(_sw(facing=r.flip), 0.2)
    assert _sw_events(r) == ["switcher_cancel"]  # ไม่ซ้ำ


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_brief_flip_during_swipe_does_not_cancel(fps):
    r = Rig(fps)
    _open_switcher(r)
    for _ in range(4):  # ข้อมือบิด ~0.13 s ตอนสะบัด (facing +0.7)
        st = r.feed(_sw(facing=0.7))
        assert st.mode == "switcher"  # ยังไม่รายงาน flip
    r.feed_for(SW, 0.3)
    assert _sw_events(r) == [] and r.states[-1].mode == "switcher"
    # พลิกจริงค้าง 0.3 s ยังยกเลิก
    r.feed_for(_sw(facing=0.7), 0.3)
    assert _sw_events(r) == ["switcher_cancel"]


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_false_open_from_scroll_entry_gap(fps):
    # log 20261026-215533: gap > 0.15 แค่ 3 เฟรมแล้วค้างที่ 0.06–0.11 (เข้า scroll) ต้องไม่เปิด
    r = Rig(fps)
    r.feed_n(_sw(idx=0.95, mid=0.95, ring=0.95, pinky=0.78), 3)   # gap 0.17
    mid_gap = _sw(idx=0.95, mid=0.95, ring=0.90, pinky=0.80)      # gap 0.10
    r.feed_for(mid_gap, 1.0)
    assert r.count("switcher_open") == 0
    # ท่าจริงที่ gap เกินเกณฑ์ ~73% ของเฟรม (สลับกับ 0.10) ยังเปิดได้
    r2 = Rig(fps)
    for i in range(int(fps * 0.8)):
        r2.feed(SW if i % 4 != 3 else mid_gap)
    assert r2.count("switcher_open") == 1


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_no_commit_right_after_minimize_swipe_then_commit_when_settled(fps):
    r = Rig(fps)
    _open_switcher(r, y=0.7)
    y = _stroke(r, 0.7, -1)
    assert _sw_events(r) == ["switcher_minimize"]
    # ระหว่าง/หลังสะบัดนิ้วดูเหมือนมือปกติ (ก้อย 0.95) 0.3 s: ไม่ commit
    r.feed_for(_sw(y=y, idx=0.95, mid=0.95, ring=0.95, pinky=0.95), 0.3)
    assert _sw_events(r) == ["switcher_minimize"]
    # มือปกติค้างต่อหลังพ้นช่วง settle → commit ครั้งเดียว
    r.feed_for(_sw(y=y, pinky=0.95), 0.8)
    assert _sw_events(r) == ["switcher_minimize", "switcher_commit"]


def test_switcher_hand_lost_cancels_once_and_reopens_after_rehold():
    r = Rig(30)
    _open_switcher(r)
    t0 = r.ts
    st = r.t.update(None, t0 + 0.1)
    assert st.events == () and st.mode == "switcher"  # dropout สั้น: ยังเปิดอยู่
    assert r.t.update(None, t0 + 0.2).events == ()
    st = r.t.update(None, t0 + 0.45)  # หายเกิน 0.3 s
    assert st.events == ("switcher_cancel",) and st.mode == "move"
    assert r.t.update(None, t0 + 0.6).events == ()  # ไม่ซ้ำ
    r.ts = t0 + 0.7
    r.feed_for(SW, 0.2)
    assert r.count("switcher_open") == 0
    r.feed_for(SW, 0.3)
    assert r.count("switcher_open") == 1


def test_switcher_disable_cancels_exactly_once():
    r = Rig(30)
    _open_switcher(r)
    ev = r.t.disable()
    assert ev.count("switcher_cancel") == 1 and "toggle_off" in ev
    assert r.t.disable() == []
    st = r.feed(SW)
    assert st.events == () and st.mode == "off"
    assert not r.t.switcher_open


def test_switcher_toggle_request_cancels_once():
    r = Rig(30)
    _open_switcher(r)
    r.t.request_toggle()
    st = r.feed(SW)
    assert st.events.count("switcher_cancel") == 1 and "toggle_off" in st.events


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_swipe_up_minimizes_once_and_return_stroke_is_ignored(fps):
    r = Rig(fps)
    _open_switcher(r, x=0.5, y=0.7)
    y = _stroke(r, 0.7, -1, drift=0.02)
    assert _sw_events(r) == ["switcher_minimize"]
    # x ที่ลอยระหว่างสะบัดไม่ขยับ carousel (rebase)
    r.feed_for(_sw(x=0.52, y=y), 0.3)  # ตีกลับที่ ~0.5–0.7 s หลังเริ่มสะบัด (ภายใน 0.8 s)
    assert r.states[-1].switcher_dx == pytest.approx(0.0, abs=0.05)
    _stroke(r, y, +1, x=0.52)  # มือตีกลับลงภายใน 0.8 s
    r.feed_for(_sw(x=0.52, y=0.7), 0.3)
    assert _sw_events(r) == ["switcher_minimize"]
    assert r.states[-1].mode == "switcher"


def test_switcher_long_swipe_up_fires_once():
    r = Rig(30)
    _open_switcher(r, y=0.9)
    for i in range(1, 9):  # สะบัดต่อเนื่อง 8 เฟรม
        r.feed(_sw(y=0.9 - 0.05 * i))
    assert _sw_events(r) == ["switcher_minimize"]


def test_switcher_second_swipe_up_after_rearm():
    r = Rig(30)
    _open_switcher(r, y=0.9)
    y = _stroke(r, 0.9, -1)
    r.feed_for(_sw(y=y), 0.3)
    y = _stroke(r, y + 0.0, -1)
    assert _sw_events(r) == ["switcher_minimize", "switcher_minimize"]


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_swipe_down_cancels(fps):
    r = Rig(fps)
    _open_switcher(r, y=0.3)
    _stroke(r, 0.3, +1)
    assert _sw_events(r) == ["switcher_cancel"]
    assert r.states[-1].mode == "move"


def test_switcher_swipe_down_after_refractory_cancels():
    r = Rig(30)
    _open_switcher(r, y=0.7)
    y = _stroke(r, 0.7, -1)
    r.feed_for(_sw(y=y), 1.0)  # เกิน 0.8 s
    _stroke(r, y, +1)
    assert _sw_events(r) == ["switcher_minimize", "switcher_cancel"]


def test_switcher_slow_vertical_and_horizontal_moves_do_not_swipe():
    r = Rig(30)
    _open_switcher(r, x=0.3, y=0.5)
    for i in range(1, 31):  # ขยับซ้าย-ขวาเร็ว ๆ (vx ~ 5)
        r.feed(_sw(x=0.3 + 0.01 * i * (1 if i < 15 else -1), y=0.5))
    for i in range(1, 31):  # ขยับขึ้นช้า ๆ ~1.5 ขนาดมือ/วิ
        r.feed(_sw(y=0.5 - 0.01 * i))
    assert _sw_events(r) == []
    # สะบัดเฉียงที่แนวนอนเด่นกว่า (|vy| < 1.5|vx|) ไม่นับ
    for i in range(1, 5):
        r.feed(_sw(x=0.5 + 0.08 * i, y=0.2 - 0.05 * i))
    assert _sw_events(r) == []


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_cannot_reopen_until_pose_released(fps):
    r = Rig(fps)
    _open_switcher(r, y=0.3)
    _stroke(r, 0.3, +1)  # cancel ระหว่างที่ยังทำท่าอยู่
    assert _sw_events(r) == ["switcher_cancel"]
    r.feed_for(_sw(y=0.7), 1.0)  # ค้างท่าต่อ → ต้องไม่เปิดซ้ำ
    assert _sw_events(r) == ["switcher_cancel"]
    r.feed_n(NORMAL, 3)  # ปล่อยท่า
    r.feed_for(SW, 0.6)
    assert _sw_events(r) == ["switcher_cancel", "switcher_open"]


def test_switcher_cannot_reopen_after_commit_while_holding_fist_pose_then_ok():
    r = Rig(30)
    _open_switcher(r)
    r.feed_for(NORMAL, 0.4)
    assert _sw_events(r) == ["switcher_commit"]
    r.feed_for(SW, 0.6)  # ปล่อยท่าแล้ว (NORMAL หลายเฟรม) → เปิดใหม่ได้
    assert _sw_events(r)[-1] == "switcher_open"


def test_switcher_mode_blocks_clicks_and_clutch():
    r = Rig(30)
    _open_switcher(r)
    r.feed_for(_sw(idx=0.6, pinky=0.76), 0.2)   # ชี้งอ (ปกติ = คลิก)
    r.feed_for(SW, 0.2)
    assert [e for e in r.events() if e.startswith(("click", "clutch", "drag", "scroll"))] == []
    assert r.states[-1].mode == "switcher"


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_single_dropped_frame_keeps_open_and_freezes_dx(fps):
    r = Rig(fps)
    _open_switcher(r)
    st = r.feed_for(_sw(x=0.56), 0.4)
    assert st.switcher_dx == pytest.approx(1.0, abs=0.01)
    r.ts += r.dt
    st = r.t.update(None, r.ts)  # มือหาย 1 เฟรม
    assert st.events == () and st.mode == "switcher"
    assert st.switcher_dx == pytest.approx(1.0, abs=0.01)  # ตรึงค่าล่าสุด
    st = r.feed_for(_sw(x=0.56), 0.5)
    assert st.switcher_dx == pytest.approx(1.0, abs=0.02)
    assert _sw_events(r) == [] and r.states[-1].mode == "switcher"
    # หายนาน 0.35 s → ยกเลิกครั้งเดียว
    t = r.ts
    evs = []
    for k in range(1, 12):
        evs += list(r.t.update(None, t + 0.035 * k).events)
    assert evs == ["switcher_cancel"]


def test_switcher_short_loss_keeps_need_release():
    r = Rig(30)
    _open_switcher(r, y=0.3)
    _stroke(r, 0.3, +1)  # swipe down = cancel ขณะยังทำท่าอยู่
    assert _sw_events(r) == ["switcher_cancel"]
    r.ts += r.dt
    r.t.update(None, r.ts)  # หายสั้น ๆ ไม่ล้างเงื่อนไข "ต้องปล่อยท่า"
    r.feed_for(_sw(y=0.7), 1.0)
    assert _sw_events(r) == ["switcher_cancel"]


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_flip_with_open_hand_cancels_not_commits(fps):
    r = Rig(fps)
    _open_switcher(r)
    r.feed_for(_sw(pinky=0.95, facing=r.flip), 0.6)  # พลิกมือกางปกติ
    assert _sw_events(r) == ["switcher_cancel"]


@pytest.mark.parametrize("fps", SW_FPS)
def test_switcher_flip_cancel_does_not_continue_to_toggle(fps):
    r = Rig(fps)
    _open_switcher(r)
    r.feed_for(_sw(facing=r.flip), 1.2)  # ค้าง > toggle_hold_ms
    assert r.events() == ["switcher_cancel"] and r.states[-1].enabled
    # พลิกใหม่ครั้งใหม่ toggle ได้ตามปกติ
    r.feed_n(_g(facing=r.normal), 3)
    r.feed_for(_g(facing=r.flip), 1.0)
    assert r.events() == ["switcher_cancel", "toggle_off"]
