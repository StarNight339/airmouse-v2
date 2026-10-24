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


def curl_ratio(world: Sequence[Point], joints: tuple[int, int, int, int]) -> float:
    a, b, c, d = (world[i] for i in joints)
    length = math.dist(a, b) + math.dist(b, c) + math.dist(c, d)
    return math.dist(a, d) / length if length > 0 else 0.0


def compute(image: Sequence[Point], world: Sequence[Point], aspect: float) -> HandFeatures:
    """aspect = ความกว้าง/ความสูงของภาพ ใช้แก้พิกัด x,y ที่ normalize คนละสเกล"""
    curl = {name: curl_ratio(world, joints) for name, joints in FINGERS.items()}
    pinch = math.dist(world[THUMB_TIP], world[INDEX_TIP])
    w, m = image[WRIST], image[MIDDLE_MCP]
    hand_size = math.hypot((w[0] - m[0]) * aspect, w[1] - m[1])
    ax = sum(image[i][0] for i in ANCHOR_IDS) / len(ANCHOR_IDS)
    ay = sum(image[i][1] for i in ANCHOR_IDS) / len(ANCHOR_IDS)
    return HandFeatures(curl=curl, pinch=pinch, hand_size=hand_size, anchor=(ax, ay))
