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
| มือหายจากกล้อง | พัก → ประหยัดพลังงาน → ปิดโปรแกรมเมื่อครบ 10 นาที |

### โหมด Swipe Switcher (ท่ามือมีความหมายเฉพาะตอนเปิด switcher)

| ท่ามือ | ฟังก์ชัน |
|---|---|
| สามนิ้วค้าง + ขยับซ้าย/ขวา | เลื่อนการ์ดโปรแกรม |
| ปัดขึ้นเร็วๆ | ย่อหน้าต่างของการ์ดที่เลือก (minimize) |
| หุบนิ้ว (กลับเป็นท่าปกติหรือกำมือ) | สลับไปโปรแกรมที่เลือกแล้วปิด switcher |
| แบมือ / ปัดลง / มือหาย | ยกเลิก กลับไปหน้าต่างเดิม |

---

## 2. หลักการตรวจจับท่ามือ (ต้องทำตาม)

1. **วัดทุกท่าด้วย `hand_world_landmarks` (3D หน่วยเมตร)** เพื่อไม่ให้ค่าเปลี่ยนตามมุมกล้องหรือระยะห่างของมือ
   - curl ของนิ้ว: `dist(mcp, tip) / (dist(mcp,pip)+dist(pip,dip)+dist(dip,tip))` นิ้วตรงได้ประมาณ 1.0 ยิ่งพับยิ่งน้อย (คิดแยกทุกนิ้ว)
   - pinch: `dist(4, 8)` หน่วยเมตร
   - กำมือ: นิ้วชี้ กลาง นาง ก้อย พับทั้งหมด
2. **cursor อิงค่าเฉลี่ยข้อนิ้วโคน (landmark 5, 9, 13, 17)** ไม่ใช่ปลายนิ้ว เวลาพับนิ้วเพื่อคลิก cursor จะได้ไม่เลื่อน
3. **Hysteresis ทุกท่า** threshold กดกับปล่อยแยกกัน และต้องค้างอย่างน้อย `confirm_frames` เฟรมถึงจะนับ
4. **ลำดับความสำคัญเมื่อท่าชนกัน:** switcher > drag (pinch) > clutch (กำมือ) > scroll > คลิก
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

---

## 10. ไว้ทำทีหลัง
- จีบแล้วหมุนข้อมือเพื่อปรับเสียง / ควบคุม media
- Dwell click (ค้าง cursor นิ่งแล้วคลิกเอง)
- ท่าทางกำหนดเองด้วย classifier เล็กๆ (input เป็น landmark 63 ค่า) แล้วผูกกับคีย์ลัด
- ปัดสี่นิ้วเพื่อสลับ virtual desktop
