"""คำนวณค่าของมือจาก landmark (pure logic ไม่แตะ OS หรือ mediapipe)

- curl / pinch ใช้ world landmarks (3D หน่วยเมตร) เพื่อไม่ให้ค่าเปลี่ยนตามมุมกล้องหรือระยะห่าง
- hand_size / anchor ใช้ image landmarks เพราะใช้กับการขยับ cursor ในภาพ
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

Point = tuple[float, float, float]

# ข้อต่อ 4 จุดของแต่ละนิ้ว (โคน → ปลาย) นิ้วโป้งใช้ CMC เป็นจุดเริ่มเพื่อให้ได้ 3 ท่อนเหมือนนิ้วอื่น
FINGERS: dict[str, tuple[int, int, int, int]] = {
    "thumb": (1, 2, 3, 4),
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}
ANCHOR_IDS = (5, 9, 13, 17)  # ข้อนิ้วโคน: พับนิ้วคลิกแล้ว cursor ไม่เลื่อน
THUMB_TIP, INDEX_TIP = 4, 8
WRIST, MIDDLE_MCP = 0, 9


@dataclass(frozen=True)
class HandFeatures:
    curl: dict[str, float]      # 1.0 = เหยียดตรง ยิ่งพับยิ่งน้อย
    pinch: float                # ระยะปลายโป้ง-ปลายชี้ (เมตร)
    hand_size: float            # ระยะข้อมือ→โคนนิ้วกลางในภาพ (สัดส่วนความสูงภาพ)
    anchor: tuple[float, float]  # ค่าเฉลี่ยข้อนิ้วโคนในภาพ (0–1)
    spread: float = 1.0         # ระยะปลายนิ้วที่อยู่ติดกัน (เฉลี่ย) / ความกว้างฝ่ามือ
    palm_facing: float = 1.0    # [-1, 1] บวก = ฝ่ามือหันเข้าหากล้อง


def curl_ratio(world: Sequence[Point], joints: tuple[int, int, int, int]) -> float:
    a, b, c, d = (world[i] for i in joints)
    length = math.dist(a, b) + math.dist(b, c) + math.dist(c, d)
    return math.dist(a, d) / length if length > 0 else 0.0


def spread_ratio(world: Sequence[Point]) -> float:
    palm = math.dist(world[5], world[17])
    if palm <= 0:
        return 0.0
    gaps = (math.dist(world[8], world[12]), math.dist(world[12], world[16]),
            math.dist(world[16], world[20]))
    return sum(gaps) / len(gaps) / palm


def palm_facing_score(image: Sequence[Point], aspect: float, handedness: str, mirrored: bool) -> float:
    """cross product ของ wrist→index MCP กับ wrist→pinky MCP (y ชี้ลง)
    ภาพ mirror + มือขวา + ฝ่ามือเข้ากล้อง ⇒ c > 0 ; มือซ้ายหรือภาพไม่ mirror กลับเครื่องหมาย"""
    w = image[WRIST]
    v1 = ((image[5][0] - w[0]) * aspect, image[5][1] - w[1])
    v2 = ((image[17][0] - w[0]) * aspect, image[17][1] - w[1])
    n = math.hypot(*v1) * math.hypot(*v2)
    if n <= 0:
        return 0.0
    c = (v1[0] * v2[1] - v1[1] * v2[0]) / n
    return c * (1 if handedness == "right" else -1) * (1 if mirrored else -1)


def compute(image: Sequence[Point], world: Sequence[Point], aspect: float,
            handedness: str = "right", mirrored: bool = True) -> HandFeatures:
    """aspect = ความกว้าง/ความสูงของภาพ ใช้แก้พิกัด x,y ที่ normalize คนละสเกล"""
    curl = {name: curl_ratio(world, joints) for name, joints in FINGERS.items()}
    pinch = math.dist(world[THUMB_TIP], world[INDEX_TIP])
    w, m = image[WRIST], image[MIDDLE_MCP]
    hand_size = math.hypot((w[0] - m[0]) * aspect, w[1] - m[1])
    ax = sum(image[i][0] for i in ANCHOR_IDS) / len(ANCHOR_IDS)
    ay = sum(image[i][1] for i in ANCHOR_IDS) / len(ANCHOR_IDS)
    return HandFeatures(
        curl=curl, pinch=pinch, hand_size=hand_size, anchor=(ax, ay),
        spread=spread_ratio(world),
        palm_facing=palm_facing_score(image, aspect, handedness, mirrored),
    )
