"""Build-time helpers: the app icon and the Windows version resource, from meshlens.__version__.

    python packaging/make_assets.py            # uses meshlens.__version__
    python packaging/make_assets.py 0.3.0      # also writes that version into meshlens/__init__.py (release builds)
"""
import pathlib
import re
import sys

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent


def set_version(v: str) -> None:
    p = ROOT / "meshlens" / "__init__.py"
    p.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{v}"', p.read_text(encoding="utf-8")), encoding="utf-8")


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "meshlens" / "__init__.py").read_text(encoding="utf-8")).group(1)


def icon() -> None:
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((16, 16, 240, 240), outline="#2463eb", width=24)
    d.ellipse((88, 88, 168, 168), fill="#2463eb")
    img.save(ROOT / "packaging" / "meshlens.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def version_info(v: str) -> None:
    nums = [int(x) for x in re.findall(r"\d+", v)[:3]] + [0, 0, 0]
    t = tuple(nums[:4])
    (ROOT / "packaging" / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'MeshLens'), StringStruct('FileDescription', 'MeshLens home network diagnostics'),
    StringStruct('FileVersion', '{v}'), StringStruct('ProductName', 'MeshLens'), StringStruct('ProductVersion', '{v}'),
    StringStruct('OriginalFilename', 'MeshLens.exe')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        set_version(sys.argv[1].lstrip("v"))
    v = version()
    icon()
    version_info(v)
    print(v)
