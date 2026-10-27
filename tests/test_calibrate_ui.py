"""ทดสอบเฉพาะ helper ที่ไม่ต้องสร้าง widget"""
from datetime import datetime

from airmouse import calibration
from airmouse.ui import calibrate


def test_dump_path_name():
    p = calibrate.dump_path(datetime(2026, 10, 27, 20, 26, 31))
    assert p.name == "calib-20261027-202631.json"
    assert p.parent.name == "logs"


def test_result_to_jsonable():
    assert calibrate.result_to_jsonable(None) is None
    r = calibration.CalibrationResult({("gesture", "curl_down"): 0.6}, {"fist": "x"}, {})
    out = calibrate.result_to_jsonable(r)
    assert out["values"] == {"gesture.curl_down": 0.6}
    assert out["problems"] == {"fist": "x"}
