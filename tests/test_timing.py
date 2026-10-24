import pytest

from airmouse.timing import RateMeter


def test_initial_rate_zero():
    assert RateMeter().rate == 0.0


def test_single_tick_rate_zero():
    m = RateMeter()
    m.tick(1.0)
    assert m.rate == 0.0


def test_steady_rate():
    m = RateMeter()
    for i in range(31):
        m.tick(i / 30)
    assert m.rate == pytest.approx(30.0)


def test_old_samples_dropped_and_rate_adapts():
    m = RateMeter(window_s=1.0)
    for i in range(10):
        m.tick(i * 0.1)  # 10 Hz
    for i in range(1, 21):
        m.tick(0.9 + i * 0.05)  # 20 Hz ครบหน้าต่าง 1 วิ
    assert m.rate == pytest.approx(20.0)
    assert len(m._times) <= 22


def test_same_timestamp_does_not_divide_by_zero():
    m = RateMeter()
    m.tick(5.0)
    m.tick(5.0)
    assert m.rate == 0.0


def test_default_timestamp_uses_clock(monkeypatch):
    clock = iter([10.0, 10.5])
    monkeypatch.setattr("airmouse.timing.time.monotonic", lambda: next(clock))
    m = RateMeter()
    m.tick()
    m.tick()
    assert m.rate == 2.0
