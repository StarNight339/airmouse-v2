"""สร้างไอคอนโปรแกรม assets/airmouse.ico (+ airmouse.png) ด้วย Pillow

    .venv\\Scripts\\python.exe tools\\make_icon.py

วาดที่ 1024 px แล้วย่อลง (anti-alias) เป็นหลายขนาดใน .ico ไฟล์เดียว
ลาย: พื้นสี่เหลี่ยมมุมมนไล่สีน้ำเงิน→ม่วง, ลูกศร cursor สีขาว, คลื่นการเคลื่อนไหวของมือ
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
S = 1024
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
TOP = (38, 120, 255)      # น้ำเงิน
BOTTOM = (124, 58, 237)   # ม่วง


def _gradient() -> Image.Image:
    g = Image.new("RGB", (S, S))
    px = g.load()
    for y in range(S):
        for x in range(S):
            t = min(1.0, max(0.0, (0.35 * x + 0.65 * y) / S))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM))
    return g


def _cursor(scale: float, ox: float, oy: float) -> list[tuple[float, float]]:
    # ลูกศรเมาส์มาตรฐาน (หน่วย 0..1) ปลายแหลมอยู่ซ้ายบน
    pts = [(0, 0), (0, 0.78), (0.21, 0.6), (0.35, 0.92), (0.5, 0.85), (0.36, 0.54), (0.62, 0.54)]
    return [(ox + x * scale, oy + y * scale) for x, y in pts]


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=255)
    img.paste(_gradient(), (0, 0), mask)

    d = ImageDraw.Draw(img)
    # คลื่นการเคลื่อนไหว 3 เส้น (มือโบก) มุมขวาบน
    cx, cy = 600, 430
    for i, r in enumerate((150, 240, 330)):
        alpha = 235 - i * 65
        d.arc((cx - r, cy - r, cx + r, cy + r), start=-78, end=-12,
              fill=(255, 255, 255, alpha), width=44)
    # เงาของ cursor แล้วตัว cursor
    pts = _cursor(560, 200, 200)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x + 18, y + 24) for x, y in pts], fill=(20, 10, 60, 120))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
    d.polygon(pts, fill=(255, 255, 255, 255))
    d.line(pts + [pts[0]], fill=(30, 30, 60, 255), width=22, joint="curve")
    return img


def main() -> None:
    big = render()
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    big.resize((256, 256), Image.LANCZOS).save(out / "airmouse.png")
    big.save(out / "airmouse.ico", sizes=[(s, s) for s in SIZES])
    print(f"wrote {out / 'airmouse.ico'} ({len(SIZES)} sizes) and airmouse.png")


if __name__ == "__main__":
    main()
