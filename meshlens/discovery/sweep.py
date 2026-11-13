"""Find devices on the configured subnets (DEV-01) and their names (SVC-03).

Paced ping sweep (no admin needed) which also fills the Windows neighbour (ARP) table; the ARP table
then gives each address's MAC, including devices that ignore ping. Names from reverse DNS and
NetBIOS node-status queries. Nothing is sent outside the configured subnets.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from ..net import icmp

_ARP_LINE = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\s+(\w+)", re.M)


def addresses(subnets: Iterable[str], exclude: Iterable[str] = ()) -> list[str]:
    """Host addresses to sweep (network/broadcast and excluded ranges removed)."""
    ex = [ipaddress.ip_network(e, strict=False) for e in exclude]
    out: list[str] = []
    for s in subnets:
        net = ipaddress.ip_network(s, strict=False)
        if net.version != 4 or net.num_addresses > 4096:
            continue  # refuse anything bigger than a /20 by accident
        for ip in net.hosts():
            if not any(ip in e for e in ex):
                out.append(str(ip))
    return out


def parse_arp(text: str) -> dict[str, str]:
    """``arp -a`` output -> {ip: mac} (dynamic and static, broadcast/multicast removed)."""
    out = {}
    for ip, mac, _kind in _ARP_LINE.findall(text):
        mac = mac.replace("-", ":").lower()
        if mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00") or mac.startswith("01:00:5e"):
            continue
        out[ip] = mac
    return out


def arp_table() -> dict[str, str]:
    cmd = ["arp", "-a"] if sys.platform == "win32" else ["ip", "neigh"]
    try:
        text = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return {}
    if sys.platform != "win32":  # "10.0.0.1 dev eth0 lladdr aa:bb:.. REACHABLE"
        return {m[0]: m[1].lower() for m in re.findall(r"^(\S+) .*lladdr (\S+)", text, re.M)}
    return parse_arp(text)


def sweep(ips: list[str], rate: int = 200, pinger: Callable = icmp.ping) -> dict[str, float | None]:
    """Ping every address at most ``rate`` per second. Returns {ip: rtt or None}."""
    results: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=min(64, max(4, rate // 4))) as pool:
        futs = {}
        for i, ip in enumerate(ips):
            futs[ip] = pool.submit(pinger, ip, 500)
            if (i + 1) % rate == 0:
                time.sleep(1.0)
        for ip, f in futs.items():
            try:
                r = f.result()
                results[ip] = r.rtt_ms if r.ok else None
            except Exception:
                results[ip] = None
    return results


def reverse_dns(ip: str) -> str | None:
    try:
        name = socket.gethostbyaddr(ip)[0]
    except OSError:
        return None
    return None if name == ip else name.rstrip(".")


def reverse_dns_many(ips: list[str]) -> dict[str, str | None]:
    """Reverse lookups in parallel (each can take seconds when there is no PTR record)."""
    with ThreadPoolExecutor(max_workers=32) as pool:
        return dict(zip(ips, pool.map(reverse_dns, ips)))


# ---------------------------------------------------------------- NetBIOS node status (UDP 137)

def _nbstat_query(tid: int) -> bytes:
    name = b"*" + b"\0" * 15
    enc = bytes(b for c in name for b in (0x41 + (c >> 4), 0x41 + (c & 0x0F)))
    return struct.pack(">HHHHHH", tid, 0, 1, 0, 0, 0) + b"\x20" + enc + b"\x00" + struct.pack(">HH", 0x21, 1)


def parse_nbstat(data: bytes) -> dict[str, str | None]:
    """Node status response -> computer name and workgroup."""
    try:
        off = 12
        off += data[off] + 2           # name length byte + name + terminator
        off += 2 + 2 + 4               # type, class, ttl
        off += 2                       # rdlength
        count = data[off]
        off += 1
        computer = group = None
        for _ in range(count):
            raw, suffix, flags = data[off:off + 15], data[off + 15], struct.unpack(">H", data[off + 16:off + 18])[0]
            off += 18
            name = raw.decode("ascii", "replace").strip()
            is_group = bool(flags & 0x8000)
            if suffix == 0x00 and not is_group and not computer:
                computer = name
            elif suffix == 0x00 and is_group and not group:
                group = name
        return {"netbios": computer, "workgroup": group}
    except (IndexError, struct.error):
        return {"netbios": None, "workgroup": None}


def netbios_names(ips: list[str], timeout: float = 1.5) -> dict[str, dict[str, str | None]]:
    """Ask every address at once over one socket; Windows PCs, NAS boxes and Samba answer."""
    out: dict[str, dict[str, str | None]] = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.2)
    try:
        for i, ip in enumerate(ips):
            try:
                s.sendto(_nbstat_query(0x4D4C + i % 1000), (ip, 137))
            except OSError:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, (ip, _port) = s.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            r = parse_nbstat(data)
            if r["netbios"]:
                out[ip] = r
    finally:
        s.close()
    return out
