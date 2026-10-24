"""
Air Mouse — webcam hand control
- ขยับมือ        : เลื่อน cursor (ใช้ข้อนิ้วโคนเป็นจุดอ้างอิง พับนิ้วแล้ว cursor ไม่กระโดด)
- พับนิ้วชี้ลง    : คลิกซ้าย (พับสองทีเร็วๆ = ดับเบิลคลิก)
- จีบ โป้ง+ชี้ ค้าง : กดเมาส์ค้าง → ลากหน้าต่าง/ไฟล์ได้ ปล่อยจีบ = ปล่อยเมาส์
- กด q หรือ Esc ที่หน้าต่างกล้องเพื่อออก

pip install mediapipe opencv-python pyautogui
"""
import math
import os
import time
import urllib.request

import cv2
import mediapipe as mp
import pyautogui
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False

# ---------------- ค่าที่ปรับได้ (ดูตัวเลขบนจอกล้องแล้วจูน) ----------------
CAM_INDEX = 0
BOX_X = (0.25, 0.75)      # กรอบควบคุมในภาพกล้อง (สัดส่วน) → map เต็มจอ
BOX_Y = (0.20, 0.70)

CURL_DOWN = 0.60          # curl ต่ำกว่านี้ = นิ้วพับ → คลิก
CURL_UP = 0.80            # ต้องเหยียดนิ้วกลับเกินนี้ก่อนคลิกครั้งถัดไป
FREEZE_AT = 0.85          # เริ่มพับนิ้วเมื่อไหร่ cursor หยุดนิ่ง ให้คลิกตรงจุด

PINCH_DOWN = 0.035        # ระยะปลายโป้ง-ชี้ (เมตร) ต่ำกว่านี้ = เริ่มลาก
PINCH_UP = 0.055          # มากกว่านี้ = ปล่อย

SMOOTH_MIN_CUTOFF = 1.0   # ต่ำลง = นิ่งขึ้นตอนมืออยู่กับที่
SMOOTH_BETA = 0.007       # สูงขึ้น = ตามทันขึ้นตอนขยับเร็ว
# -------------------------------------------------------------------------

MODEL = "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")


class OneEuro:
    """One Euro Filter: นิ่งตอนช้า ไวตอนเร็ว"""

    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.x, self.dx, self.t = None, 0.0, None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.x is None:
            self.x, self.t = x, t
            return x
        dt = max(t - self.t, 1e-3)
        self.t = t
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * (x - self.x) / dt + (1 - a_d) * self.dx
        a = self._alpha(self.min_cutoff + self.beta * abs(self.dx), dt)
        self.x = a * x + (1 - a) * self.x
        return self.x


def dist(a, b):
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def curl_ratio(w):
    """1.0 = นิ้วชี้เหยียดตรง, ยิ่งพับยิ่งน้อย (ใช้พิกัด 3D จริง ไม่ขึ้นกับมุมกล้อง)"""
    length = dist(w[5], w[6]) + dist(w[6], w[7]) + dist(w[7], w[8])
    return dist(w[5], w[8]) / length


def clamp01(v):
    return max(0.0, min(1.0, v))


def main():
    if not os.path.exists(MODEL):
        print("Downloading hand model...")
        urllib.request.urlretrieve(MODEL_URL, MODEL)

    options = vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.6,
        min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    landmarker = vision.HandLandmarker.create_from_options(options)

    cap = cv2.VideoCapture(CAM_INDEX)
    sw, sh = pyautogui.size()
    fx = OneEuro(SMOOTH_MIN_CUTOFF, SMOOTH_BETA)
    fy = OneEuro(SMOOTH_MIN_CUTOFF, SMOOTH_BETA)

    dragging = False
    click_armed = True
    curl_frames = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.flip(frame, 1)  # กระจกเงา: ขยับขวา cursor ไปขวา
        h, w_px = frame.shape[:2]
        t = time.monotonic()

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), int(t * 1000))

        if res.hand_landmarks:
            lm = res.hand_landmarks[0]        # พิกัดในภาพ (0-1)
            wl = res.hand_world_landmarks[0]  # พิกัด 3D หน่วยเมตร
            ratio = curl_ratio(wl)
            pinch = dist(wl[4], wl[8])

            # --- จีบ = ลาก ---
            if not dragging and pinch < PINCH_DOWN:
                pyautogui.mouseDown()
                dragging = True
                click_armed = False
            elif dragging and pinch > PINCH_UP:
                pyautogui.mouseUp()
                dragging = False

            # --- พับนิ้วชี้ = คลิก ---
            if ratio > CURL_UP:
                click_armed = True
            if (not dragging and click_armed and pinch > PINCH_UP
                    and ratio < CURL_DOWN):
                curl_frames += 1
                if curl_frames >= 2:          # กันสัญญาณหลอกเฟรมเดียว
                    pyautogui.click()
                    click_armed = False
                    curl_frames = 0
            else:
                curl_frames = 0

            # --- เลื่อน cursor จากข้อนิ้วโคน (5,9,13,17) ---
            ax = sum(lm[i].x for i in (5, 9, 13, 17)) / 4
            ay = sum(lm[i].y for i in (5, 9, 13, 17)) / 4
            nx = clamp01((ax - BOX_X[0]) / (BOX_X[1] - BOX_X[0]))
            ny = clamp01((ay - BOX_Y[0]) / (BOX_Y[1] - BOX_Y[0]))
            sx = fx(nx * (sw - 1), t)
            sy = fy(ny * (sh - 1), t)

            frozen = not dragging and ratio < FREEZE_AT
            if not frozen:
                pyautogui.moveTo(int(sx), int(sy))

            # --- แสดงผล debug ---
            for i, color in ((4, (255, 120, 0)), (8, (0, 200, 255))):
                cv2.circle(frame, (int(lm[i].x * w_px), int(lm[i].y * h)), 8, color, -1)
            cv2.circle(frame, (int(ax * w_px), int(ay * h)), 10, (0, 255, 0), 2)
            state = "DRAG" if dragging else ("FROZEN" if frozen else "MOVE")
            cv2.putText(frame, f"curl {ratio:.2f}  pinch {pinch * 100:.1f}cm  {state}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        else:
            if dragging:
                pyautogui.mouseUp()
                dragging = False
            fx.reset()
            fy.reset()

        cv2.rectangle(frame,
                      (int(BOX_X[0] * w_px), int(BOX_Y[0] * h)),
                      (int(BOX_X[1] * w_px), int(BOX_Y[1] * h)),
                      (255, 0, 255), 2)
        cv2.imshow("Air Mouse (q to quit)", frame)
        if cv2.waitKey(1) & 0xFF in (27, ord("q")):
            break

    if dragging:
        pyautogui.mouseUp()
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
