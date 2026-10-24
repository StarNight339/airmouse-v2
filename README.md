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

## ลองรันต้นแบบ

```bash
pip install mediapipe opencv-python pyautogui
python prototype/airmouse_prototype.py
```
