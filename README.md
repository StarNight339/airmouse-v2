# Air Mouse v2

ควบคุมเมาส์และสลับโปรแกรมด้วยมือผ่านเว็บแคม บน Windows 10/11
ใช้ [MediaPipe Hand Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker) (โมเดลสำเร็จรูป ไม่ต้อง train)

> สถานะ: กำลังวางโครง — ดูแผนเต็มใน [PLAN.md](PLAN.md)

## ท่ามือหลัก

| ท่ามือ | ฟังก์ชัน |
|---|---|
| แบมือค้าง 1 วิ | เปิด/ปิดการควบคุม |
| ขยับมือ | เลื่อน cursor |
| พับนิ้วชี้ | คลิกซ้าย (2 ครั้ง = ดับเบิลคลิก) |
| พับนิ้วกลาง | คลิกขวา |
| จีบโป้ง+ชี้ ค้าง | ลาก |
| ชูนิ้วชี้+กลาง ขยับขึ้นลง | Scroll |
| กำมือ | Clutch (ย้ายมือโดย cursor ไม่ตาม) |
| ชูสามนิ้วค้าง | Swipe Switcher สลับโปรแกรม |

## โครงสร้าง

- `PLAN.md` — แผนและข้อกำหนดทั้งหมด แบ่งเป็น 6 phase
- `prototype/airmouse_prototype.py` — ต้นแบบไฟล์เดียว (เลื่อน cursor, พับนิ้วชี้คลิก, จีบลาก) ใช้อ้างอิง

## ตั้งค่าสภาพแวดล้อมและรัน (Phase 1)

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m airmouse --debug-view   # เปิดหน้าต่างดูภาพกล้อง + landmark (กด Esc หรือ q เพื่อออก)
python -m airmouse --debug        # เขียน log ละเอียด (FPS, dt, เวลา inference) ลง logs/
python -m airmouse --config path\to\config.toml   # ใช้ไฟล์ตั้งค่าอื่น
pytest                            # รันเทสต์ (ไม่ต้องใช้กล้อง)
```

- ถ้า PowerShell ไม่ยอมรัน `Activate.ps1` (execution policy) ให้รัน `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` ครั้งเดียว หรือไม่ต้อง activate แล้วใช้ `.venv\Scripts\python -m airmouse ...` แทน
- ครั้งแรกที่รัน โปรแกรมจะดาวน์โหลดโมเดล Hand Landmarker ไปไว้ที่ `models/` อัตโนมัติ (ต้องต่ออินเทอร์เน็ต)
- เปิดได้หลายแฟลกพร้อมกัน เช่น `python -m airmouse --debug-view --debug`
- กด Ctrl+C ใน terminal เพื่อออกเมื่อไม่ได้เปิด `--debug-view`
- `prototype/airmouse_prototype.py` ยังเก็บไว้เป็นต้นแบบอ้างอิง (`pip install mediapipe opencv-python pyautogui` แล้วรันได้เลย)
