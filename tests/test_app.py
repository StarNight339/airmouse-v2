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

    def update(self, feats, ts):
        return self.states.pop(0)


class FakeMotion:
    speed = gain = 0.0

    def __init__(self):
        self.resets = []
        self.updates = []

    def reset(self, xy):
        self.resets.append(xy)

    def update(self, anchor, hand_size, aspect, dt):
        self.updates.append((anchor, dt))
        return (100.0 + len(self.updates), 50.0)


class FakeMouse:
    def __init__(self):
        self.user_moved = False
        self.targets = []
        self.active = []
        self.resyncs = 0

    def resync(self):
        self.resyncs += 1
        self.user_moved = False
        return (10.0, 20.0)

    def set_target(self, x, y, frame_interval):
        self.targets.append((x, y, frame_interval))

    def set_active(self, active):
        self.active.append(active)


def _feats():
    return HandFeatures(curl={}, pinch=0.1, hand_size=0.2, anchor=(0.5, 0.5))


ON = GestureState(True, False, 0.0, False, False)
OFF = GestureState(False, False, 0.0, False, False)
CLUTCH = GestureState(True, True, 0.0, False, True)


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


def test_controller_user_mouse_move_resyncs():
    c, m, mouse = _controller([ON, ON])
    c.step(_feats(), 1.0, 4 / 3)
    mouse.user_moved = True
    c.step(_feats(), 1.033, 4 / 3)
    assert len(m.resets) == 2 and not mouse.user_moved
