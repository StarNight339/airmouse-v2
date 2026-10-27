import logging
import threading
import time

import numpy as np
import pytest

from airmouse.app import InferenceThread
from airmouse import camera as camera_mod
from airmouse.camera import Camera
from airmouse.config import CameraConfig
from airmouse.timing import RateMeter
from airmouse.tracker import TrackedHand

W, H = 8, 4


def _frame(fill: int = 0) -> np.ndarray:
    f = np.full((H, W, 3), fill, np.uint8)
    f[:, 0] = (10, 20, 30)  # คอลัมน์ซ้ายสุดมีสีเฉพาะ ไว้ตรวจการ flip
    return f


class FakeCamera:
    def __init__(self, frames=(), error=None):
        self.frames = list(frames)  # รายการ (frame, ts)
        self.error = error
        self.fps = RateMeter()
        self.fps.rate = 25.0

    def wait_frame(self, last_seq, timeout):
        if last_seq < len(self.frames):
            frame, ts = self.frames[last_seq]
            return last_seq + 1, frame, ts
        time.sleep(0.005)
        return None


class FakeTracker:
    def __init__(self, hands=None, exc=None):
        self.hands = hands or {}
        self.exc = exc
        self.calls = []

    def process(self, rgb, ts):
        self.calls.append((rgb.copy(), ts))
        if self.exc:
            raise self.exc
        return self.hands.get(len(self.calls) - 1)


def _hand() -> TrackedHand:
    pts = [(0.1 + 0.01 * i, 0.2 + 0.02 * i, 0.0) for i in range(21)]
    return TrackedHand(landmarks=pts, world=[(x * 0.1, y * 0.1, z) for x, y, z in pts],
                       handedness="right", score=0.9)


def _wait_for(pred, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.002)
    return False


@pytest.fixture
def run_thread():
    started = []

    def _run(cam, trk, mirror=False, debug=False):
        t = InferenceThread(cam, trk, mirror, debug)
        started.append(t)
        t.start()
        return t

    yield _run
    for t in started:
        t.stop()
        t.join(timeout=1)


def test_latest_none_before_frames(run_thread):
    t = run_thread(FakeCamera(), FakeTracker())
    time.sleep(0.02)
    assert t.latest() is None


def test_results_have_seq_ts_and_features(run_thread):
    cam = FakeCamera([(_frame(), 1.0), (_frame(), 1.1), (_frame(), 1.2)])
    trk = FakeTracker(hands={0: _hand(), 2: _hand()})
    t = run_thread(cam, trk)
    assert _wait_for(lambda: t.latest() is not None and t.latest().seq == 3)
    r = t.latest()
    assert r.ts == 1.2
    assert r.hand is not None and r.features is not None
    assert r.camera_fps == 25.0
    assert r.infer_ms >= 0
    assert [c[1] for c in trk.calls] == [1.0, 1.1, 1.2]
    assert t.error is None


def test_features_none_when_no_hand(run_thread):
    t = run_thread(FakeCamera([(_frame(), 2.0)]), FakeTracker())
    assert _wait_for(lambda: t.latest() is not None)
    r = t.latest()
    assert r.seq == 1 and r.hand is None and r.features is None


def test_features_computed_for_hand(run_thread):
    t = run_thread(FakeCamera([(_frame(), 2.0)]), FakeTracker(hands={0: _hand()}))
    assert _wait_for(lambda: t.latest() is not None)
    f = t.latest().features
    assert set(f.curl) == {"thumb", "index", "middle", "ring", "pinky"}
    assert f.hand_size > 0


def test_infer_fps_updates(run_thread):
    cam = FakeCamera([(_frame(), i * 0.1) for i in range(5)])
    t = run_thread(cam, FakeTracker())
    assert _wait_for(lambda: t.latest() is not None and t.latest().seq == 5)
    assert t.latest().infer_fps == pytest.approx(10.0)


def test_mirror_flips_frame(run_thread):
    trk = FakeTracker()
    t = run_thread(FakeCamera([(_frame(), 1.0)]), trk, mirror=True)
    assert _wait_for(lambda: t.latest() is not None)
    r = t.latest()
    assert tuple(r.frame[0, -1]) == (10, 20, 30)
    assert tuple(r.frame[0, 0]) == (0, 0, 0)
    # tracker ได้รับ RGB ของภาพที่กลับด้านแล้ว
    assert tuple(trk.calls[0][0][0, -1]) == (30, 20, 10)


def test_no_mirror_keeps_frame(run_thread):
    t = run_thread(FakeCamera([(_frame(), 1.0)]), FakeTracker(), mirror=False)
    assert _wait_for(lambda: t.latest() is not None)
    assert tuple(t.latest().frame[0, 0]) == (10, 20, 30)


def test_stop_join_is_prompt(run_thread):
    t = run_thread(FakeCamera(), FakeTracker())
    time.sleep(0.02)
    t0 = time.monotonic()
    t.stop()
    t.join(timeout=1)
    assert not t.is_alive()
    assert time.monotonic() - t0 < 1.0
    assert t.error is None


def test_camera_error_stops_thread(run_thread):
    t = run_thread(FakeCamera(error="ถูกถอด"), FakeTracker())
    t.join(timeout=1)
    assert not t.is_alive()
    assert "ถูกถอด" in t.error


def test_camera_error_after_running(run_thread):
    cam = FakeCamera([(_frame(), 1.0)])
    t = run_thread(cam, FakeTracker())
    assert _wait_for(lambda: t.latest() is not None)
    cam.error = "lost"
    t.join(timeout=1)
    assert not t.is_alive() and "lost" in t.error
    assert t.latest().seq == 1  # ผลล่าสุดยังอ่านได้


def test_tracker_exception_captured(run_thread):
    t = run_thread(FakeCamera([(_frame(), 1.0)]), FakeTracker(exc=ValueError("boom")))
    t.join(timeout=1)
    assert not t.is_alive()
    assert t.error == "boom"
    assert t.latest() is None


def test_debug_mode_logs_stats(run_thread, caplog):
    # _DebugStats นับเวลาจาก time.monotonic() จึงต้องใช้ timestamp ฐานเดียวกัน
    t0 = time.monotonic()
    cam = FakeCamera([(_frame(), t0 + i * 0.3) for i in range(8)])
    with caplog.at_level(logging.DEBUG, logger="airmouse.app"):
        t = run_thread(cam, FakeTracker(), debug=True)
        assert _wait_for(lambda: t.latest() is not None and t.latest().seq == 8)
    assert t.error is None
    assert any("fps cam=" in r.getMessage() for r in caplog.records)


# --- Camera (ไม่เรียก start() ไม่เปิดฮาร์ดแวร์ ขับ state ภายในโดยตรง) ---

def _cam() -> Camera:
    return Camera(CameraConfig())


def test_camera_wait_frame_timeout_returns_none():
    t0 = time.monotonic()
    assert _cam().wait_frame(0, timeout=0.05) is None
    assert time.monotonic() - t0 < 0.5


def test_camera_wait_frame_returns_new_frame():
    cam = _cam()
    f = _frame()
    cam._publish(f, 3.0)
    seq, frame, ts = cam.wait_frame(0, timeout=0.5)
    assert (seq, ts) == (1, 3.0) and frame is f
    assert cam.wait_frame(1, timeout=0.02) is None


def test_camera_wait_frame_woken_by_publisher():
    cam = _cam()
    threading.Timer(0.03, cam._publish, (_frame(), 1.0)).start()
    t0 = time.monotonic()
    got = cam.wait_frame(0, timeout=2.0)
    assert got is not None and got[0] == 1
    assert time.monotonic() - t0 < 1.0


def test_camera_stop_wakes_waiter():
    cam = _cam()
    threading.Timer(0.03, cam.stop).start()
    t0 = time.monotonic()
    assert cam.wait_frame(0, timeout=2.0) is None
    assert time.monotonic() - t0 < 1.0


def test_camera_publish_marks_opened_and_size():
    cam = _cam()
    assert cam.wait_opened(0.01) is False
    cam._publish(np.zeros((6, 10, 3), np.uint8), 1.0)
    assert cam.wait_opened(0.01) is True
    assert cam.size == (10, 6)


class FakeCapture:
    instances: list["FakeCapture"] = []

    def __init__(self, *_args, opened=True, set_exc=None):
        self.opened = opened
        self.set_exc = set_exc
        self.released = False
        FakeCapture.instances.append(self)

    def isOpened(self):
        return self.opened

    def set(self, *_args):
        if self.set_exc:
            raise self.set_exc
        return True

    def get(self, _prop):
        return 0

    def release(self):
        self.released = True


@pytest.fixture
def fake_capture(monkeypatch):
    FakeCapture.instances = []

    def install(**kwargs):
        monkeypatch.setattr(camera_mod.cv2, "VideoCapture",
                            lambda *a: FakeCapture(*a, **kwargs))
        return FakeCapture.instances

    return install


def test_camera_open_failure_sets_error_and_opens_once(fake_capture):
    caps = fake_capture(opened=False)
    cam = _cam()
    cam.run()  # เรียกตรงๆ ใน thread นี้ (ไม่ start)
    assert len(caps) == 1 and caps[0].released
    assert cam.error and cam.wait_opened(0.01) is False


def test_camera_open_releases_capture_on_driver_exception(fake_capture):
    caps = fake_capture(set_exc=RuntimeError("driver"))
    cam = _cam()
    cam.run()
    assert len(caps) == 1 and caps[0].released
    assert cam.error == "driver"


def test_camera_wait_opened_semantics():
    cam = _cam()
    cam.error = "x"
    cam._opened.set()
    assert cam.wait_opened(0.01) is False
    assert _cam().wait_opened(0.01) is False


# --- Controller (ท่ามือ → motion → mouse) ---

from airmouse.app import Controller  # noqa: E402
from airmouse.features import HandFeatures  # noqa: E402
from airmouse.gestures import GestureState  # noqa: E402


class FakeGestures:
    def __init__(self, states):
        self.states = list(states)

    def update(self, feats, ts, aspect=4 / 3):
        return self.states.pop(0)


class FakeMotion:
    speed = gain = 0.0

    def __init__(self):
        self.resets = []
        self.updates = []
        self.history = {}  # ts -> ตำแหน่ง
        self.positions = []
        self.boosts = []
        self.scroll_resets = 0
        self.scroll_out = []

    def reset(self, xy):
        self.resets.append(xy)

    def update(self, anchor, hand_size, aspect, dt, ts=None):
        self.updates.append((anchor, dt))
        out = (100.0 + len(self.updates), 50.0)
        if ts is not None:
            self.history[ts] = out
        return out

    def history_at(self, t):
        older = [k for k in self.history if k <= t]
        return self.history[max(older)] if older else self.history[min(self.history)]

    def set_position(self, xy):
        self.positions.append(xy)

    def set_shown(self, xy):
        self.shown = xy

    def boost_smoothing(self, seconds):
        self.boosts.append(seconds)

    def reset_scroll(self):
        self.scroll_resets += 1

    def scroll_delta(self, anchor_y, hand_size, dt):
        return self.scroll_out.pop(0) if self.scroll_out else 0


class FakeMouse:
    def __init__(self):
        self.user_moved = False
        self.targets = []
        self.active = []
        self.resyncs = 0
        self.cmds = []

    def resync(self):
        self.resyncs += 1
        self.user_moved = False
        return (10.0, 20.0)

    def set_target(self, x, y, frame_interval):
        self.targets.append((x, y, frame_interval))

    def set_active(self, active):
        self.active.append(active)

    # คำสั่งปุ่ม/scroll เก็บเป็นลำดับเดียว เพื่อตรวจลำดับได้
    def click_at(self, x, y, btn="left"):
        self.cmds.append(("click", btn, (x, y)))

    def button_down(self, btn):
        self.cmds.append(("down", btn))

    def button_up(self, btn):
        self.cmds.append(("up", btn))

    def scroll(self, delta):
        self.cmds.append(("scroll", delta))


def _feats():
    return HandFeatures(curl={}, pinch=0.1, hand_size=0.2, anchor=(0.5, 0.5))


ON = GestureState(True, False, 0.0, False, False, mode="move")
OFF = GestureState(False, False, 0.0, False, False, mode="off")
CLUTCH = GestureState(True, True, 0.0, False, True, mode="clutch")


def _on(*events, mode="move", bend=None):
    return GestureState(True, False, 0.0, False, False, events=tuple(events), mode=mode,
                        click_bend_ts=bend)


def _controller(states):
    m, mouse = FakeMotion(), FakeMouse()
    return Controller(FakeGestures(states), m, mouse), m, mouse


def test_controller_disabled_never_moves():
    c, m, mouse = _controller([OFF, OFF])
    c.step(_feats(), 1.0, 4 / 3)
    c.step(_feats(), 1.033, 4 / 3)
    assert mouse.targets == [] and m.updates == []
    assert mouse.active == [False, False]


def test_controller_resyncs_when_control_starts_then_moves():
    c, m, mouse = _controller([OFF, ON, ON])
    c.step(_feats(), 1.0, 4 / 3)
    c.step(_feats(), 1.05, 4 / 3)
    c.step(_feats(), 1.10, 4 / 3)
    assert m.resets == [(10.0, 20.0)] and mouse.resyncs == 1
    assert len(mouse.targets) == 2
    assert mouse.targets[-1][2] == pytest.approx(0.05)  # frame interval = dt ของกล้อง
    assert mouse.active == [False, True, True]


def test_controller_clutch_stops_and_release_resyncs():
    c, m, mouse = _controller([ON, CLUTCH, CLUTCH, ON])
    for i in range(4):
        c.step(_feats(), 1.0 + i * 0.033, 4 / 3)
    assert mouse.active == [True, False, False, True]
    assert len(m.resets) == 2  # ตอนเริ่ม และตอนปล่อย clutch
    assert len(mouse.targets) == 2


def test_controller_hand_lost_deactivates_and_resyncs_on_return():
    c, m, mouse = _controller([ON, ON, ON])
    c.step(_feats(), 1.0, 4 / 3)
    c.step(None, 1.033, 4 / 3)
    c.step(_feats(), 1.066, 4 / 3)
    assert mouse.active == [True, False, True]
    assert len(m.resets) == 2


def test_controller_user_mouse_pauses_hand_then_resumes_from_cursor():
    c, m, mouse = _controller([ON] * 4)
    c.step(_feats(), 1.0, 4 / 3)
    mouse.user_moved = True
    c.step(_feats(), 1.033, 4 / 3)            # ผู้ใช้ขยับเมาส์จริง → หยุดทันที
    assert not mouse.user_moved and mouse.active[-1] is False
    c.step(_feats(), 1.2, 4 / 3)              # ยังอยู่ในช่วงพัก
    assert mouse.active[-1] is False and len(mouse.targets) == 1
    c.step(_feats(), 1.5, 4 / 3)              # พ้นช่วงพัก → ตั้งต้นใหม่จาก cursor
    assert mouse.active[-1] is True
    assert len(m.resets) == 2 and len(mouse.targets) == 2


def test_controller_follows_screen_changes():
    c, m, mouse = _controller([ON])
    mouse.screen = ("new-screen",)
    c.step(_feats(), 1.0, 4 / 3)
    assert m.bounds == ("new-screen",)


# --- Phase 3: คลิก / ลาก / scroll ---

def test_click_uses_position_from_before_finger_bend():
    c, m, mouse = _controller([ON, ON, ON, _on("click_left", bend=1.1)])
    for i, ts in enumerate((1.0, 1.1, 1.2, 1.3)):
        c.step(_feats(), ts, 4 / 3)
    # history: 1.0→101, 1.1→102, 1.2→103, 1.3→104 ; bend ที่ 1.1 (อยู่ในช่วง rollback 150 ms)
    assert mouse.cmds == [("click", "left", (102.0, 50.0))]
    assert m.positions == [(102.0, 50.0)]


def test_click_rollback_is_capped():
    c, m, mouse = _controller([ON, ON, ON, ON, _on("click_right", bend=0.5)])
    for ts in (1.0, 1.1, 1.2, 1.3, 1.4):
        c.step(_feats(), ts, 4 / 3)
    # bend เก่าเกิน → ย้อนได้ไม่เกิน 150 ms (1.25 → ตัวอย่างที่ 1.2)
    assert mouse.cmds == [("click", "right", (103.0, 50.0))]


def test_double_click_reuses_first_position():
    c, m, mouse = _controller([ON, _on("click_left", bend=1.0), ON, _on("click_left", bend=1.2)])
    for ts in (1.0, 1.05, 1.2, 1.3):
        c.step(_feats(), ts, 4 / 3)
    (_, _, p1), (_, _, p2) = mouse.cmds
    assert p1 == p2  # ห่าง ≤ 4 px และภายใน 500 ms → จุดเดิม


def test_drag_down_up_and_boost():
    c, m, mouse = _controller([ON, _on("drag_start", mode="drag"), _on(mode="drag"),
                               _on("drag_end")])
    for ts in (1.0, 1.1, 1.2, 1.3):
        c.step(_feats(), ts, 4 / 3)
    assert mouse.cmds == [("down", "left"), ("up", "left")]
    assert m.boosts == [0.15]


def test_hand_lost_while_dragging_releases_button():
    c, m, mouse = _controller([ON, _on("drag_start", mode="drag"), ON])
    c.step(_feats(), 1.0, 4 / 3)
    c.step(_feats(), 1.1, 4 / 3)
    c.step(None, 1.2, 4 / 3)
    assert mouse.cmds == [("down", "left"), ("up", "left")]


def test_user_mouse_while_dragging_releases_button():
    c, m, mouse = _controller([_on("drag_start", mode="drag"), _on(mode="drag")])
    c.step(_feats(), 1.0, 4 / 3)
    mouse.user_moved = True
    c.step(_feats(), 1.1, 4 / 3)
    assert mouse.cmds == [("down", "left"), ("up", "left")]


def test_scroll_sends_wheel_and_freezes_cursor_then_resyncs():
    c, m, mouse = _controller([ON, _on("scroll_start", mode="scroll"), _on(mode="scroll"),
                               _on("scroll_end"), ON])
    m.scroll_out = [0, 120]
    for ts in (1.0, 1.1, 1.2, 1.3, 1.4):
        c.step(_feats(), ts, 4 / 3)
    assert mouse.cmds == [("scroll", 120)]
    assert m.scroll_resets == 1
    assert len(mouse.targets) == 3  # ไม่มี set_target ระหว่าง scroll 2 เฟรม
    assert len(m.resets) == 2       # ตอนเริ่ม และหลังจบ scroll


def test_controller_activates_mouse_before_enqueuing_click():
    c, m, mouse = _controller([_on("click_left", bend=1.0)])
    order = []
    mouse.set_active = lambda a: order.append(("active", a))
    mouse.click_at = lambda x, y, btn="left": order.append(("click", btn))
    c.step(_feats(), 1.0, 4 / 3)
    assert order == [("active", True), ("click", "left")]


def test_flip_mode_freezes_cursor_and_resyncs_after():
    flip = GestureState(True, False, 0.5, True, False, mode="flip")
    c, m, mouse = _controller([ON, flip, flip, ON])
    for ts in (1.0, 1.1, 1.2, 1.3):
        c.step(_feats(), ts, 4 / 3)
    assert mouse.active == [True, False, False, True]
    assert len(mouse.targets) == 2   # ไม่ขยับระหว่างพลิก
    assert len(m.resets) == 2        # พลิกกลับแล้วตั้งต้นจาก cursor ใหม่


# --- PoseRecorder (--log-pose) ---

def test_pose_recorder_writes_jsonl_and_replays(tmp_path):
    import json

    from airmouse.app import PoseRecorder
    from airmouse.gestures import GestureTracker
    from airmouse.config import GestureConfig

    hand = _hand()
    rec = PoseRecorder(tmp_path / "p.jsonl")
    g = GestureState(True, False, 0.0, False, False, events=("click_left",), mode="move")
    rec.write(1.0, 4 / 3, True, hand, g)
    rec.write(1.1, 4 / 3, True, None, None)
    rec.close()
    lines = [json.loads(x) for x in (tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    a, b = lines
    assert a["hand"] == "right" and len(a["lm"]) == 21 and len(a["world"]) == 21
    assert a["ev"] == ["click_left"] and a["mode"] == "move"
    assert b["hand"] is None
    # ค่าที่บันทึกต้องคำนวณ features ซ้ำได้ (ใช้ replay)
    from airmouse import features
    f = features.compute(a["lm"], a["world"], a["aspect"], a["hand"], a["mirror"])
    ref = features.compute(hand.landmarks, hand.world, 4 / 3, "right", True)
    assert f.pinch == pytest.approx(ref.pinch, abs=1e-4)
    t = GestureTracker(GestureConfig())
    t.update(f, a["t"])
    t.update(None, b["t"])


def test_cursor_record_includes_snap_state():
    from types import SimpleNamespace

    from airmouse.app import _cursor_record

    ctrl = SimpleNamespace(snap_virtual=None, hovered=None)
    assert _cursor_record(None, (1.0, 2.0), 0.5) is None
    assert _cursor_record(ctrl, None, 0.5) is None
    assert _cursor_record(ctrl, (10.04, 20.0), 0.12345) == {"cur": [10.0, 20.0], "spd": 0.123}
    ctrl = SimpleNamespace(snap_virtual=(12.0, 21.0),
                           hovered=SimpleNamespace(left=0, top=1, right=30, bottom=31))
    r = _cursor_record(ctrl, (10.0, 20.0), 0.2)
    assert r["virt"] == [12.0, 21.0] and r["snap"] == [0, 1, 30, 31]


def test_inference_thread_records_pose_when_enabled(tmp_path, monkeypatch, run_thread):
    from airmouse import config as config_mod
    monkeypatch.setattr(config_mod, "LOGS_DIR", tmp_path)
    cam = FakeCamera([(_frame(), 1.0), (_frame(), 1.1)])
    t = InferenceThread(cam, FakeTracker(hands={0: _hand()}), False, False, log_pose=True)
    t.start()
    assert _wait_for(lambda: t.latest() is not None and t.latest().seq == 2)
    t.stop()
    t.join(timeout=1)
    files = list(tmp_path.glob("*-pose.jsonl"))
    assert len(files) == 1
    assert len(files[0].read_text(encoding="utf-8").splitlines()) == 2


# --- Phase 4: lifecycle ใน InferenceThread ---

class CountingTracker(FakeTracker):
    """นับจำนวนเฟรมที่รัน inference จริง (ใช้วัดการข้ามเฟรมช่วงประหยัดพลังงาน)"""


def _lifecycle(low_power_after_s=0.3, auto_exit_minutes=0.0):
    from airmouse.config import AppConfig
    from airmouse.lifecycle import Lifecycle
    return Lifecycle(AppConfig(auto_exit_minutes=auto_exit_minutes,
                               low_power_after_s=low_power_after_s))


def test_low_power_throttles_inference_and_disables_control():
    from airmouse.app import Controller
    from airmouse.config import GestureConfig
    from airmouse.gestures import GestureTracker

    gestures = GestureTracker(GestureConfig())
    gestures.enabled = True
    ctrl = Controller(gestures, FakeMotion(), FakeMouse())
    # 30 fps นาน 2 วิ ไม่มีมือเลย → low_power ที่ 0.3 วิ แล้วเหลือ ~6 fps
    frames = [(_frame(), 1.0 + i / 30) for i in range(60)]
    trk = CountingTracker()
    t = InferenceThread(FakeCamera(frames), trk, False, False, ctrl,
                        lifecycle=_lifecycle(), low_power_fps=6.0)
    t.start()
    assert _wait_for(lambda: t.latest() is not None and t.latest().ts >= frames[-6][1], 2.0)
    t.stop()
    t.join(timeout=1)
    events = t.drain_events()
    assert "paused" in events and "low_power" in events and "toggle_off" in events
    assert not gestures.enabled
    # ก่อน low_power ~9 เฟรม + หลังจากนั้น ~1.7 วิ × 6 fps ≈ 10 → ไม่ใช่ 60
    assert len(trk.calls) < 30
    assert t.latest().life.state == "low_power"


def test_exit_event_reaches_main_thread_queue():
    frames = [(_frame(), 1.0 + i * 0.1) for i in range(20)]
    t = InferenceThread(FakeCamera(frames), FakeTracker(), False, False, None,
                        lifecycle=_lifecycle(low_power_after_s=100, auto_exit_minutes=1 / 60))
    t.start()
    assert _wait_for(lambda: t.latest() is not None and t.latest().seq == 20, 2.0)
    t.stop()
    t.join(timeout=1)
    assert t.drain_events().count("exit") == 1
    assert t.drain_events() == []


# --- Snap (เกาะปุ่ม) ใน Controller ---

class FakeSnapper:
    def __init__(self, enabled=True):
        from types import SimpleNamespace
        self.cfg = SimpleNamespace(enabled=enabled)
        self.calls = []
        self.resets = 0
        self.offset = (0.0, 0.0)

    def adjust(self, prev, target, speed, dt, targets):
        self.calls.append((prev, target, list(targets)))
        # หนืดลด virtual 1 px และแสดงถูกดึงเพิ่มอีก 1 px
        self.offset = (-1.0, 0.0)
        virtual = (target[0] - 1.0, target[1])
        return virtual, (virtual[0] - 1.0, virtual[1]), "BTN"

    def reset(self):
        self.resets += 1
        self.offset = (0.0, 0.0)

    def clear_offset(self):
        self.offset = (0.0, 0.0)


class FakeFinder:
    def __init__(self):
        self.targets = ["T1"]
        self.probes = []
        self.active = []

    def set_probe(self, x, y):
        self.probes.append((x, y))

    def set_active(self, on):
        self.active.append(on)


def _snap_controller(states, enabled=True):
    m, mouse = FakeMotion(), FakeMouse()
    s, f = FakeSnapper(enabled), FakeFinder()
    return Controller(FakeGestures(states), m, mouse, snapper=s, finder=f), m, mouse, s, f


def test_snap_adjusts_target_and_syncs_motion_in_move_mode():
    c, m, mouse, s, f = _snap_controller([ON, ON])
    c.step(_feats(), 1.0, 4 / 3)
    c.step(_feats(), 1.1, 4 / 3)
    assert mouse.targets[-1][:2] == (100.0, 50.0)  # motion 102 → virtual 101 → แสดง 100
    assert m.positions[-1] == (101.0, 50.0)          # ป้อนกลับแค่ virtual ไม่ใช่ตำแหน่งที่ดูด
    assert m.shown == (100.0, 50.0)                  # history = ตำแหน่งที่แสดง (ใช้ย้อนคลิก)
    assert s.calls[-1][0] == (100.0, 50.0)           # prev = virtual ของเฟรมก่อน (101 - 1)
    assert c.hovered == "BTN" and f.probes[-1] == (101.0, 50.0) and f.active[-1] is True
    assert s.calls[-1][2] == ["T1"]


def test_snap_off_while_dragging_and_when_disabled():
    c, m, mouse, s, f = _snap_controller([ON, _on("drag_start", mode="drag"), _on(mode="drag")])
    for ts in (1.0, 1.1, 1.2):
        c.step(_feats(), ts, 4 / 3)
    assert len(s.calls) == 1            # เฉพาะเฟรม move แรก
    assert c.hovered is None and f.active[-1] is False
    # เฟรมเริ่มลาก: ฝัง offset ดึง (-1) เข้า motion ครั้งเดียว cursor ไม่กระโดดออกจากปุ่ม
    assert mouse.targets[1][:2] == (101.0, 50.0) and m.positions[-1] == (101.0, 50.0)
    assert mouse.targets[2][:2] == (103.0, 50.0)  # เฟรมถัดไป FakeMotion ไม่ได้ย้ายจริง
    c2, m2, mouse2, s2, f2 = _snap_controller([ON, ON], enabled=False)
    c2.step(_feats(), 1.0, 4 / 3)
    c2.step(_feats(), 1.1, 4 / 3)
    assert s2.calls == [] and mouse2.targets[-1][:2] == (102.0, 50.0)


def test_snap_click_moves_virtual_to_click_point_without_bounce():
    c, m, mouse, s, f = _snap_controller([ON, ON, _on("click_left")])
    for ts in (1.0, 1.1, 1.2):
        c.step(_feats(), ts, 4 / 3)
    pos = mouse.cmds[-1][2]
    assert m.positions[-1] == pos and c._snap_prev == pos and s.offset == (0.0, 0.0)


def test_snap_resets_when_hand_lost():
    c, m, mouse, s, f = _snap_controller([ON, ON])
    c.step(_feats(), 1.0, 4 / 3)
    r0 = s.resets  # เฟรมแรก reset ไปแล้วหนึ่งครั้งตอนตั้งต้น motion
    c.step(None, 1.1, 4 / 3)
    assert c.hovered is None and s.resets == r0 + 1 and f.active[-1] is False


# --- Snap แบบ closed loop: Controller + Motion + Snapper จริง ---

from dataclasses import dataclass as _dataclass  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from airmouse.config import MotionConfig, SnapConfig  # noqa: E402
from airmouse.motion import Motion  # noqa: E402
from airmouse.snap import HYSTERESIS, Snapper  # noqa: E402


@_dataclass(frozen=True)
class _Btn:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)


class _MouseAt(FakeMouse):
    def __init__(self, xy):
        super().__init__()
        self.xy = xy

    def resync(self):
        super().resync()
        return self.xy


def _closed_loop(buttons, start_xy, hand_speed, fps, seconds):
    """มือขยับไปทางขวาด้วยความเร็วคงที่ (hand-size/s) คืนลำดับ (out, hovered) ทุกเฟรม"""
    n = int(seconds * fps)
    motion = Motion(MotionConfig(), SimpleNamespace(x=0, y=0, w=1920, h=1080), 1080.0)
    finder = FakeFinder()
    finder.targets = list(buttons)
    c = Controller(FakeGestures([ON] * n), motion, _MouseAt(start_xy),
                   snapper=Snapper(SnapConfig()), finder=finder)
    aspect, size, dt = 4 / 3, 0.2, 1 / fps
    ax = 0.3
    seq = []
    for i in range(n):
        ax += hand_speed * size * dt / aspect
        feats = HandFeatures(curl={}, pinch=0.1, hand_size=size, anchor=(ax, 0.5))
        _, out = c.step(feats, 1.0 + i * dt, aspect)
        seq.append((out, c.hovered))
    return seq


@pytest.mark.parametrize("fps", [10, 30])
@pytest.mark.parametrize("hand_speed", [0.2, 0.5])  # ช้ากว่า slow_speed ทั้งคู่ (โดนดูดตลอด)
def test_snap_slow_hand_escapes_small_button(fps, hand_speed):
    btn = _Btn(104, 99, 136, 131)  # 32x32 กลาง (120, 115)
    seq = _closed_loop([btn], btn.center, hand_speed, fps, seconds=4.0)
    assert seq[0][1] is btn
    exit_x = btn.right + SnapConfig().capture_px * HYSTERESIS
    (x, _), hov = seq[-1]
    assert hov is None and x > exit_x  # หลุดออกมาได้ ไม่ค้างที่ปุ่ม


@pytest.mark.parametrize("fps", [10, 30])
def test_snap_slow_sweep_over_button_row_does_not_stick_to_last(fps):
    # แถบปุ่ม 32 px ติดกัน ช่องว่าง 4 px (เช่น toolbar)
    row = [_Btn(104 + i * 36, 99, 136 + i * 36, 131) for i in range(5)]
    seq = _closed_loop(row, row[0].center, 0.3, fps, seconds=8.0)
    visited = []
    for _, hov in seq:
        if hov is not None and (not visited or visited[-1] is not hov):
            visited.append(hov)
    assert visited == row  # เกาะทีละปุ่มตามลำดับ ไม่ย้อน ไม่ข้าม
    (x, _), hov = seq[-1]
    assert hov is None and x > row[-1].right + SnapConfig().capture_px * HYSTERESIS
    xs = [out[0] for out, _ in seq]
    assert all(b >= a - 1.0 for a, b in zip(xs, xs[1:]))  # cursor ไม่ถอยสวนทิศมือ


# --- Swipe Switcher: Controller + _SwitcherUI (ของปลอม ไม่เปิดหน้าต่าง/สลับจริง) ---

def test_controller_freezes_cursor_while_switcher_open():
    c, m, mouse = _controller([ON, _on("switcher_open", mode="switcher"),
                               _on(mode="switcher"), _on("switcher_commit"), ON])
    for i in range(5):
        c.step(_feats(), 1.0 + i * 0.1, 4 / 3)
    assert len(m.updates) == 3            # เฟรม move 1 + หลังปิด 2 เท่านั้น
    assert mouse.active[1:3] == [False, False]
    assert len(m.resets) == 2             # ปิด switcher แล้วเริ่มจากตำแหน่ง cursor จริง


def test_inference_emit_routes_switcher_events_with_dx():
    from collections import deque

    t = InferenceThread.__new__(InferenceThread)
    t._events, t._sw_events = deque(), deque()
    t._emit(("click_left", "switcher_minimize"), 1.5)
    assert t.drain_events() == ["click_left", "switcher_minimize"]
    assert t.drain_switcher_events() == [("switcher_minimize", 1.5)]


class _FakeWin32:
    def __init__(self, n):
        self.wins = [SimpleNamespace(hwnd=100 + i, title=f"w{i}", exe="a.exe") for i in range(n)]
        self.minimized, self.activated = [], []

    def list_switch_windows(self, pid):
        return list(self.wins)

    def minimize(self, hwnd):
        self.minimized.append(hwnd)

    def activate(self, hwnd):
        self.activated.append(hwnd)
        return True


class _FakeSwitcherWindow:
    def __init__(self):
        self.is_open = False
        self.removed, self.positions = [], []

    def open(self, wins, pos):
        self.is_open = True

    def set_position(self, pos, sel):
        self.positions.append((pos, sel))

    def animate_remove(self, i):
        self.removed.append(i)

    def close_switcher(self):
        self.is_open = False


def _switcher_ui(n):
    from airmouse.app import _SwitcherUI

    w32, win = _FakeWin32(n), _FakeSwitcherWindow()
    return _SwitcherUI(True, win32=w32, window=win), w32, win


def _join_activate():
    import threading
    for th in threading.enumerate():
        if th.name == "activate":
            th.join(1)


def test_switcher_ui_commit_activates_card_from_event_dx():
    ui, w32, win = _switcher_ui(5)
    ui.handle("switcher_open", 0.0, 0.0)
    assert ui.is_open and win.is_open
    ui.tick(0.0, 1, 0.03)
    ui.handle("switcher_commit", 2.0, 0.06)  # มือเลื่อน +2 การ์ดจากใบที่ 2 → ใบที่ 4
    _join_activate()
    assert w32.activated == [103] and not ui.is_open and not win.is_open


def test_switcher_ui_minimize_removes_target_card_and_cancel_closes():
    ui, w32, win = _switcher_ui(3)
    ui.handle("switcher_open", 0.0, 0.0)
    ui.handle("switcher_minimize", 0.0, 0.1)
    assert w32.minimized == [101] and win.removed == [1] and ui.is_open
    ui.handle("switcher_cancel", 0.0, 0.2)
    assert not ui.is_open and w32.activated == []


def test_switcher_ui_needs_two_windows_and_closes_when_frames_stop():
    ui, w32, win = _switcher_ui(1)
    ui.handle("switcher_open", 0.0, 0.0)
    assert not ui.is_open                  # หน้าต่างเดียว ไม่มีอะไรให้สลับ
    ui, w32, win = _switcher_ui(3)
    ui.handle("switcher_open", 0.0, 0.0)
    ui.tick(0.0, 7, 0.1)
    ui.tick(0.0, 7, 0.5)
    assert ui.is_open
    ui.tick(0.0, 7, 1.2)                   # seq เดิมเกิน 1 วิ (กล้องค้าง) → ปิด
    assert not ui.is_open and not win.is_open
