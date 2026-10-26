"""ทดสอบ helper ล้วนของ overlay/sound/console handler (ไม่แสดงหน้าต่าง ไม่เล่นเสียงจริง)"""
from __future__ import annotations

import sys
import wave
from types import SimpleNamespace

import pytest

from airmouse import sound
from airmouse.ui.cursor_overlay import MODE_COLORS, overlay_progress

MODES = ("off", "move", "clutch", "drag", "scroll", "flip")


def test_color_map_covers_modes():
    for m in MODES:
        assert m in MODE_COLORS


def _st(mode, i=0.0, mid=0.0, pin=0.0, tog=0.0):
    return SimpleNamespace(mode=mode, index_progress=i, middle_progress=mid,
                           pinch_progress=pin, toggle_progress=tog)


def test_overlay_progress_cases():
    assert overlay_progress(_st("move", 0.2, 0.7, 0.4)) == pytest.approx(0.7)
    assert overlay_progress(_st("move")) == 0.0
    assert overlay_progress(_st("flip", tog=0.5)) == pytest.approx(0.5)
    assert overlay_progress(_st("off", tog=0.25)) == pytest.approx(0.25)
    assert overlay_progress(_st("drag")) == 1.0
    assert overlay_progress(_st("clutch", 0.9, tog=0.9)) == 0.0
    assert overlay_progress(_st("scroll", 0.9)) == 0.0
    assert overlay_progress(_st("move", 3.0)) == 1.0  # clamp


def test_make_wav_valid(tmp_path):
    p = tmp_path / "sub" / "t.wav"
    sound.make_wav(p, ((1000.0, 1500.0), 800.0), 0.1, 0.25)
    with wave.open(str(p), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 44100
        assert abs(w.getnframes() - 4410) <= 2
        raw = w.readframes(w.getnframes())
    import struct

    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    peak = max(abs(s) for s in samples) / 32767
    assert 0.1 < peak <= 0.25 + 1e-3
    assert samples[0] == 0  # fade-in


def test_sounds_disabled_is_noop(tmp_path):
    s = sound.Sounds(False, tmp_path)
    s.play("click_left")
    assert list(tmp_path.iterdir()) == []


def test_event_mapping():
    f = sound.sound_for_event
    assert f("click_left") == f("click_right") == "click"
    assert f("exit") == "notify"
    assert f("drag_start") == "drag_start"
    assert f("nope") is None
    for name in sound.EVENT_SOUNDS.values():
        assert name in sound.SOUND_SPECS


def test_sounds_generates_files(tmp_path):
    sound.Sounds(True, tmp_path)
    for name in sound.SOUND_SPECS:
        assert (tmp_path / f"{name}.wav").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_console_handler_events(monkeypatch):
    from airmouse.platform.win32 import system

    calls = []
    monkeypatch.setattr(system, "_console_cb", lambda: calls.append(1))
    for code in (system.CTRL_CLOSE_EVENT, system.CTRL_LOGOFF_EVENT, system.CTRL_SHUTDOWN_EVENT):
        assert system._on_console_event(code) is False
    assert len(calls) == 3
    for code in (system.CTRL_C_EVENT, system.CTRL_BREAK_EVENT):
        assert system._on_console_event(code) is False
    assert len(calls) == 3


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_console_handler_swallows_callback_errors(monkeypatch):
    from airmouse.platform.win32 import system

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(system, "_console_cb", boom)
    assert system._on_console_event(system.CTRL_CLOSE_EVENT) is False


def test_tray_and_overlay_importable():
    from airmouse.ui import cursor_overlay, tray  # noqa: F401
