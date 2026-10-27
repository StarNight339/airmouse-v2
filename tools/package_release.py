"""แพ็ก dist\\airmouse เป็น zip สำหรับ GitHub Release (รันหลัง tools\\build_exe.ps1)

    .venv\\Scripts\\python.exe tools\\package_release.py

ไม่เอาของส่วนตัวของเครื่องนี้ไปด้วย: config.toml ถูกสร้างใหม่แบบกลางจาก config.toml ของ repo
(มือขวา, เรียนรู้ด้านมือเอง, ยังไม่ calibrate) และไม่มี logs / ไฟล์ .bak
ได้ dist\\AirMouse-v<version>-windows-x64.zip
"""
from __future__ import annotations

import shutil
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airmouse.config import from_dict, update_toml_text  # noqa: E402

NEUTRAL = {("camera", "hand"): "right", ("gesture", "normal_facing"): 0, ("meta", "calibrated"): ""}


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    src = ROOT / "dist" / "airmouse"
    if not (src / "airmouse.exe").exists():
        print("ไม่พบ dist\\airmouse\\airmouse.exe: รัน tools\\build_exe.ps1 ก่อน")
        return 1
    stage = ROOT / "build" / "release" / "AirMouse"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(src, stage, ignore=shutil.ignore_patterns("logs", "config.toml*"))
    (stage / "logs").mkdir()

    raw = (ROOT / "config.toml").read_bytes().decode("utf-8")
    text = update_toml_text(raw, NEUTRAL)
    from_dict(tomllib.loads(text))  # ต้องโหลดได้
    (stage / "config.toml").write_bytes(text.encode("utf-8"))

    out = ROOT / "dist" / f"AirMouse-v{version}-windows-x64.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(stage.rglob("*")):
            z.write(p, Path("AirMouse") / p.relative_to(stage))
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
