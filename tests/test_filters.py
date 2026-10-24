import pytest

from airmouse.filters import OneEuroFilter

DT = 1 / 30


def test_constant_input_stays_constant():
    f = OneEuroFilter(1.0, 0.007)
    for _ in range(50):
        assert f(3.5, DT) == pytest.approx(3.5)
    assert f.velocity == pytest.approx(0.0)


def test_step_converges():
    f = OneEuroFilter(1.0, 0.007)
    f(0.0, DT)
    y = 0.0
    for _ in range(300):
        y = f(10.0, DT)
    assert y == pytest.approx(10.0, abs=1e-2)


def test_higher_beta_has_less_lag_on_ramp():
    def lag(beta: float) -> float:
        f = OneEuroFilter(1.0, beta)
        err = 0.0
        for i in range(120):
            x = i * 0.5
            err = x - f(x, DT)
        return err

    assert lag(5.0) < lag(0.0)


def test_reset_clears_state():
    f = OneEuroFilter(1.0, 0.1)
    f(0.0, DT)
    f(100.0, DT)
    f.reset()
    assert f.value is None
    assert f.velocity == 0.0
    assert f(7.0, DT) == 7.0  # ค่าแรกหลัง reset ผ่านตรง ๆ


def test_nonpositive_dt_is_safe():
    f = OneEuroFilter(1.0, 0.007)
    f(1.0, DT)
    assert f(5.0, 0.0) == pytest.approx(1.0)
    assert f(5.0, -1.0) == pytest.approx(1.0)
    assert f.value == pytest.approx(1.0)
