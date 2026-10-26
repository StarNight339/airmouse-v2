"""python -m airmouse [--debug-view] [--debug] [--stay] [--log-pose] [--config PATH]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH


def main(argv: list[str] | None = None) -> int:
    # console บน Windows อาจเป็น cp1252 ซึ่งพิมพ์ภาษาไทยไม่ได้
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="airmouse", description="ควบคุมเมาส์ด้วยมือผ่านเว็บแคม")
    parser.add_argument("--debug-view", action="store_true",
                        help="เปิดหน้าต่างภาพกล้องพร้อม landmark, ค่า curl/pinch และ FPS")
    parser.add_argument("--debug", action="store_true",
                        help="บันทึก FPS, dt และเวลา inference ลง logs/")
    parser.add_argument("--stay", action="store_true",
                        help="ไม่ปิดโปรแกรมเองเมื่อไม่เห็นมือนาน (เฉพาะรอบนี้)")
    parser.add_argument("--log-pose", action="store_true",
                        help="บันทึกค่ามือ (curl, pinch, facing, mode) ทุกเฟรมลง log (ใช้กับ --debug)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="ไฟล์ config (ค่าเริ่มต้น: config.toml ที่ root โปรเจกต์)")
    args = parser.parse_args(argv)

    from .app import run  # import ช้าๆ (mediapipe, Qt) หลัง parse args เพื่อให้ --help เร็ว
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
