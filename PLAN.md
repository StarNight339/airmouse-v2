# Air Mouse — Project Plan

ควบคุมเมาส์และสลับโปรแกรมด้วยมือผ่านเว็บแคม โดยใช้ MediaPipe Hand Landmarker (โมเดลสำเร็จรูป ไม่ต้อง train)
**เป้าหมายหลัก: Windows 10/11**

ให้ทำทีละ Phase ตามหัวข้อ 9 ทดสอบด้วยมือจริงจนผ่านเงื่อนไข "เสร็จเมื่อ" ก่อนไป Phase ถัดไป

---

## 1. ตารางท่ามือ

### โหมดปกติ (ควบคุมเมาส์)

| ท่ามือ | ฟังก์ชัน |
|---|---|
| แบมือ (5 นิ้ว) หันหากล้องค้าง 1 วิ | เปิด/ปิดการควบคุม (toggle) |
| ขยับมือ (นิ้วเหยียดสบายๆ) | เลื่อน cursor |
| พับนิ้วชี้ลงแล้วเหยียดกลับ | คลิกซ้าย |
| พับนิ้วชี้ 2 ครั้งเร็วๆ | ดับเบิลคลิก |
| พับนิ้วกลางลง (นิ้วชี้ยังเหยียด) แล้วเหยียดกลับ | คลิกขวา |
| จีบโป้ง+ชี้ ค้างแล้วขยับ | ลาก (mouse down ค้าง) |
| ชูนิ้วชี้+กลาง (นางพับ) แล้วขยับขึ้นลง | Scroll |
| กำมือแล้วขยับ | Clutch: ย้ายมือโดย cursor ไม่ตาม |
| ชูนิ้วชี้+กลาง+นาง ค้าง 300 ms | เปิด Swipe Switcher |
| ชูโป้ง+ก้อย พับชี้/กลาง/นาง (ท่า 🤙) ค้าง 300 ms | เปิดโหมดจัดหน้าต่าง (หัวข้อ 11) |
| มือหายจากกล้อง | พัก → ประหยัดพลังงาน → ปิดโปรแกรมเมื่อครบ 10 นาที |

### โหมด Swipe Switcher (ท่ามือมีความหมายเฉพาะตอนเปิด switcher)

| ท่ามือ | ฟังก์ชัน |
|---|---|
| สามนิ้วค้าง + ขยับซ้าย/ขวา | เลื่อนการ์ดโปรแกรม |
| ปัดขึ้นเร็วๆ | ย่อหน้าต่างของการ์ดที่เลือก (minimize) |
| ออกจากท่าสามนิ้วชัดเจน (กางก้อยกลับเป็นมือปกติ หรือกำมือ) | สลับไปโปรแกรมที่เลือกแล้วปิด switcher (แบบปล่อย Alt) |
| พลิกมือค้าง 0.25 วิ / ปัดลง / มือหาย 0.3 วิ | ยกเลิก กลับไปหน้าต่างเดิม |

> หมายเหตุ (2026-10-27): เดิมใช้ "แบมือ = ยกเลิก" แต่ toggle เปลี่ยนเป็นท่าพลิกมือแล้ว และมือปกติคือแบมือ
> จึงใช้พลิกมือเป็นท่ายกเลิกแทน เกณฑ์ทั้งหมดของท่าสามนิ้วมาจาก log `20261027-202631`

### โหมดจัดหน้าต่าง (ท่ามือมีความหมายเฉพาะตอนเปิดโหมดนี้)

| ท่ามือ | ฟังก์ชัน | คีย์ที่ส่ง |
|---|---|---|
| ค้างท่า 🤙 แล้วปัดขึ้น | ขยายเต็มจอ (maximize) | `Win+Up` |
| ค้างท่า 🤙 แล้วปัดลง | คืนขนาด ถ้าคืนขนาดอยู่แล้ว = ย่อ | `Win+Down` |
| ค้างท่า 🤙 แล้วปัดซ้าย/ขวา | Snap ครึ่งจอ | `Win+Left` / `Win+Right` |
| ออกจากท่า 🤙 โดยไม่ได้ปัด / มือหาย | ยกเลิก ไม่ทำอะไร | - |

---

## 2. หลักการตรวจจับท่ามือ (ต้องทำตาม)

1. **วัดทุกท่าด้วย `hand_world_landmarks` (3D หน่วยเมตร)** เพื่อไม่ให้ค่าเปลี่ยนตามมุมกล้องหรือระยะห่างของมือ
   - curl ของนิ้ว: `dist(mcp, tip) / (dist(mcp,pip)+dist(pip,dip)+dist(dip,tip))` นิ้วตรงได้ประมาณ 1.0 ยิ่งพับยิ่งน้อย (คิดแยกทุกนิ้ว)
   - pinch: `dist(4, 8)` หน่วยเมตร
   - กำมือ: นิ้วชี้ กลาง นาง ก้อย พับทั้งหมด
2. **cursor อิงค่าเฉลี่ยข้อนิ้วโคน (landmark 5, 9, 13, 17)** ไม่ใช่ปลายนิ้ว เวลาพับนิ้วเพื่อคลิก cursor จะได้ไม่เลื่อน
3. **Hysteresis ทุกท่า** threshold กดกับปล่อยแยกกัน และต้องค้างอย่างน้อย `confirm_frames` เฟรมถึงจะนับ
4. **ลำดับความสำคัญเมื่อท่าชนกัน:** switcher > จัดหน้าต่าง (🤙) > drag (pinch) > clutch (กำมือ) > scroll > คลิก
   - คลิกได้เฉพาะตอน `pinch > pinch_up`
   - หลังคลิกหรือหลังปล่อยจีบ ต้องเหยียดนิ้วกลับจน `curl > curl_up` ก่อน (click armed)
   - คลิกขวาต้องให้นิ้วชี้เหยียดอยู่ เพื่อแยกออกจากท่ากำมือ
5. **ข้ามเฟรมที่โมเดลไม่มั่นใจ** (ค่า handedness/presence score ต่ำกว่าเกณฑ์) ไม่นำมาตีความเป็นท่า
6. **เลือกมือได้** (ซ้าย/ขวาใน config) และติดตามแค่มือเดียว ถ้ามีมือที่สองเข้ามาในเฟรมให้ไม่สนใจ

---

## 3. ความรู้สึกในการขยับ cursor

ลำดับการประมวลผล: `ตำแหน่งข้อนิ้ว → ชดเชยขนาดมือ → deadzone → กราฟความไว → One Euro filter → map ลงจอ`

- **โหมด relative (ค่าเริ่มต้น):** cursor ขยับตาม "การเปลี่ยนแปลง" ของมือแบบ trackpad ใช้ร่วมกับ clutch ได้ ส่วนโหมด absolute ให้เลือกได้ใน config
- **กรอบควบคุมตามจุดวางมือ:** ตอน toggle เปิด ให้ตั้งกึ่งกลางกรอบไว้ที่ตำแหน่งมือขณะนั้น (สำหรับโหมด absolute)
- **ขอบจอเข้าถึงง่าย:** map ให้เกินขอบจอเล็กน้อย (`edge_overshoot`) แล้วค่อย clamp
- **ชดเชยขนาดมือ:** หารการเคลื่อนที่ด้วยขนาดมือในภาพ (เช่น ระยะ landmark 0→9) ไม่ว่ามือจะใกล้หรือไกลกล้อง ความไวก็เท่ากัน
- **Soft deadzone:** ถ้าการเคลื่อนที่น้อยกว่า `deadzone` cursor จะนิ่ง พอเกินแล้วให้ความเร็วค่อยๆ เพิ่มจาก 0 (ห้ามกระโดด) เช่น `out = max(0, |v| - dz)` แล้วผสมแบบ smoothstep ช่วงต้น
- **กราฟความไว (precision):** ขยับช้าได้ gain ต่ำ ขยับเร็วได้ gain สูง ใช้ความเร็วที่ผ่าน filter แล้วเท่านั้น (ห้ามใช้ dx/dt ดิบ)
- **ย้อนตำแหน่งตอนคลิก:** เก็บประวัติตำแหน่ง cursor ~200 ms เมื่อระบบยืนยันว่าคลิก ให้คลิกที่ตำแหน่งก่อนเริ่มพับนิ้ว (`click_rollback_ms`) ใช้แทนการ freeze
- **ตอนเริ่มจีบเพื่อลาก:** เพิ่ม smoothing ชั่วคราว (~150 ms) กันไม่ให้ของที่ลากเด้งตอนเริ่ม
- **เก็บเศษ sub-pixel:** สะสมเศษทศนิยมของการเคลื่อนที่ไว้ใช้ในเฟรมถัดไป ห้ามปัดทิ้งด้วย `int()`

---

## 4. ความลื่นและการทำงานเบื้องหลัง (กันอาการกระตุก)

1. **แยก thread**
   - Qt main thread: UI ทั้งหมด (overlay, switcher, tray, หน้าต่าง debug)
   - Camera thread: อ่านกล้อง เก็บไว้แค่เฟรมล่าสุดพร้อม timestamp ตอนรับภาพ
   - Inference thread: รัน MediaPipe → gestures → อัปเดต "ตำแหน่งเป้าหมาย" ของ cursor (~30 Hz)
   - Mouse thread: เลื่อน cursor เข้าหาเป้าหมายแบบ interpolate ที่ 120–240 Hz
2. **dt มาจาก timestamp ของเฟรมกล้อง** และ clamp ไว้ 10–50 ms ถ้าเฟรมมาช้าผิดปกติ ไม่นำไปคำนวณความเร็ว
3. **Windows-specific (ทำผ่าน ctypes ตอนเริ่มโปรแกรม)**
   - `SetProcessDpiAwareness(2)` (Per-monitor DPI aware)
   - `timeBeginPeriod(1)` และเรียก `timeEndPeriod(1)` ตอนปิด
   - ปิด power throttling ด้วย `SetProcessInformation(ProcessPowerThrottling)` และตั้ง priority เป็น ABOVE_NORMAL
   - ขยับเมาส์ด้วย `SendInput` + `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK` (ห้ามใช้แบบ relative เพราะจะโดน mouse acceleration ของ Windows ทับ) และรองรับหลายจอ
4. ส่วน debug ให้ log FPS, dt และเวลา inference ลงไฟล์ได้ (`--debug`)

---

## 5. วงจรชีวิตโปรแกรม (idle / auto-exit)

| สถานะ | เงื่อนไข | การทำงาน |
|---|---|---|
| Active | เห็นมือ + toggle เปิด | ทำงานเต็ม FPS |
| Paused | มือหาย | ปล่อยเมาส์ (ถ้าลากอยู่), ปิด switcher, หยุดขยับ cursor, รีเซ็ต filter |
| Low-power | มือหาย > 3 วิ | ลดเหลือ 5–10 FPS และลดความละเอียด ยังตรวจหามืออยู่ |
| Exit | มือหายครบ `auto_exit_minutes` (ค่าเริ่มต้น 10) | ปิดกล้อง แจ้งเตือน "Air Mouse ปิดแล้ว" ผ่าน tray แล้วออกจากโปรแกรม |

- มือกลับมาจาก Low-power ต้องทำท่า toggle ก่อนถึงจะควบคุมได้
- `auto_exit_minutes = 0` คือไม่ปิดเอง และ flag `--stay` ใช้ปิด auto-exit เฉพาะรอบนั้น
- **ต้องปล่อยเมาส์และปล่อยคีย์ที่กดค้างเสมอ** ทั้งตอนปิดโปรแกรม ตอน crash (try/finally + atexit) และตอนมือหาย
- เป้าหมาย: ตั้งแต่เปิดโปรแกรมจนใช้งานได้ไม่เกิน 3 วินาที
- README ต้องมีวิธีสร้าง shortcut และตั้ง Shortcut key (เช่น Ctrl+Alt+M) ใน Properties ของไฟล์ .lnk

---

## 6. Swipe Switcher (UI สลับโปรแกรมแบบทำเอง)

### หน้าตาและการโต้ตอบ
- แถบการ์ดแนวนอนลอยกลางจอ พื้นหลังโปร่งแสงแบบ blur/acrylic และหน้าต่างเป็น always-on-top, frameless และไม่แย่ง focus
- การ์ดแต่ละใบมี **preview สดของหน้าต่าง** ไอคอน และชื่อโปรแกรม การ์ดที่เลือกอยู่ตรงกลาง ขยายใหญ่กว่าใบอื่น
- เลื่อนแบบ carousel ตามมือ (relative จากตำแหน่งตอนเปิด) มีแรงเฉื่อยเล็กน้อย และ snap เข้าการ์ดที่ใกล้ที่สุดเมื่อมือหยุด
- ปัดขึ้น: การ์ดเลื่อนขึ้นแล้วหายไปพร้อม animation และหน้าต่างนั้นถูกย่อ (minimize)
- เรียงการ์ดตามลำดับการใช้งานล่าสุดแบบเดียวกับ Alt+Tab และการ์ดที่เลือกตอนเปิดคือโปรแกรมก่อนหน้า (ใบที่ 2) เพื่อให้สลับไปมาระหว่างสองโปรแกรมได้เร็ว
- ตอนเปิด switcher ให้ซ่อน cursor overlay และหยุดการควบคุมเมาส์

### การทำบน Windows
- รายการหน้าต่าง: `EnumWindows` แล้วกรองให้เหมือน Alt+Tab คือ visible, ไม่ใช่ tool window, ไม่มี owner, ไม่ cloaked (`DwmGetWindowAttribute(DWMWA_CLOAKED)`) และมี title
- ลำดับ z-order จาก `EnumWindows` ใช้เป็นลำดับใช้งานล่าสุด
- Preview สด: `DwmRegisterThumbnail` + `DwmUpdateThumbnailProperties` วาดลงตำแหน่งการ์ดใน HWND ของ overlay
- ไอคอน: `WM_GETICON` / `GetClassLongPtr(GCLP_HICON)` หรือดึงจากไฟล์ .exe เป็นทางสำรอง
- สลับหน้าต่าง: `SetForegroundWindow` ซึ่งมักโดน Windows บล็อก ต้องใช้เทคนิคส่ง `Alt` down/up ด้วย `SendInput` ก่อนเรียก (หรือ `AttachThreadInput`) และถ้าหน้าต่างถูกย่ออยู่ ให้ `ShowWindow(SW_RESTORE)` ก่อน
- ย่อ: `ShowWindow(SW_MINIMIZE)`
- รีเฟรชรายการทุกครั้งที่เปิด switcher (ไม่ต้อง cache)

---

## 7. Feedback และความสะดวก

- **Cursor overlay:** วงเล็กๆ ตาม cursor (always-on-top, click-through)
  - สีบอกสถานะ: ขยับ / clutch / ลาก / scroll / ปิดอยู่
  - วงความคืบหน้าค่อยๆ เต็มตามระดับการพับนิ้วหรือการจีบ และตามเวลาที่ค้างท่า toggle
- **เสียงเบาๆ** ตอนคลิก เริ่ม/ปล่อยลาก เปิด/ปิด และตอนสลับโปรแกรม (ปิดได้ใน config)
- **System tray:** เมนูเปิด/ปิด, Calibrate, โหมดฝึก, เปิด config และออก
- **Calibration** (`--calibrate` หรือเมนู tray): ให้ทำท่า เหยียด / พับชี้ / พับกลาง / จีบ / กำ / สามนิ้ว ท่าละ 2–3 วิ แล้วคำนวณ threshold เขียนลง config
- **โหมดฝึก:** หน้าต่างมีเป้าวงกลมให้คลิก ลาก และ scroll ตาม พร้อมแสดงความแม่นยำ
- **หน้าต่าง debug** (`--debug-view`): ภาพกล้องพร้อม landmark ค่า curl/pinch state และ FPS

---

## 8. Tech stack และโครงสร้าง

- Python 3.14 (เครื่องนี้เคยติดตั้ง `mediapipe` 0.10.35 บน 3.14 ได้แล้ว)
- `mediapipe` ใช้ **Tasks API** (`HandLandmarker`, `RunningMode.VIDEO`) ห้ามใช้ `mp.solutions.hands`
  - โมเดลดาวน์โหลดอัตโนมัติไปที่ `models/`: `https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task`
- `opencv-python` ใช้อ่านกล้องอย่างเดียว (การแสดงผลทั้งหมดอยู่ใน Qt)
- `PySide6` สำหรับ overlay, switcher, tray, เสียง (`QSoundEffect`) และหน้าต่าง debug
- `ctypes` สำหรับ Win32 API ทั้งหมด (ไม่ใช้ pyautogui)
- `tomllib` อ่าน config และใช้ `tomli-w` เขียน config ตอน calibrate
- `pytest`

```
airmouse/
├─ README.md
├─ PLAN.md
├─ pyproject.toml
├─ config.toml
├─ .gitignore                # models/, .venv/, __pycache__/, logs/
├─ assets/sounds/
├─ models/
├─ src/airmouse/
│  ├─ __main__.py            # CLI: --stay --debug --debug-view --calibrate --practice
│  ├─ app.py                 # สร้าง threads, Qt app, สถานะโปรแกรม (Active/Paused/Low-power/Exit)
│  ├─ config.py              # dataclass + โหลด/บันทึก config.toml
│  ├─ camera.py              # thread อ่านกล้อง
│  ├─ tracker.py             # HandLandmarker wrapper + ดาวน์โหลดโมเดล + เลือกมือ
│  ├─ features.py            # คำนวณ curl ทุกนิ้ว, pinch, ขนาดมือ, จุดอ้างอิง
│  ├─ gestures.py            # state machine → events (pure logic ไม่แตะ OS)
│  ├─ motion.py              # deadzone, กราฟความไว, filter, rollback buffer, sub-pixel
│  ├─ filters.py             # OneEuroFilter
│  ├─ platform/win32/
│  │  ├─ system.py           # DPI, timer, throttling, priority
│  │  ├─ mouse.py            # SendInput: move/down/up/click/scroll + mouse thread
│  │  ├─ keyboard.py         # SendInput keys (ใช้กับ foreground trick)
│  │  └─ windows.py          # Enum, filter, icon, thumbnail, activate, minimize
│  ├─ ui/
│  │  ├─ cursor_overlay.py
│  │  ├─ switcher.py
│  │  ├─ tray.py
│  │  ├─ debug_view.py
│  │  ├─ calibrate.py
│  │  └─ practice.py
│  └─ sound.py
└─ tests/
   ├─ test_filters.py
   ├─ test_motion.py         # deadzone ไม่กระโดด, sub-pixel ไม่หาย, rollback
   └─ test_gestures.py       # landmark จำลอง → events ถูกต้อง, ท่าไม่ชนกัน
```

แนวทาง: `gestures.py` และ `motion.py` ต้องไม่ import อะไรที่ผูกกับ OS เพื่อให้เทสต์ได้โดยไม่ต้องใช้กล้อง

### config.toml (ค่าเริ่มต้น)

```toml
[camera]
index = 0
width = 640
height = 480
mirror = true
hand = "right"            # "right" | "left"

[motion]
mode = "relative"         # "relative" | "absolute"
sensitivity = 1.0
deadzone = 0.004          # หน่วย: สัดส่วนขนาดมือ
accel_low_gain = 0.4
accel_high_gain = 2.5
edge_overshoot = 0.05
click_rollback_ms = 150
min_cutoff = 1.0
beta = 0.007

[gesture]
curl_down = 0.60
curl_up = 0.80
pinch_down = 0.035        # meters
pinch_up = 0.055
confirm_frames = 2
toggle_hold_ms = 1000
switcher_hold_ms = 300
switcher_step = 0.6       # ระยะเลื่อนต่อการ์ด (หน่วยขนาดมือ)
min_presence = 0.6

[app]
auto_exit_minutes = 10
low_power_after_s = 3
sounds = true
```

---

## 9. Phases

### Phase 1 — Scaffold + tracking
- โครงโปรเจกต์, venv, `config.py`, `camera.py`, `tracker.py`, `features.py`
- Qt app + `debug_view.py` แสดง landmark, ค่า curl ทุกนิ้ว, pinch และ FPS
- **เสร็จเมื่อ:** `python -m airmouse --debug-view` เห็นจุดบนมือ FPS ≥ 25 และปิดโปรแกรมได้เรียบร้อย

### Phase 2 — Cursor ที่ลื่น
- `platform/win32/system.py` + `mouse.py` (SendInput absolute + mouse thread 120–240 Hz)
- `motion.py` + `filters.py`: relative mode, ชดเชยขนาดมือ, soft deadzone, กราฟความไว, sub-pixel
- toggle (แบมือค้าง) + clutch (กำมือ)
- `test_motion.py`
- **เสร็จเมื่อ:** cursor ไม่สั่นตอนมือนิ่ง ตามทันตอนขยับเร็ว และ **ไม่กระตุกตอนโปรแกรมอยู่เบื้องหลัง** (ให้เช็กด้วย log dt)

### Phase 3 — คลิก, ลาก, scroll
- คลิกซ้าย (พร้อม rollback), ดับเบิลคลิก, คลิกขวา, ลากด้วยการจีบ (พร้อม smoothing ตอนเริ่ม) และ scroll
- กฎลำดับความสำคัญของท่าตามหัวข้อ 2
- `test_gestures.py` ครอบคลุมกรณีท่าชนกัน
- **เสร็จเมื่อ:** คลิกไอคอนเล็กได้ตรง ลากหน้าต่างได้ scroll หน้าเว็บได้ และไม่มีคลิกหลอกตอนจีบหรือกำมือ

### Phase 4 — วงจรชีวิตโปรแกรม + feedback
- Paused / Low-power / Auto-exit, `--stay`, การปล่อยเมาส์ทุกกรณี
- `cursor_overlay.py`, `sound.py`, `tray.py` (พร้อมแจ้งเตือนตอนปิดเอง)
- **เสร็จเมื่อ:** ไม่เห็นมือ 10 นาทีแล้วโปรแกรมปิดเองพร้อมแจ้งเตือน, CPU ช่วง low-power ต่ำ และ overlay บอกสถานะถูกต้อง

### Phase 5 — Swipe Switcher
- `platform/win32/windows.py` (enum, filter, icon, thumbnail, activate, minimize)
- `ui/switcher.py` (carousel, preview สด, animation, ปัดขึ้นเพื่อย่อ)
- ท่าสามนิ้วและท่าในโหมด switcher ตามหัวข้อ 1
- **เสร็จเมื่อ:** สลับไปมาระหว่าง 2 โปรแกรมได้ในการขยับไม่กี่ครั้ง, เลือกโปรแกรมที่ 5 ขึ้นไปได้แม่น, ยกเลิกแล้วกลับหน้าต่างเดิม และหน้าต่างที่ถูกย่อก็ดึงกลับมาได้

### Phase 6 — Calibration + โหมดฝึก + Polish
- `ui/calibrate.py`, `ui/practice.py`
- README: ติดตั้ง, ตารางท่ามือ, การจูน และการตั้ง shortcut key
- (optional) แพ็กเป็น .exe ด้วย PyInstaller
- **เสร็จเมื่อ:** คนใหม่ calibrate แล้วใช้งานได้ภายใน 5 นาทีโดยไม่ต้องแก้ config เอง

### Phase 7 — โหมดจัดหน้าต่าง
- ตามหัวข้อ 11: ท่า 🤙, mode ใหม่ใน `gestures.py`, ส่ง `Win+ลูกศร`, overlay ลูกศร 4 ทิศ, `[window]` ใน config, ท่าใหม่ใน calibrate
- **เสร็จเมื่อ:** ขยาย / คืนขนาด / ย่อ / snap ซ้าย-ขวา ได้ทุกทิศในการปัดครั้งเดียว, ไม่เปิดโหมดเองโดยบังเอิญเมื่อ replay log เก่าทั้งหมด และไม่มีปุ่ม Win ค้าง (Start menu ไม่เด้ง)

---

## 10. ไว้ทำทีหลัง
- จีบแล้วหมุนข้อมือเพื่อปรับเสียง / ควบคุม media
- Dwell click (ค้าง cursor นิ่งแล้วคลิกเอง)
- ท่าทางกำหนดเองด้วย classifier เล็กๆ (input เป็น landmark 63 ค่า) แล้วผูกกับคีย์ลัด
- ปัดสี่นิ้วเพื่อสลับ virtual desktop

---

## 11. โหมดจัดหน้าต่าง (Window Mode)

> เพิ่มเมื่อ 2026-10-28: ตอนนี้ย่อหน้าต่างได้ทางเดียวคือผ่าน Switcher และยังขยาย / คืนขนาด / snap ไม่ได้
> การจัดหน้าต่างที่ใช้อยู่เกิดบ่อยกว่าการจัดหน้าต่างอื่น จึงทำเป็นโหมดแยกที่ทำกับหน้าต่าง active ได้ทันที
> แทนการยัดลง Switcher (ซึ่งปัดลงถูกใช้เป็นยกเลิกอยู่แล้ว)

### ท่าเปิดโหมด: 🤙 (ชูโป้ง+ก้อย พับชี้ กลาง นาง)
- **ทำไมเลือกท่านี้:** ทุกท่าที่มีอยู่ใช้นิ้วชี้เป็นหลัก แต่ท่านี้นิ้วชี้พับ จึงไม่ชนท่าเดิม และโป้งกับก้อยที่ชูอยู่ห่างกันมาก MediaPipe จับได้ชัด
- **เงื่อนไข (วัดด้วย world landmarks ตามหัวข้อ 2):**
  - ก้อยเหยียด: `curl_pinky > curl_up`
  - ชี้ กลาง นาง พับ: `max(curl_index, curl_middle, curl_ring) < curl_down`
  - ใช้ gap แบบเดียวกับท่าสามนิ้ว: `curl_pinky − max(i, m, r) > window_gap` เพื่อให้ทนกับ curl ที่แกว่ง
  - โป้งกางออก: ระยะปลายโป้ง (4) ถึงข้อโคนนิ้วชี้ (5) มากกว่า `window_thumb_out` ใช้แยกจากท่ากำมือที่บังเอิญก้อยหลุดออกมา
- ค้าง `window_hold_ms` (เริ่ม 300 ms เท่า switcher) ถึงจะเปิดโหมด
- **ระวังทางผ่าน:** กำมือ (clutch) → แบมือ อาจผ่านท่าคล้าย 🤙 สั้นๆ เวลาค้างจึงต้องกันได้ ให้ replay log เก่าทั้งหมดแล้วต้องไม่เปิดเองเลย
- เกณฑ์ทุกตัวต้องได้จาก log มือจริง (`--debug --log-pose`) ห้ามเดา และค่าในตารางด้านล่างเป็นแค่จุดเริ่ม

### การทำงานในโหมด
1. ตอนเปิดโหมด:
   - จำ HWND ของหน้าต่าง foreground ณ ตอนนั้นไว้เป็นเป้าหมาย (ห้ามให้ overlay แย่ง focus)
   - จำตำแหน่งมือ (anchor แบบเดียวกับ cursor) เป็นจุดศูนย์กลาง
   - หยุดการควบคุมเมาส์ และ overlay เปลี่ยนเป็นลูกศร 4 ทิศรอบ cursor
2. ระหว่างค้างท่า: คำนวณการเคลื่อนที่จากจุดศูนย์กลาง (หน่วยขนาดมือ)
   - ทิศที่เลือก = แกนที่ขยับมากกว่า (`|dx|` เทียบ `|dy|`) และ overlay ไฮไลต์ทิศนั้นตามระยะ
   - เมื่อระยะเกิน `window_swipe` ให้ยิงคำสั่งของทิศนั้น **ครั้งเดียว**
3. หลังยิงคำสั่ง ต้องคืนมือกลับเข้าใกล้จุดศูนย์กลาง (ภายใน `window_rearm`) ก่อนถึงปัดครั้งถัดไปได้ (กันยิงรัว)
   - ทำให้ปัดลงสองครั้งในโหมดเดียวกันได้: คืนขนาด → ย่อ
   - หลังย่อแล้วให้ปิดโหมดเลย เพราะหน้าต่างเป้าหมายไม่ได้อยู่หน้าสุดแล้ว
4. ออกจากท่า 🤙 หรือมือหาย `0.3 วิ` = ปิดโหมด กลับไปควบคุมเมาส์ (ถ้ายังไม่ได้ปัดเลยก็คือยกเลิก)

### การทำบน Windows
- ส่งคีย์ด้วย SendInput (ต่อยอดจาก `build_alt_sequence` / `_tap_alt` ใน `platform/win32/windows.py`) ลำดับ `Win↓ → ลูกศร↓ → ลูกศร↑ → Win↑` ในการเรียกเดียว
- **ก่อนส่งคีย์ ให้เช็กว่าหน้าต่างเป้าหมายยังเป็น foreground** ถ้าไม่ใช่ ให้ activate ด้วยวิธีเดียวกับ switcher ก่อน (หรือยกเลิก)
- **ห้ามมีปุ่ม Win ค้าง** เพราะจะทำให้ Start menu เด้ง หรือคีย์ถัดไปกลายเป็นคีย์ลัด: ปล่อยใน `finally` และรวมอยู่ในการปล่อยคีย์ทุกกรณีตามหัวข้อ 5
- ทำไมใช้ `Win+ลูกศร` แทน `ShowWindow`: ได้ Snap Assist และพฤติกรรมหลายจอของ Windows มาฟรี และ maximize/restore ทำงานเหมือนกดคีย์เอง

### Feedback
- overlay ลูกศร 4 ทิศ (always-on-top, click-through, ไม่แย่ง focus) ไฮไลต์ทิศที่กำลังจะเลือก และวงความคืบหน้าตอนค้างท่าเปิดโหมด แบบเดียวกับท่าอื่น
- เสียงสั้นตอนยิงคำสั่ง (ใช้เสียงเดียวกับตอนสลับโปรแกรมได้)
- debug view แสดงสถานะ `window` และค่า gap / thumb_out

### Calibrate
- เพิ่มท่า 🤙 ต่อจากท่าสามนิ้วในหน้าต่าง calibrate (ค้าง ~3 วิ) แล้วคำนวณ `window_gap` และ `window_thumb_out` เขียนลง config แบบเดียวกับท่าอื่น

### config.toml
```toml
[window]
enabled = true
hold_ms = 300
gap = 0.15            # curl ก้อย − max(ชี้, กลาง, นาง) ขั้นต่ำ (เริ่มเท่าท่าสามนิ้ว ต้องจูนจาก log)
thumb_out = 0.05      # meters ระยะปลายโป้งถึงโคนนิ้วชี้ขั้นต่ำ (ต้องจูนจาก log)
swipe = 0.6           # ระยะปัดขั้นต่ำ (หน่วยขนาดมือ)
rearm = 0.25          # ต้องคืนมือเข้าใกล้จุดศูนย์กลางเท่านี้ก่อนปัดซ้ำ
```

### ไฟล์ที่เกี่ยวข้อง
- `features.py`: เพิ่มค่าระยะโป้งถึงโคนนิ้วชี้ (ถ้ายังไม่มี)
- `gestures.py`: predicate `is_window_pose` + mode `window` (pure logic) ส่ง event `window_action(direction)`
- `window_model.py` (pure): คำนวณทิศ / ระยะ / rearm จากการเคลื่อนที่ เทสต์ได้โดยไม่มี OS
- `platform/win32/windows.py`: ฟังก์ชัน `send_combo(vk_mod, vk_key)` ที่ปล่อยคีย์เสมอ (ใช้โครงเดียวกับ `_tap_alt`) และฟังก์ชันสร้างลำดับคีย์แบบ pure ไว้เทสต์เหมือน `build_alt_sequence`
- `app.py`: เชื่อม event → ส่งคีย์ และ overlay
- `ui/cursor_overlay.py`: สถานะลูกศร 4 ทิศ
- `ui/calibrate.py`, `calibration.py`: ท่า 🤙
- `tests/test_gestures.py`: ท่า 🤙 ไม่ชนกำมือ / สามนิ้ว / scroll, ทิศถูก, ยิงครั้งเดียวต่อการปัด, rearm, ยกเลิกเมื่อมือหาย
- README: ตารางท่ามือ
