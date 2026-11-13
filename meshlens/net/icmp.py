"""ICMP echo without admin rights.

Windows: the IP Helper API (``IcmpSendEcho``), which ordinary users may call; it also supports a TTL,
which finds the first router beyond the home router (the ISP hop) like the first lines of tracert.
Elsewhere (tests, development): the system ``ping`` command.
"""
from __future__ import annotations

import ctypes
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass
class Reply:
    ok: bool
    rtt_ms: float | None = None
    address: str | None = None     # who answered (the hop, for TTL-limited pings)
    status: int = 0
    ttl_expired: bool = False


if sys.platform == "win32":
    from ctypes import wintypes

    class _IpOptions(ctypes.Structure):
        _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                    ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]

    class _EchoReply(ctypes.Structure):
        _fields_ = [("Address", wintypes.ULONG), ("Status", wintypes.ULONG), ("RoundTripTime", wintypes.ULONG),
                    ("DataSize", wintypes.USHORT), ("Reserved", wintypes.USHORT), ("Data", ctypes.c_void_p),
                    ("Options", _IpOptions)]

    _iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
    _iphlp.IcmpCreateFile.restype = wintypes.HANDLE
    _iphlp.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
    _iphlp.IcmpSendEcho.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.WORD,
                                    ctypes.POINTER(_IpOptions), ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    _iphlp.IcmpSendEcho.restype = wintypes.DWORD
    IP_TTL_EXPIRED_TRANSIT = 11013

    def ping(host: str, timeout_ms: int = 1000, ttl: int | None = None, size: int = 32) -> Reply:
        try:
            ip = socket.gethostbyname(host)
        except OSError:
            return Reply(False, status=-1)
        addr = struct.unpack("=I", socket.inet_aton(ip))[0]
        payload = b"MeshLens" * (size // 8 + 1)
        payload = payload[:size]
        buf_size = ctypes.sizeof(_EchoReply) + size + 8 + 64
        buf = ctypes.create_string_buffer(buf_size)
        opts = _IpOptions(Ttl=ttl or 128)
        h = _iphlp.IcmpCreateFile()
        try:
            t0 = time.perf_counter()
            n = _iphlp.IcmpSendEcho(h, addr, payload, len(payload), ctypes.byref(opts), buf, buf_size, timeout_ms)
            elapsed = (time.perf_counter() - t0) * 1000
        finally:
            _iphlp.IcmpCloseHandle(h)
        rep = _EchoReply.from_buffer(buf)
        who = socket.inet_ntoa(struct.pack("=I", rep.Address)) if rep.Address else None
        if n and rep.Status == 0:
            # perf_counter gives sub-millisecond resolution; the API's own value is whole milliseconds.
            return Reply(True, round(min(elapsed, max(rep.RoundTripTime, 0) + 1.0), 2), who)
        if rep.Status == IP_TTL_EXPIRED_TRANSIT:
            return Reply(False, round(elapsed, 2), who, rep.Status, ttl_expired=True)
        return Reply(False, address=who, status=rep.Status or ctypes.get_last_error())
else:  # pragma: no cover - development on other systems
    def ping(host: str, timeout_ms: int = 1000, ttl: int | None = None, size: int = 32) -> Reply:
        args = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000))] + (["-t", str(ttl)] if ttl else []) + [host]
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=timeout_ms / 1000 + 2).stdout
        except Exception:
            return Reply(False, status=-1)
        if "time=" in out:
            return Reply(True, float(out.split("time=")[1].split()[0]))
        return Reply(False, status=1)


def isp_hop(target: str = "1.1.1.1", router: str | None = None, max_ttl: int = 4) -> tuple[str, int] | None:
    """First router beyond the home router on the way to ``target``: (address, hop number)."""
    for ttl in range(2, max_ttl + 1):
        r = ping(target, timeout_ms=1500, ttl=ttl)
        if r.ttl_expired and r.address and r.address != router:
            return r.address, ttl
        if r.ok:
            return None
    return None


def ping_target(target: str, timeout_ms: int = 1000) -> Reply:
    """Ping a monitor target. ``hop:<ttl>:<dest>`` measures one hop on the way to dest, the way tracert does:
    ISP routers often ignore pings sent to them but do answer "time exceeded" for traffic passing through."""
    if target.startswith("hop:"):
        _, ttl, dest = target.split(":", 2)
        r = ping(dest, timeout_ms, ttl=int(ttl))
        return Reply(r.ttl_expired, r.rtt_ms if r.ttl_expired else None, r.address, r.status)
    return ping(target, timeout_ms)
