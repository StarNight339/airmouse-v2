"""ทดสอบตัวกรอง Alt+Tab (pure) ไม่เรียก Win32 จริง"""
from __future__ import annotations

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)

from airmouse.platform.win32.windows import (  # noqa: E402
    WS_EX_APPWINDOW, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, is_switch_candidate)


def cand(visible=True, owner=0, ex=0, cloaked=0, title="Doc", cls="Chrome_WidgetWin_1"):
    return is_switch_candidate(visible, owner, ex, cloaked, title, cls)


def test_normal_window_ok():
    assert cand()


def test_invisible():
    assert not cand(visible=False)


def test_owned():
    assert not cand(owner=1234)


def test_tool_window_and_appwindow_override():
    assert not cand(ex=WS_EX_TOOLWINDOW)
    assert cand(ex=WS_EX_TOOLWINDOW | WS_EX_APPWINDOW)


def test_noactivate_and_override():
    assert not cand(ex=WS_EX_NOACTIVATE)
    assert cand(ex=WS_EX_NOACTIVATE | WS_EX_APPWINDOW)


def test_cloaked():
    assert not cand(cloaked=2)


def test_empty_title():
    assert not cand(title="")
    assert not cand(title="   ")


@pytest.mark.parametrize("cls", ["Progman", "WorkerW", "Shell_TrayWnd",
                                 "Windows.UI.Core.CoreWindow"])
def test_shell_classes(cls):
    assert not cand(cls=cls)
    assert not cand(cls=cls, ex=WS_EX_APPWINDOW)
