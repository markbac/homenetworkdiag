"""In-app updates from the project's GitHub releases.

- ``check()`` asks the GitHub API for the latest release (no data about the network is sent; GitHub sees
  the PC's public address, as with any web request).
- ``download()`` fetches the setup exe from that release and verifies it against the release's
  ``SHA256SUMS.txt``; only github.com release URLs are accepted.
- ``launch_installer()`` runs it silently with elevation (one UAC prompt). The installer closes
  MeshLens, replaces the program files and starts it again; data and passwords are untouched.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import __version__

log = logging.getLogger("meshlens.updater")

REPO = "markbac/homenetworkdiag"
API = f"https://api.github.com/repos/{REPO}/releases"
ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
INSTALLER_ARGS = "/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /SP-"


def parse_version(v: str) -> tuple[tuple[int, ...], bool]:
    """'v0.2.1' -> ((0, 2, 1), False); '0.3.0-dev.4' -> ((0, 3, 0), True) (pre-release)."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(.*)", (v or "").strip())
    if not m:
        return (0,), True
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums = nums + (0,) * (3 - len(nums))
    return nums, bool(m.group(2).strip())


def is_newer(candidate: str, current: str = __version__) -> bool:
    c, c_pre = parse_version(candidate)
    cur, cur_pre = parse_version(current)
    return c > cur or (c == cur and cur_pre and not c_pre)


def _get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"MeshLens/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _allowed(url: str) -> bool:
    u = urlparse(url)
    return u.scheme == "https" and u.hostname in ALLOWED_HOSTS


def check(fetch: Callable[[str], bytes] = _get, include_prereleases: bool = False,
          current: str = __version__) -> dict[str, Any]:
    """Latest release info and whether it is newer than this version."""
    if include_prereleases:
        releases = json.loads(fetch(f"{API}?per_page=10"))
        rel = next((r for r in releases if not r.get("draft")), None)
    else:
        rel = json.loads(fetch(f"{API}/latest"))
    if not rel:
        return {"available": False, "current": current, "latest": None}
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets") or []}
    setup = next((n for n in assets if n.lower().endswith("-setup.exe")), None)
    latest = rel.get("tag_name", "").lstrip("v")
    return {
        "available": bool(setup) and is_newer(latest, current),
        "current": current, "latest": latest, "name": rel.get("name") or latest,
        "notes": (rel.get("body") or "")[:4000], "url": rel.get("html_url"), "published": rel.get("published_at"),
        "installer": setup, "installer_url": assets.get(setup) if setup else None,
        "sums_url": assets.get("SHA256SUMS.txt"),
    }


def download(info: dict[str, Any], dest_dir: Path, fetch: Callable[[str], bytes] = _get) -> Path:
    """Download and verify the installer. Raises ValueError if anything doesn't check out."""
    url, sums_url, name = info.get("installer_url"), info.get("sums_url"), info.get("installer")
    if not url or not name or not _allowed(url):
        raise ValueError("the release has no installer at a GitHub address")
    if not sums_url or not _allowed(sums_url):
        raise ValueError("the release has no SHA256SUMS.txt, so the download cannot be verified")
    sums = {}
    for line in fetch(sums_url).decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[-1].lstrip("*")] = parts[0].lower()
    if name not in sums:
        raise ValueError(f"{name} is not listed in SHA256SUMS.txt")
    data = fetch(url)
    digest = hashlib.sha256(data).hexdigest()
    if digest != sums[name]:
        raise ValueError("the download does not match its published checksum; not installing it")
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob("MeshLens-*-setup.exe"):
        try:
            old.unlink()
        except OSError:
            pass
    path = dest_dir / Path(name).name
    path.write_bytes(data)
    return path


def can_self_update() -> bool:
    """Only the installed app updates itself; a source checkout updates with git."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _shell_execute(path: Path, args: str) -> int:
    """ShellExecuteEx with "open": Windows shows the UAC prompt for the installer itself, and Setup keeps the
    signed-in user as its "original user", so it can start MeshLens again as that user afterwards.
    Returns the installer's process id."""
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    SEE_MASK_NOCLOSEPROCESS = 0x40
    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=SEE_MASK_NOCLOSEPROCESS, lpVerb="open",
                             lpFile=str(path), lpParameters=args, nShow=1)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        raise RuntimeError("the update was cancelled at the Windows permission prompt" if err == 1223 else
                           f"could not start the installer (error {err})")
    pid = ctypes.windll.kernel32.GetProcessId(info.hProcess)
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return pid


def relaunch_watcher(installer_pid: int, exe: str) -> None:
    """Backstop: a hidden helper waits for Setup to finish, then starts MeshLens unless it is already running.
    Started detached so it survives MeshLens being closed by the installer."""
    import subprocess
    exe_ps = exe.replace("'", "''")
    script = (f"Wait-Process -Id {int(installer_pid)} -ErrorAction SilentlyContinue; Start-Sleep -Seconds 4; "
              f"if (-not (Get-Process -Name MeshLens -ErrorAction SilentlyContinue)) "
              f"{{ Start-Process -FilePath '{exe_ps}' -ArgumentList '--tray' }}")
    flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                     creationflags=flags, close_fds=True)


def launch_installer(path: Path) -> None:
    """Start the installer (Windows asks for permission) and make sure MeshLens comes back afterwards."""
    pid = _shell_execute(path, INSTALLER_ARGS)
    try:
        relaunch_watcher(pid, sys.executable)
    except OSError as e:
        log.warning("could not start the restart helper: %s", e)
