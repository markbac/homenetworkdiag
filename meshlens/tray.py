"""System tray icon (D-02, UI-06): Open dashboard, Collect now, Pause, Quit."""
from __future__ import annotations

import webbrowser

import pystray
from PIL import Image, ImageDraw

from .engine import Engine


def _icon(colour: str = "#2463eb") -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), outline=colour, width=6)
    d.ellipse((22, 22, 42, 42), fill=colour)
    return img


def run(engine: Engine, url: str) -> None:
    def open_dashboard(icon, item):
        webbrowser.open(url)

    def collect(icon, item):
        engine.collect_now(full=True)

    def toggle_pause(icon, item):
        engine.paused = not engine.paused
        icon.icon = _icon("#888888" if engine.paused else "#2463eb")

    def quit_(icon, item):
        engine.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", open_dashboard, default=True),
        pystray.MenuItem("Collect now", collect),
        pystray.MenuItem("Pause collectors", toggle_pause, checked=lambda item: engine.paused),
        pystray.MenuItem("Quit", quit_),
    )
    pystray.Icon("MeshLens", _icon(), "MeshLens", menu).run()
