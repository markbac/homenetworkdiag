"""System tray icon (D-02, UI-06).

Menu: Open dashboard, Collect now, Run speed test, Pause, Start with Windows, Quit.
The icon colour shows the network's state: blue = no data yet, green = fine, amber = medium issues,
red = high-severity issues, grey = paused.
"""
from __future__ import annotations

import sys
import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw

from .engine import Engine

COLOURS = {"none": "#2463eb", "ok": "#1a8f4a", "medium": "#c27a00", "high": "#c4322b", "paused": "#888888"}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "MeshLens"


def _icon(colour: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), outline=colour, width=6)
    d.ellipse((22, 22, 42, 42), fill=colour)
    return img


def startup_command() -> str:
    """What Windows runs at sign-in: the installed exe, or pythonw for a development install."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" -m meshlens --tray'


def start_with_windows() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_start_with_windows(on: bool) -> None:
    """Per-user sign-in entry (HKCU Run), the same one the installer's option creates."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


def state(engine: Engine) -> tuple[str, str]:
    if engine.paused:
        return "paused", "MeshLens: paused"
    try:
        a = engine.analysis(24)
    except Exception:
        return "none", "MeshLens"
    n = a["network"]
    if n["score"] is None:
        return "none", "MeshLens: waiting for data"
    c = n["issue_counts"]
    key = "high" if c["high"] else "medium" if c["medium"] else "ok"
    return key, f"MeshLens: health {n['score']}/100, {c['high']} high / {c['medium']} medium issues"


def run(engine: Engine, url: str) -> None:
    stop = threading.Event()

    def refresh(icon) -> None:
        key, tip = state(engine)
        icon.icon = _icon(COLOURS[key])
        icon.title = tip[:120]

    def open_dashboard(icon, item):
        webbrowser.open(url)

    def collect(icon, item):
        engine.collect_now(full=True)

    def speed(icon, item):
        threading.Thread(target=engine.run_speedtest, args=("tray",), daemon=True).start()

    def toggle_pause(icon, item):
        engine.paused = not engine.paused
        engine.monitor.paused = engine.paused
        refresh(icon)

    def toggle_startup(icon, item):
        set_start_with_windows(not start_with_windows())

    def quit_(icon, item):
        stop.set()
        engine.stop()
        icon.stop()

    def updater(icon) -> None:
        icon.visible = True
        while not stop.wait(60):
            try:
                refresh(icon)
            except Exception:
                pass

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", open_dashboard, default=True),
        pystray.MenuItem("Collect now", collect),
        pystray.MenuItem("Run speed test", speed),
        pystray.MenuItem("Pause collectors", toggle_pause, checked=lambda item: engine.paused),
        pystray.MenuItem("Start with Windows", toggle_startup, checked=lambda item: start_with_windows(),
                         visible=sys.platform == "win32"),
        pystray.MenuItem("Quit", quit_),
    )
    icon = pystray.Icon("MeshLens", _icon(COLOURS["none"]), "MeshLens", menu)
    icon.run(setup=updater)
