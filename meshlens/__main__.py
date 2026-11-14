"""Entry point.

    python -m meshlens            run the engine and web UI in the console (Ctrl+C to stop)
    python -m meshlens --tray     run as a tray app (normal use)
    python -m meshlens --once     one full router poll, print a summary, exit
"""
from __future__ import annotations

import argparse
import logging
import time

from . import __version__, config, logging_setup
from .engine import Engine
from .web.auth import Auth
from .web.server import run_in_thread


_MUTEX = None


def already_running() -> bool:
    """True if another MeshLens tray app is running for this Windows user (named mutex)."""
    import sys
    global _MUTEX
    if sys.platform != "win32":
        return False
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _MUTEX = k32.CreateMutexW(None, False, "Local\\MeshLensTray")
    return ctypes.get_last_error() == 183      # ERROR_ALREADY_EXISTS


def main() -> None:
    ap = argparse.ArgumentParser(prog="meshlens", description="MeshLens home network diagnostics")
    ap.add_argument("--tray", action="store_true", help="run with a system tray icon")
    ap.add_argument("--once", action="store_true", help="one full router poll, then exit")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args()

    logging_setup.setup(logging.DEBUG if args.debug else logging.INFO, console=not args.tray)
    cfg = config.load()
    if args.tray and already_running():
        import webbrowser
        webbrowser.open(f"http://localhost:{cfg.web.port}/")
        return
    engine = Engine(cfg)

    if args.once:
        st = engine.asus.poll(engine.router_password(), full=True)
        print(st)
        return

    auth = Auth(config.home() / "web_auth.json")
    run_in_thread(engine, auth, cfg.web.bind, cfg.web.port)
    engine.start()
    url = f"http://localhost:{cfg.web.port}/"
    if args.tray:
        from . import tray
        tray.run(engine, url)
    else:
        print(f"MeshLens {__version__} running: {url}  (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            engine.stop()


if __name__ == "__main__":
    main()
