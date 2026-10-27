# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec (one-folder, ไม่มี console เพราะเป็นแอป tray)
# build: tools\build_exe.ps1  (config.toml, assets/, models/ ถูก copy ไปข้าง exe โดยสคริปต์นั้น)
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = []

# mediapipe: ไฟล์ .binarypb/.tflite และ DLL ที่ hook ปกติอาจพลาด
datas += collect_data_files("mediapipe")
binaries += collect_dynamic_libs("mediapipe")
# cv2: config + DLL (hook ของ pyinstaller จัดการส่วนใหญ่ แต่เก็บซ้ำกันพลาด)
datas += collect_data_files("cv2")
binaries += collect_dynamic_libs("cv2")

# comtypes: targets.py เรียก comtypes.client.GetModule("UIAutomationCore.dll") ตอนรัน
# ตอน frozen comtypes จะสร้าง wrapper ใน comtypes.gen แบบ in-memory/ชั่วคราว (ดูหมายเหตุท้ายไฟล์)
# จึงต้องมีตัว package และ module ที่มันใช้ภายใน
hiddenimports += collect_submodules(
    "comtypes", filter=lambda n: not n.startswith(("comtypes.test", "comtypes.gen.")))
hiddenimports += ["comtypes.gen", "comtypes.client", "comtypes.automation", "comtypes.persist"]

# ตัด Qt module ที่ไม่ใช้ (โปรเจกต์ใช้แค่ QtCore/QtGui/QtWidgets)
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.QtQml", "PySide6.QtDesigner", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    # ห้ามตัด QtMultimedia: sound.py ใช้ QSoundEffect
    "PySide6.QtMultimediaWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSvgWidgets", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtNetworkAuth", "PySide6.QtSpatialAudio",
    # ห้ามตัด matplotlib: mediapipe.tasks.python.vision.drawing_utils import ตอนโหลด mediapipe
    # (ตัดแล้ว exe พังตั้งแต่ import: ModuleNotFoundError ขึ้นเป็น dialog ตอน console=False)
    "tkinter", "pytest", "IPython", "notebook", "scipy.tests",
]

a = Analysis(
    [str(ROOT / "tools" / "exe_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

_icons = sorted((ROOT / "assets").glob("*.ico"))
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="airmouse",
    console=False,
    icon=str(_icons[0]) if _icons else None,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="airmouse",
)

# หมายเหตุ
# - ไม่ bundle config.toml / models / assets ใน _internal: config.PROJECT_ROOT ตอน frozen
#   คือโฟลเดอร์ของ exe ดังนั้น build_exe.ps1 copy config.toml, assets\, models\ ไปไว้ข้าง exe
#   (models\hand_landmarker.task ถ้าไม่มีจะถูกดาวน์โหลดเองตอนรันครั้งแรก)
# - comtypes ตอน frozen: comtypes.client._code_cache ใช้ comtypes.gen ที่ผูกกับ sys.frozen
#   ถ้า comtypes.gen ใน _internal เขียนไม่ได้ จะ fallback ไป %TEMP%\comtypes_cache\airmouse-<ver> (เขียนได้เสมอ)
#   ไม่ต้อง pre-generate; GetModule("UIAutomationCore.dll") สร้าง wrapper ครั้งแรกที่รันช้าเล็กน้อย
