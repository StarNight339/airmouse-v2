# Air Mouse v2

ควบคุมเมาส์และสลับโปรแกรมด้วยมือผ่านเว็บแคม บน Windows 10/11
ใช้ [MediaPipe Hand Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker) (โมเดลสำเร็จรูป ไม่ต้อง train)

แผนและข้อกำหนดทั้งหมดอยู่ใน [PLAN.md](PLAN.md)

## ท่ามือ

### ควบคุมเมาส์

| ท่ามือ | ฟังก์ชัน |
|---|---|
| พลิกมือ (หันอีกด้านเข้ากล้อง) ค้าง 0.7 วิ | เปิด/ปิดการควบคุม |
| ขยับมือ (นิ้วเหยียดสบายๆ) | เลื่อน cursor (เกาะปุ่มให้เองเมื่อขยับช้า) |
| พับนิ้วชี้แล้วเหยียดกลับ | คลิกซ้าย (2 ครั้งเร็วๆ = ดับเบิลคลิก) |
| พับนิ้วกลาง (นิ้วชี้ยังเหยียด) | คลิกขวา |
| จีบโป้ง+ชี้ ค้างแล้วขยับ | ลาก |
| ชูนิ้วชี้+กลาง (นาง/ก้อยพับ) แล้วยกขึ้น/ลง | Scroll แบบจอยสติ๊ก (ยิ่งห่างจุดเริ่มยิ่งเร็ว) |
| กำมือแล้วขยับ | Clutch: ย้ายมือโดย cursor ไม่ตาม |
| มือหายจากกล้อง | 3 วิ: ประหยัดพลังงาน, 10 นาที: ปิดโปรแกรมเอง |

### Swipe Switcher (สลับโปรแกรม)

| ท่ามือ | ฟังก์ชัน |
|---|---|
| ชูนิ้วชี้+กลาง+นาง พับก้อย ค้าง 0.3 วิ | เปิด switcher (เริ่มที่โปรแกรมก่อนหน้า) |
| ค้างท่าแล้วเลื่อนมือซ้าย/ขวา | เลือกการ์ด |
| กางก้อยกลับเป็นมือปกติ หรือกำมือ | สลับไปโปรแกรมที่เลือก |
| ปัดขึ้นเร็วๆ | ย่อหน้าต่างของการ์ดที่เลือก |
| พลิกมือค้าง / ปัดลง / เอามือออก | ยกเลิก |

## ติดตั้งและรัน

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"

.venv\Scripts\python.exe -m airmouse              # รันปกติ (อยู่ใน system tray)
.venv\Scripts\python.exe -m airmouse --calibrate  # calibrate ท่ามือก่อนใช้ครั้งแรก
.venv\Scripts\python.exe -m airmouse --practice   # โหมดฝึก
```

- ครั้งแรกที่รัน โปรแกรมจะดาวน์โหลดโมเดล Hand Landmarker ไปไว้ที่ `models/` (ต้องต่ออินเทอร์เน็ต)
- ไอคอนอยู่ใน system tray: คลิกขวาเพื่อเปิด/ปิดการควบคุม, Calibrate, โหมดฝึก, เปิด/ปิด Snap, เปิด config หรือออก
- ออกด้วยเมนู "ออก" หรือ Ctrl+C ใน terminal
- ถ้าเปิดโปรแกรมซ้อนสองตัว ตัวที่สองจะใช้กล้องไม่ได้ ("หมดเวลารอเฟรมแรก") ให้ปิดตัวเก่าก่อน

| แฟลก | ความหมาย |
|---|---|
| `--calibrate` | เปิดหน้าต่าง calibrate ตอนเริ่ม |
| `--practice` | เปิดโหมดฝึกตอนเริ่ม |
| `--debug` | เขียน log ละเอียด (FPS, dt, เวลา inference) ลง `logs/` |
| `--log-pose` | บันทึก landmark ทุกเฟรมลง `logs/*-pose.jsonl` (ใช้กับ `--debug`) |
| `--debug-view` | เปิดหน้าต่างภาพกล้อง + landmark + ค่าแต่ละนิ้ว |
| `--stay` | ไม่ปิดเองเมื่อไม่เห็นมือนาน (เฉพาะรอบนี้) |
| `--config PATH` | ใช้ไฟล์ตั้งค่าอื่น |

## Calibrate

มือแต่ละคน กล้อง และแสงในห้องต่างกัน เกณฑ์ท่ามือจึงควร calibrate ก่อนใช้ครั้งแรก (โปรแกรมจะเตือนผ่าน tray ถ้ายังไม่เคยทำ)

1. เปิดด้วย `--calibrate` หรือเมนู tray → Calibrate…
2. ทำท่าตามที่หน้าต่างบอกทีละท่า ท่าละ ~3 วิ: มือปกติ, พับชี้, พับกลาง, จีบ, กำมือ, สามนิ้ว, พลิกมือ
3. ดูตารางเทียบค่าเดิมกับค่าใหม่ แล้วกด "บันทึก"

- ผลจะเขียนลง `config.toml` เฉพาะตัวเลข โดยเก็บ comment ไว้ และสำรองไฟล์เดิมเป็น `config.toml.bak`
- มีผลทันทีโดยไม่ต้องเปิดโปรแกรมใหม่ (ต้องพลิกมือเปิดการควบคุมอีกครั้ง)
- ระหว่าง calibrate การควบคุมเมาส์ถูกพักไว้
- ค่าดิบของแต่ละท่าอยู่ใน `logs/calib-*.json`

## โหมดฝึก

หน้าต่างเต็มจอมี 3 ด่าน: คลิกเป้า (รวมดับเบิลคลิกและคลิกขวา), ลากลูกบอลเข้าวง และ scroll หาแถวที่กำหนด
จบแล้วจะแสดงความแม่นยำและเวลาเฉลี่ย และเก็บผลไว้ใน `logs/practice-*.json` ไว้เทียบพัฒนาการ กด Esc เพื่อออก

## ปรับแต่งเพิ่ม (`config.toml`)

ค่าใน `config.toml` มี comment บอกความหมายและที่มาของค่า ที่น่าปรับเอง:

- `[motion] sensitivity`, `deadzone`: ความไวและรัศมีมือนิ่ง
- `[motion] scroll_speed`: ความเร็ว scroll
- `[gesture] switcher_step`: ระยะเลื่อนมือต่อการ์ดใน switcher
- `[snap]`: แรงเกาะปุ่ม (`friction`, `slow_speed`, `escape_speed`) หรือปิดด้วย `enabled = false`
- `[app] auto_exit_minutes`, `sounds`, `overlay`

**จูนจากข้อมูลจริง:** รัน `--debug --log-pose` ใช้งานสักพัก แล้ว replay log กับ config ที่แก้ได้โดยไม่ต้องเปิดกล้อง

```powershell
.venv\Scripts\python.exe tools\replay_pose.py logs\airmouse-....-pose.jsonl --events
```

## ตั้ง Shortcut key

1. คลิกขวาที่ desktop → New → Shortcut
2. ใส่ `"<path>\.venv\Scripts\pythonw.exe" -m airmouse` (หรือ path ของ `airmouse.exe` ถ้าใช้ตัว .exe) แล้วตั้ง "Start in" เป็นโฟลเดอร์โปรเจกต์
3. Properties ของ shortcut → ช่อง Shortcut key กดปุ่มที่ต้องการ เช่น `Ctrl+Alt+M`

## สร้าง .exe

```powershell
.venv\Scripts\python.exe -m pip install -e ".[build]"
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
```

ได้โฟลเดอร์ `dist\airmouse\` ที่มี `airmouse.exe` พร้อม `config.toml` และ `logs\` อยู่ข้างกัน
ทั้งโฟลเดอร์ย้ายไปเครื่องอื่นได้ (ไม่ต้องติดตั้ง Python) ตัว .exe ไม่มีหน้าต่าง terminal ใช้งานผ่าน tray

## เทสต์

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider   # ไม่ต้องใช้กล้อง ไม่เปิดหน้าต่าง
```

## โครงสร้าง

- `src/airmouse/`: โค้ดหลัก (ดูรายละเอียดใน `CLAUDE.md`)
- `tools/`: `replay_pose.py` (replay log), `build_exe.ps1` (สร้าง .exe)
- `prototype/airmouse_prototype.py`: ต้นแบบไฟล์เดียวรุ่นแรก เก็บไว้อ้างอิง
