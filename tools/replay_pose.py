"""replay ไฟล์ logs/*-pose.jsonl (จาก --log-pose) ผ่าน features + GestureTracker ปัจจุบัน

ใช้จูน threshold: แก้ config.toml แล้ว replay ดูว่า event เปลี่ยนไปอย่างไร โดยไม่ต้องเปิดกล้อง

    python tools/replay_pose.py logs/airmouse-...-pose.jsonl            # สรุป event
    python tools/replay_pose.py logs/...-pose.jsonl --events            # ทุก event พร้อมค่ามือ
    python tools/replay_pose.py logs/...-pose.jsonl --config other.toml
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from airmouse import config as config_mod
from airmouse import features
from airmouse.gestures import GestureTracker


def replay(path: Path, cfg: config_mod.Config, show_events: bool) -> tuple[Counter, Counter]:
    tracker = GestureTracker(cfg.gesture)
    old, new = Counter(), Counter()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            old.update(rec.get("ev", []))
            feats = None
            if rec["hand"] is not None:
                feats = features.compute(rec["lm"], rec["world"], rec["aspect"],
                                         rec["hand"], rec["mirror"])
            st = tracker.update(feats, rec["t"])
            new.update(st.events)
            if show_events and st.events:
                pose = ""
                if feats is not None:
                    c = feats.curl
                    pose = (f"ind={c['index']:.2f} mid={c['middle']:.2f} rin={c['ring']:.2f} "
                            f"pin={c['pinky']:.2f} pinch={feats.pinch * 100:.1f}cm "
                            f"ay={feats.anchor[1]:.3f}")
                print(f"t={rec['t']:.3f} {st.mode:6s} {','.join(st.events):24s} {pose}")
    return old, new


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pose_log", type=Path)
    parser.add_argument("--config", type=Path, default=config_mod.DEFAULT_CONFIG_PATH)
    parser.add_argument("--events", action="store_true", help="แสดงทุก event พร้อมค่ามือ")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    old, new = replay(args.pose_log, config_mod.load(args.config), args.events)
    print("ตอนบันทึก:", dict(old))
    print("replay  :", dict(new))
    return 0


if __name__ == "__main__":
    sys.exit(main())
