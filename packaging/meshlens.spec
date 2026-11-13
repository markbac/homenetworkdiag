# PyInstaller build for MeshLens (one folder, no console window).
#   .venv\Scripts\pyinstaller packaging\meshlens.spec --noconfirm
# Output: dist\MeshLens\MeshLens.exe
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "meshlens", "web", "static"), os.path.join("meshlens", "web", "static"))]
datas += collect_data_files("devolo_plc_api")
hidden = (collect_submodules("zeroconf") + collect_submodules("devolo_plc_api") + collect_submodules("keyring.backends")
          + ["pystray._win32", "win32timezone"])

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MeshLens",
    console=False,
    icon=os.path.join(ROOT, "packaging", "meshlens.ico"),
    version=os.path.join(ROOT, "packaging", "version_info.txt") if os.path.exists(os.path.join(ROOT, "packaging", "version_info.txt")) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MeshLens")
