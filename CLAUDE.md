# Air Mouse v2: บริบทสำหรับ Claude

ควบคุมเมาส์ด้วยมือผ่านเว็บแคม (MediaPipe Hand Landmarker) บน Windows 10/11
แผนและข้อกำหนดทั้งหมดอยู่ใน `PLAN.md` (ภาษาไทย) ส่วนวิธีรันอยู่ใน `README.md`

## สถานะ
- **Phase 1–3:** เสร็จและทดสอบสดแล้ว (tracking, cursor, คลิก/ลาก/scroll)
- **Phase 4 (lifecycle, low-power, auto-exit, tray, overlay, เสียง):** เขียนเสร็จ (`91cd885`) แต่ยังรอทดสอบสด
- **Phase 4.5 snap (cursor เกาะปุ่มด้วย UI Automation + แม่เหล็ก/หนืด):** เขียนเสร็จแล้ว (`snap.py`, `platform/win32/targets.py`, `[snap]` ใน config, เมนู tray "Snap เกาะปุ่ม")
  - รอทดสอบสด: ปุ่มย่อ/ปิดหน้าต่าง, แท็บ และปุ่มในโปรแกรม
  - Opus ตรวจรอบแรกยังไม่มีผลก่อน clear context ต้องสั่งตรวจใหม่
  - เจอแล้ว: NVIDIA Overlay และแอป Dynamic Island บัง UIA `ElementFromPoint` จึงต้องเช็กว่าปุ่มอยู่ในหน้าต่างจริงที่ `WindowFromPoint` คืนมา ทำไว้แล้ว
  - คอมมิตยังไม่ได้ push: Phase 4 ทั้งหมดและ snap จะ push หลังทดสอบสดผ่าน
- **branch `custom-ui` (worktree `..\airmouse-v2-ui`):** อีกเซสชันใช้คุยเรื่องการปรับแต่งและ custom UI
- **Phase 5 (Swipe Switcher) และ Phase 6 (calibrate, โหมดฝึก, polish):** ยังไม่เริ่ม

## ผู้ใช้
- คุยภาษาไทย ใช้**มือซ้าย** (`config.toml`: `hand = "left"`, `normal_facing = -1`)
- กล้องได้ ~10–16 FPS เพราะห้องค่อนข้างมืด

## โครงสร้างหลัก (`src/airmouse/`)
- **`app.py`:**
  - `Controller` (ท่ามือ → motion → mouse)
  - `InferenceThread` (กล้อง → MediaPipe → features → Controller → Lifecycle)
  - `_AppUI` (Qt main thread: tray, overlay, เสียง) และ `PoseRecorder`
- **`gestures.py`:** `GestureTracker` เป็น pure state machine
  - predicate `is_*` / `raw_pose` ใช้ร่วมกับ debug view
  - mode: off / move / clutch / drag / scroll / flip
- **`motion.py`:** pre-filter + leash deadzone + กราฟความไว, scroll แบบจอยสติ๊ก, ประวัติตำแหน่งสำหรับย้อนคลิก
- **`features.py`:** curl, pinch, spread, palm_facing, anchor
- **`lifecycle.py`:** สถานะ active / paused / low_power / exit
- **`platform/win32/`:** mouse (SendInput + MouseThread + Raw Input), system (DPI, timer, console handler)
- **`ui/`:** debug_view, cursor_overlay, tray
- **อื่นๆ:** `sound.py`, `tools/replay_pose.py` (replay `logs/*-pose.jsonl` จาก `--log-pose`)
- ไฟล์ pure (`gestures`, `motion`, `features`, `lifecycle`, `snap`, `filters`) ห้าม import อะไรที่ผูกกับ OS

## วิธีทำงานที่ตกลงกันไว้
- **ลำดับงานแต่ละ phase:**
  1. วางแผนใน plan mode
  2. ส่งให้ Sonnet coder 2 ตัวทำพร้อมกัน แต่ละตัวแก้คนละไฟล์
  3. Opus ตรวจความถูกต้อง
  4. ตัวหลักรีวิวซ้ำและเชื่อมต่อ
  5. ทดสอบสดกับผู้ใช้ด้วย `--debug --log-pose` แล้วอ่าน log มาจูน
  6. commit เป็น checkpoint ทุกขั้น และ push ขึ้น GitHub เมื่อผ่านการทดสอบสด
- **จูนจากข้อมูลจริง:** ค่าเกณฑ์ทุกตัวใน `config.toml` มาจาก log มือจริงของผู้ใช้ ก่อนเปลี่ยนให้ดู log หรือ replay ก่อน
- **รันทดสอบ:**
  - เทสต์: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (ต้องไม่เปิดกล้อง หน้าต่าง หรือส่ง input จริง)
  - ทดสอบสด: เปิดหน้าต่าง PowerShell แยกด้วย `.venv\Scripts\python.exe -m airmouse --debug --log-pose` (ใส่ `--debug-view` ถ้าอยากดูภาพกล้อง)
- **สไตล์โค้ด:** comment ภาษาไทยในปริมาณพอดี, type hints, `from __future__ import annotations`
- **ระวังเรื่องไฟล์:** แก้ไฟล์ด้วย Edit/Write ไม่ใช้ heredoc เพราะเคยทำให้มี NUL byte และ `\n` เพี้ยน และไฟล์ใน repo ผสม CRLF/LF อยู่
- **commit message:** ลงท้ายด้วย `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
